"""agent-api end to end over ASGI: /messages with news_search, message log, traces, dashboard REST."""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy.dialects.postgresql import insert

from app.adapters.local.identity import LocalIdentity
from app.contracts import Envelope
from app.db.models import Article
from app.main import create_app
from app.settings import Settings
from tests.integration.conftest import TEST_DATABASE_URL, TEST_REDIS_URL

pytestmark = pytest.mark.integration

SECRET = "integration-secret"
ME = "team-08-news"


@pytest.fixture
async def client(sessions, redis):
    async with sessions() as s:
        await s.execute(insert(Article).values([
            {"source_id": 1, "url": "https://news.test/1", "url_hash": "1", "title": "Giá vàng lập đỉnh mới",
             "summary": "Giá vàng SJC tăng", "content": "Giá vàng miếng SJC tăng mạnh"},
            {"source_id": 1, "url": "https://news.test/2", "url_hash": "2", "title": "Bóng đá Việt Nam thắng",
             "summary": "Đội tuyển thắng 3-0", "content": "trận đấu"},
        ]))
        await s.commit()
    settings = Settings(_env_file=None, database_url=TEST_DATABASE_URL, redis_url=TEST_REDIS_URL,
                        agent_id=ME, local_shared_secret=SECRET)
    app = create_app(settings)
    state = app.state.agent
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as c:
        yield c
    await state.http.aclose()
    await state.redis.aclose()
    await state.engine.dispose()


async def _send(client: httpx.AsyncClient, capability: str, payload: dict, **kw) -> httpx.Response:
    token = await LocalIdentity(SECRET).issue_token("tester")
    env = Envelope(from_="tester", to=ME, capability=capability, payload=payload, token=token, **kw)
    return await client.post("/messages", json=env.wire())


async def test_news_search_without_diacritics(client):
    resp = await _send(client, "news_search", {"query": "gia vang"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["type"] == "response"
    titles = [r["title"] for r in body["payload"]["results"]]
    assert titles == ["Giá vàng lập đỉnh mới"]
    assert body["payload"]["mode_used"] == "keyword"


async def test_message_log_and_trace_are_persisted(client):
    resp = await _send(client, "news_search", {"query": "bóng đá"}, message_id="m-int-1")
    trace_id = resp.json()["trace_id"]

    page = (await client.get("/api/v1/messages", params={"trace_id": trace_id})).json()
    by_direction = {m["direction"]: m for m in page["items"]}
    assert by_direction["inbound"]["status"] == "replied"
    assert by_direction["inbound"]["latency_ms"] is not None
    assert by_direction["outbound"]["reply_to"] == "m-int-1"

    events = [e["event"] for e in (await client.get(f"/api/v1/traces/{trace_id}")).json()]
    assert events == ["msg.received", "msg.replied"]


async def test_duplicate_returns_stored_reply(client):
    first = await _send(client, "news_search", {"query": "vàng"}, message_id="m-dup")
    second = await _send(client, "news_search", {"query": "vàng"}, message_id="m-dup")
    assert first.json() == second.json()


async def test_dashboard_articles_and_health(client):
    latest = (await client.get("/api/v1/articles")).json()
    assert latest["count"] == 2
    found = (await client.get("/api/v1/articles", params={"q": "bong da"})).json()
    assert [r["title"] for r in found["results"]] == ["Bóng đá Việt Nam thắng"]
    detail = (await client.get(f"/api/v1/articles/{found['results'][0]['id']}")).json()
    assert detail["source"] == "testnews"
    assert (await client.get("/api/v1/articles/a_999")).status_code == 404

    grid = (await client.get("/api/v1/stats/crawl-grid", params={"hours": 6})).json()
    assert len(grid["hours"]) == 6
    [src] = grid["sources"]
    assert src["code"] == "testnews" and sum(src["counts"]) == 2 and src["counts"][-1] == 2

    health = (await client.get("/health")).json()
    assert health["deps"]["db"] == "ok" and health["deps"]["redis"] == "ok"
    assert health["status"] == "degraded"  # crawler never ran in the test DB
