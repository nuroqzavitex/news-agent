"""Spec §9, one test per row of the fault table, driven through the same fault switches as the dashboard's fault lab.

Rows tested elsewhere are referenced rather than repeated:
- processor crash mid-way: tests/unit/test_streams.py::test_pending_from_dead_consumer_is_reclaimed
- broker down: not applicable while the broker adapter is direct HTTP (the Team 2 adapter does not exist yet)
- api replica dies: stretch goal (spec §2.2)
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select, update

from app.adapters.local.identity import LocalIdentity
from app.contracts import Envelope
from app.db.models import Source, TraceEvent
from tests.fake_gemini import FakeGemini
from tests.integration.conftest import ME, SECRET, Network

pytestmark = pytest.mark.integration


async def admin_headers(network: Network, password: str = "change-me") -> dict[str, str]:
    resp = await network.api.post("/api/v1/admin/login", json={"password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['token']}"}


async def set_faults(network: Network, **faults: Any) -> None:
    resp = await network.api.put("/api/v1/admin/faults", json=faults, headers=await admin_headers(network))
    assert resp.status_code == 200, resp.text


async def send(network: Network, *, token: str | None = None, sender: str = "tester", **kw: Any) -> httpx.Response:
    env = Envelope(
        from_=sender, to=ME, capability="news_search", payload={"query": "TCB"},
        token=token or await LocalIdentity(SECRET).issue_token(sender), **kw,
    )
    return await network.api.post("/messages", json=env.wire())


async def health(network: Network) -> dict[str, Any]:
    return (await network.api.get("/health")).json()


def step(result: dict[str, Any], capability: str) -> dict[str, Any]:
    return next(s for s in result["steps"] if s["capability"] == capability)


async def test_duplicate_message_is_answered_from_the_stored_reply(network):
    first = await send(network, message_id="m-dup-9")
    second = await send(network, message_id="m-dup-9")
    assert first.json() == second.json()
    async with network.state.sessions() as s:
        events = (await s.scalars(select(TraceEvent.event).where(TraceEvent.message_id == "m-dup-9"))).all()
    assert events.count("msg.replied") == 1 and events.count("msg.duplicate") == 1


async def test_target_not_answering_is_retried_then_skipped(network):
    network.mock("mock-finance-a").fault.delay_s = 5
    result = await network.ask()
    assert step(result, "stock_price")["agents"] == ["mock-finance-b"]
    names = [e["event"] for e in await network.events(result["trace_id"])]
    assert names.count("broker.retry") == 1


async def test_target_down_fails_over(network):
    for name in ("mock-finance-a", "mock-finance-b"):
        network.mock(name).fault.fail_status = 503
    result = await network.ask()
    assert step(result, "stock_price")["agents"] == ["mock-finance-c"]
    assert "client.failover" in [e["event"] for e in await network.events(result["trace_id"])]


async def test_no_agent_left_gives_a_partial_answer(network):
    for name in ("mock-finance-a", "mock-finance-b", "mock-finance-c"):
        network.mock(name).fault.fail_status = 503
    result = await network.ask()
    assert result["partial"] is True and result["missing"] == ["stock_price"]


async def test_registry_down_uses_the_agent_cache(network):
    await network.ask()  # learn the agents while the registry works
    await network.state.redis.delete("cache:cap:stock_price")
    await set_faults(network, registry_down=True)

    assert (await health(network))["deps"]["registry"] == "down"
    result = await network.ask("Giá cổ phiếu VCB?")
    assert step(result, "stock_price")["status"] == "ok"
    assert "registry.fallback_cache" in [e["event"] for e in await network.events(result["trace_id"])]
    graph = (await network.api.get("/api/v1/network")).json()
    assert graph["registry_ok"] is False and {n["source"] for n in graph["nodes"]} == {"self", "cache"}


async def test_identity_down_accepts_recently_validated_tokens_only(network):
    known = await LocalIdentity(SECRET).issue_token("tester")
    assert (await send(network, token=known)).status_code == 200
    await set_faults(network, identity_down=True)

    assert (await send(network, token=known)).status_code == 200  # validated in the last 60 seconds
    unknown = await LocalIdentity(SECRET).issue_token("tester")
    unknown = LocalIdentity(SECRET).sign("tester", time.time() + 120) if unknown == known else unknown
    assert (await send(network, token=unknown)).status_code == 401  # never seen: cannot be trusted
    assert (await health(network))["deps"]["identity"] == "down"


@pytest.mark.parametrize("case", ["missing", "bad_signature", "expired", "spoofed_from"])
async def test_invalid_tokens_are_rejected(network, case):
    identity = LocalIdentity(SECRET)
    token = {
        "missing": "",
        "bad_signature": (await identity.issue_token("tester"))[:-4] + "AAAA",
        "expired": identity.sign("tester", time.time() - 1),
        "spoofed_from": await identity.issue_token("someone-else"),
    }[case]
    env = Envelope(from_="tester", to=ME, capability="news_search", payload={"query": "x"}, token=token or None)
    resp = await network.api.post("/messages", json=env.wire())
    assert resp.status_code == 401 and resp.json()["payload"]["error"] == "unauthorized"
    events = [e["event"] for e in await network.events(resp.json()["trace_id"])]
    assert "msg.rejected" in events


async def test_observability_down_keeps_local_traces(network):
    await set_faults(network, tracer_down=True)
    resp = await send(network)
    assert resp.status_code == 200
    events = [e["event"] for e in await network.events(resp.json()["trace_id"])]
    assert events == ["msg.received", "msg.replied"]
    body = await health(network)
    assert body["deps"]["tracer"] == "down" and body["status"] == "degraded" and body["faults"] == ["tracer_down"]


async def test_llm_down_falls_back_and_shows_in_health(network):
    llm = FakeGemini().client(redis=network.state.redis)
    llm._faults = network.state.faults
    network.state.handler._deps["llm"] = llm
    await set_faults(network, llm_down=True)

    token = await LocalIdentity(SECRET).issue_token("tester")
    env = Envelope(from_="tester", to=ME, capability="news_summary", payload={"query": "TCB"}, token=token)
    reply = (await network.api.post("/messages", json=env.wire())).json()
    assert reply["type"] == "response" and reply["payload"]["generated_by"] == "extractive"
    assert "llm.fallback" in [e["event"] for e in await network.events(reply["trace_id"])]
    # /health only reports an LLM error when a key is configured.
    network.state.settings.gemini_api_key = "test-key"
    body = await health(network)
    assert body["deps"]["llm"].startswith("error") and body["status"] == "degraded"


async def test_dead_crawler_degrades_but_old_data_is_still_served(network):
    async with network.state.sessions() as s:
        await s.execute(update(Source).values(last_crawled_at=datetime.now(UTC) - timedelta(hours=2)))
        await s.commit()
    body = await health(network)
    assert body["status"] == "degraded" and body["deps"]["crawler"].startswith("stale")
    reply = (await send(network)).json()
    assert reply["type"] == "response" and reply["payload"]["count"] == 1
    assert reply["payload"]["freshness"].startswith(str((datetime.now(UTC) - timedelta(hours=2)).date()))


async def test_this_agent_failing_or_slow_on_purpose(network):
    await set_faults(network, inbound_fail_status=503)
    assert (await send(network)).status_code == 503
    await set_faults(network, inbound_fail_status=None, inbound_delay_s=0.3)
    started = time.monotonic()
    assert (await send(network)).status_code == 200
    assert time.monotonic() - started >= 0.3
    await set_faults(network)
    assert (await health(network))["faults"] == []


# --- dashboard admin ----------------------------------------------------------------------------------------


async def test_admin_endpoints_require_login(network):
    assert (await network.api.post("/api/v1/admin/login", json={"password": "wrong"})).status_code == 401
    for method, path in (("GET", "/api/v1/admin/faults"), ("PUT", "/api/v1/admin/faults"),
                         ("GET", "/api/v1/admin/mocks"), ("POST", "/api/v1/admin/agents/call")):
        assert (await network.api.request(method, path, json={})).status_code == 401
        bad = {"Authorization": "Bearer forged.token"}
        assert (await network.api.request(method, path, json={}, headers=bad)).status_code == 401
    assert (await network.api.get("/api/v1/admin/session", headers=await admin_headers(network))).json() == {"ok": True}


async def test_faults_can_be_disabled(network):
    network.state.faults.enabled = False
    resp = await network.api.put("/api/v1/admin/faults", json={"llm_down": True}, headers=await admin_headers(network))
    assert resp.status_code == 403


async def test_mock_faults_are_controlled_from_the_dashboard(network):
    headers = await admin_headers(network)
    mocks = (await network.api.get("/api/v1/admin/mocks", headers=headers)).json()
    assert {m["agent_id"] for m in mocks} == {"mock-finance-a", "mock-finance-b", "mock-finance-c",
                                              "mock-weather-a", "mock-weather-b"}
    assert all(m["reachable"] for m in mocks)

    resp = await network.api.put("/api/v1/admin/mocks/mock-finance-a/fault", json={"fail_status": 503}, headers=headers)
    assert resp.status_code == 200 and network.mock("mock-finance-a").fault.fail_status == 503
    assert (await network.api.put("/api/v1/admin/mocks/" + ME + "/fault", json={}, headers=headers)).status_code == 404


async def test_test_message_to_one_agent(network):
    headers = await admin_headers(network)
    ok = (await network.api.post("/api/v1/admin/agents/call", headers=headers, json={
        "agent_id": "mock-weather-a", "capability": "weather", "payload": {"location": "Huế"}})).json()
    assert ok["ok"] is True and ok["payload"]["location"] == "Huế"
    refused = (await network.api.post("/api/v1/admin/agents/call", headers=headers, json={
        "agent_id": "mock-weather-a", "capability": "weather", "payload": {}})).json()
    assert refused["ok"] is False and "location" in refused["error"]
    missing = await network.api.post("/api/v1/admin/agents/call", headers=headers,
                                     json={"agent_id": "nobody", "capability": "x"})
    assert missing.status_code == 404


# --- metrics and network graph -----------------------------------------------------------------------------


async def test_metrics_count_requests_errors_retries_and_duplicates(network):
    await send(network, message_id="m-metric")
    await send(network, message_id="m-metric")  # duplicate
    await send(network, token="forged.token")  # rejected
    network.mock("mock-finance-a").fault.fail_status = 503
    network.mock("mock-finance-a").fault.fail_times = 1
    await network.ask()

    metrics = (await network.api.get("/api/v1/metrics", params={"window_minutes": 60})).json()
    assert metrics["inbound_requests"] == 2  # the accepted one and the rejected one
    assert metrics["error_rate"] == 0.5
    assert metrics["duplicates"] == 1 and metrics["rejected"] == 1
    assert metrics["outbound_calls"] == 2 and metrics["retries"] == 1
    assert metrics["p95_ms"] is not None
    assert len(metrics["series"]) == 24 and sum(b["requests"] for b in metrics["series"]) == 2
    assert {c["capability"] for c in metrics["by_capability"]} == {"news_search"}


async def test_network_shows_who_talked_to_whom(network):
    await network.ask()
    await send(network)
    graph = (await network.api.get("/api/v1/network")).json()
    me = next(n for n in graph["nodes"] if n["is_self"])
    assert me["agent_id"] == ME and "news_search" in me["capabilities"]
    assert len(graph["nodes"]) == 6
    edges = {(e["peer"], e["direction"]): e for e in graph["edges"]}
    assert edges[("mock-finance-a", "outbound")]["messages"] == 1
    assert edges[("tester", "inbound")]["messages"] == 1
