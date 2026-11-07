"""Coordination end to end: real Postgres + Redis, and mock agents speaking the real protocol over ASGI."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import yaml
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from app.db.models import AgentCache, Article, KnowledgeItem, Message
from app.main import create_app
from app.settings import Settings
from app.state import AppState
from scripts.mock_agent import create_mock_app
from tests.fake_gemini import FakeGemini
from tests.integration.conftest import TEST_DATABASE_URL, TEST_REDIS_URL

pytestmark = pytest.mark.integration

SECRET = "integration-secret"
ME = "team-08-news"
TCB_QUESTION = "Tin tức ảnh hưởng thế nào tới cổ phiếu TCB tuần này?"


class TimedTransport(httpx.AsyncBaseTransport):
    """ASGITransport ignores timeouts; a slow mock must look like a slow network."""

    def __init__(self, app: Any) -> None:
        self._inner = httpx.ASGITransport(app)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        timeout = request.extensions.get("timeout", {}).get("read")
        try:
            return await asyncio.wait_for(self._inner.handle_async_request(request), timeout)
        except TimeoutError as exc:
            raise httpx.ReadTimeout("mock took too long", request=request) from exc


@dataclass
class Network:
    api: httpx.AsyncClient
    state: AppState
    mocks: dict[str, Any]

    async def ask(self, question: str = TCB_QUESTION) -> dict[str, Any]:
        resp = await self.api.post("/api/v1/ask", json={"question": question})
        assert resp.status_code == 200, resp.text
        return resp.json()

    def mock(self, name: str):
        return self.mocks[name].state.mock

    async def events(self, trace_id: str) -> list[dict[str, Any]]:
        return (await self.api.get(f"/api/v1/traces/{trace_id}")).json()


AGENTS = {
    "mock-finance-a": dict(capabilities=["stock_price"], price_bias=0.0, confidence=0.9),
    "mock-finance-b": dict(capabilities=["stock_price"], price_bias=0.02, confidence=0.7),
    "mock-finance-c": dict(capabilities=["stock_price"], price_bias=0.0, confidence=0.6),
    "mock-weather-a": dict(capabilities=["weather"]),
    "mock-weather-b": dict(capabilities=["weather"]),
}


@pytest.fixture
async def network(sessions, redis, tmp_path, monkeypatch):
    monkeypatch.setattr("app.agent.client.BACKOFF_S", (0.01, 0.01, 0.01))
    async with sessions() as s:
        await s.execute(insert(Article).values(
            {"source_id": 1, "url": "https://news.test/tcb", "url_hash": "tcb", "title": "TCB báo lãi kỷ lục",
             "summary": "Lợi nhuận TCB tăng trưởng, cổ phiếu TCB phục hồi", "sentiment_label": "positive",
             "sentiment_score": 0.8, "sentiment_source": "lexicon"}
        ))
        await s.commit()

    mocks = {name: create_mock_app(agent_id=name, secret=SECRET, **cfg) for name, cfg in AGENTS.items()}
    registry_file = tmp_path / "registry.yaml"
    registry_file.write_text(yaml.safe_dump({"agents": [
        {"agent_id": name, "group_id": "mock", "domain": "mock", "capabilities": cfg["capabilities"],
         "endpoint": f"http://{name}"}
        for name, cfg in AGENTS.items()
    ]}), encoding="utf-8")

    settings = Settings(
        _env_file=None, database_url=TEST_DATABASE_URL, redis_url=TEST_REDIS_URL, agent_id=ME,
        local_shared_secret=SECRET, local_registry_file=str(registry_file),
        agent_call_timeout_s=0.3, agent_call_retries=1, agent_call_deadline_s=5,
    )
    app = create_app(settings)
    state = app.state.agent
    state.infra.broker._client = httpx.AsyncClient(
        mounts={f"http://{name}": TimedTransport(m) for name, m in mocks.items()}
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as api:
        yield Network(api, state, mocks)
    await state.infra.broker._client.aclose()
    await state.http.aclose()
    await state.redis.aclose()
    await state.engine.dispose()


def step(result: dict[str, Any], capability: str) -> dict[str, Any]:
    return next(s for s in result["steps"] if s["capability"] == capability)


async def test_question_combines_local_news_with_two_finance_agents(network):
    result = await network.ask()
    assert result["planned_by"] == "rules" and result["generated_by"] == "template"
    assert {s["capability"] for s in result["steps"]} == {"news_summary", "sentiment", "stock_price"}

    # Scenario 3: at least three agents take part, and the trace proves it.
    assert result["agents"] == ["mock-finance-a", "mock-finance-b", ME]
    assert result["partial"] is False and result["missing"] == []
    assert step(result, "news_summary")["agents"] == [ME]

    events = await network.events(result["trace_id"])
    names = [e["event"] for e in events]
    assert names[0] == "ask.received" and names[-1] == "ask.answered"
    assert {e["attrs"]["to_agent"] for e in events if e["event"] == "broker.send"} == {
        "mock-finance-a", "mock-finance-b"
    }

    # Messages are logged with the same trace, and the answer says what happened.
    page = (await network.api.get("/api/v1/messages", params={"trace_id": result["trace_id"]})).json()
    assert {m["to_agent"] for m in page["items"] if m["direction"] == "outbound"} == {
        "mock-finance-a", "mock-finance-b"
    }
    assert "TCB" in result["answer"] and "VND" in result["answer"]


async def test_conflicting_agents_are_recorded_and_reported(network):
    result = await network.ask()
    # Scenario 5: the agents disagree; the more confident one wins, and the conflict is stated.
    assert result["has_conflict"] is True
    stock = step(result, "stock_price")
    assert stock["data"]["source_agent"] == "mock-finance-a"
    assert [c["agent"] for c in stock["conflicts"]] == ["mock-finance-b"]
    assert "xung đột" in result["answer"]
    assert "memory.conflict" in [e["event"] for e in await network.events(result["trace_id"])]

    async with network.state.sessions() as s:
        items = (await s.scalars(select(KnowledgeItem).order_by(KnowledgeItem.id))).all()
    assert [i.source_agent for i in items] == ["mock-finance-a", "mock-finance-b"]
    assert items[0].key == "stock_price:TCB"
    assert items[0].conflict_group is not None and items[0].conflict_group == items[1].conflict_group
    assert items[0].source_message_id is not None  # provenance


async def test_memory_answers_within_ttl_without_asking_again(network):
    await network.ask()
    handled = network.mock("mock-finance-a").handled
    second = await network.ask()
    # Scenario 4.
    assert network.mock("mock-finance-a").handled == handled
    stock = step(second, "stock_price")
    assert stock["status"] == "memory" and stock["agents"] == ["mock-finance-a"]
    assert second["has_conflict"] is True  # the disagreement is still known
    assert "memory.hit" in [e["event"] for e in await network.events(second["trace_id"])]


async def _expire_memory(network: Network) -> None:
    async with network.state.sessions() as s:
        await s.execute(update(KnowledgeItem).values(expires_at=datetime.now(UTC) - timedelta(minutes=1)))
        await s.commit()


async def test_expired_memory_is_refreshed_as_a_new_version(network):
    await network.ask()
    await _expire_memory(network)
    third = await network.ask()
    assert step(third, "stock_price")["status"] == "ok"
    versions = (await network.api.get("/api/v1/memory/stock_price:TCB/versions")).json()
    from_a = [v for v in versions if v["source_agent"] == "mock-finance-a"]
    assert [v["version"] for v in from_a] == [2, 1]
    assert from_a[1]["superseded_by"] == from_a[0]["id"]
    current = (await network.api.get("/api/v1/memory")).json()
    assert {(i["source_agent"], i["version"]) for i in current} == {("mock-finance-a", 2), ("mock-finance-b", 2)}


async def test_stale_memory_is_used_and_labelled_when_agents_are_down(network):
    await network.ask()
    await _expire_memory(network)
    for name in ("mock-finance-a", "mock-finance-b", "mock-finance-c"):
        network.mock(name).fault.fail_status = 503
    result = await network.ask()
    stock = step(result, "stock_price")
    assert stock["status"] == "stale" and stock["data"]["symbol"] == "TCB"
    assert result["has_stale_data"] is True and result["partial"] is False
    assert "đã cũ" in result["answer"]


async def test_retry_keeps_message_id_so_the_receiver_can_deduplicate(network):
    network.mock("mock-finance-a").fault.fail_status = 503
    network.mock("mock-finance-a").fault.fail_times = 1
    result = await network.ask()
    assert step(result, "stock_price")["status"] == "ok"
    assert "mock-finance-a" in step(result, "stock_price")["agents"]

    assert sorted(network.mock("mock-finance-a").seen.values()) == [2]  # the same message_id, twice
    assert network.mock("mock-finance-a").handled == 1
    events = await network.events(result["trace_id"])
    assert "broker.retry" in [e["event"] for e in events]
    async with network.state.sessions() as s:
        attempts = await s.scalar(
            select(Message.attempts).where(Message.to_agent == "mock-finance-a", Message.direction == "outbound")
        )
    assert attempts == 2


async def test_slow_agent_times_out_and_the_other_one_answers(network):
    network.mock("mock-finance-a").fault.delay_s = 5
    result = await network.ask()
    stock = step(result, "stock_price")
    assert stock["status"] == "ok" and stock["agents"] == ["mock-finance-b"]
    assert stock["data"]["source_agent"] == "mock-finance-b"
    assert result["partial"] is False


async def test_failover_to_the_next_agent_when_those_asked_fail(network):
    network.mock("mock-weather-a").fault.fail_status = 503
    result = await network.ask("Thời tiết ở Đà Nẵng thế nào?")
    weather = step(result, "weather")
    assert weather["status"] == "ok" and weather["agents"] == ["mock-weather-b"]
    assert "Đà Nẵng" in result["answer"]
    names = [e["event"] for e in await network.events(result["trace_id"])]
    assert "client.failover" in names and "broker.retry" in names

    # And again from a fresh start with the finance agents: both asked fail, the third answers.
    for name in ("mock-finance-a", "mock-finance-b"):
        network.mock(name).fault.fail_status = 500
    stock = step(await network.ask(), "stock_price")
    assert stock["status"] == "ok" and stock["agents"] == ["mock-finance-c"]


async def test_partial_answer_when_every_agent_is_down(network):
    for name in ("mock-finance-a", "mock-finance-b", "mock-finance-c"):
        network.mock(name).fault.fail_status = 503
    result = await network.ask()
    assert result["partial"] is True and result["missing"] == ["stock_price"]
    assert step(result, "stock_price")["status"] == "failed"
    # The news part still comes through.
    assert step(result, "news_summary")["status"] == "ok"
    assert "Không lấy được stock_price" in result["answer"] and "TCB" in result["answer"]


async def test_capability_nobody_offers_is_reported_missing(network, tmp_path):
    network.state.client._registry = _EmptyRegistry()
    await network.state.redis.flushdb()
    result = await network.ask()
    assert result["missing"] == ["stock_price"] and step(result, "stock_price")["status"] == "missing"


class _EmptyRegistry:
    async def find_by_capability(self, capability: str) -> list:
        return []

    async def list_agents(self) -> list:
        return []


class _BrokenRegistry:
    async def find_by_capability(self, capability: str) -> list:
        raise ConnectionError("registry is down")

    async def list_agents(self) -> list:
        raise ConnectionError("registry is down")


async def test_registry_down_uses_known_agents(network):
    await network.ask()  # fills agent_cache
    async with network.state.sessions() as s:
        assert await s.get(AgentCache, "mock-finance-a") is not None
    network.state.client._registry = _BrokenRegistry()
    await network.state.redis.flushdb()
    await _expire_memory(network)

    result = await network.ask()
    stock = step(result, "stock_price")
    assert stock["status"] == "ok" and stock["agents"]
    assert "registry.fallback_cache" in [e["event"] for e in await network.events(result["trace_id"])]


async def test_registry_down_with_nothing_known_is_still_an_answer(network):
    network.state.client._registry = _BrokenRegistry()
    result = await network.ask()
    assert result["missing"] == ["stock_price"] and step(result, "news_summary")["status"] == "ok"


async def test_agent_refusing_is_not_retried(network):
    # A bad request gets the same answer every time, so it must not be sent twice.
    result = await network.ask("Thời tiết ở Huế thế nào?")
    assert step(result, "weather")["status"] == "ok"
    outcome = await network.state.client.call("weather", {}, trace_id="tr-refuse", fan_out=1)
    assert outcome.replies == [] and outcome.failures[0].attempts == 1
    assert len(outcome.failures) == 2  # both agents were tried, once each


async def test_llm_plans_and_composes_when_available(network):
    plan = (
        '{"steps": [{"capability": "stock_price", "payload": {"symbol": "VCB"}},'
        ' {"capability": "hack", "payload": {}}]}'
    )
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        text = plan if len(calls) == 1 else '{"answer": "Giá VCB từ các agent."}'
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": text}]}}]})

    llm = FakeGemini().client()
    llm._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    network.state.coordinator._llm = llm
    result = await network.ask("Giá VCB?")
    assert result["planned_by"] == "llm" and [s["capability"] for s in result["steps"]] == ["stock_price"]
    assert result["answer"] == "Giá VCB từ các agent." and result["generated_by"] == "test-llm"


async def test_llm_outage_falls_back_to_rules_and_template(network):
    fake = FakeGemini()
    fake.fail_with = 503
    network.state.coordinator._llm = fake.client()
    result = await network.ask()
    assert result["planned_by"] == "rules" and result["generated_by"] == "template"
    assert step(result, "stock_price")["status"] == "ok"


async def test_memory_purge_removes_only_long_expired_items(network):
    await network.ask()
    await _expire_memory(network)
    await network.ask()  # new versions; the old ones point at them
    assert await network.state.memory.purge_expired() == 0  # expired, but only just

    async with network.state.sessions() as s:
        await s.execute(
            update(KnowledgeItem)
            .where(KnowledgeItem.superseded_by.is_not(None))
            .values(expires_at=datetime.now(UTC) - timedelta(days=8))
        )
        await s.commit()
    assert await network.state.memory.purge_expired() == 2
    assert len((await network.api.get("/api/v1/memory")).json()) == 2
