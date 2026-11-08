"""Integration tests: real Postgres (pgvector) + Redis.

Locally: `docker compose up -d postgres redis` then `uv run pytest -m integration`.
Uses a dedicated database (`news_test`) and Redis DB 15, so dev data is never touched.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import asyncpg
import httpx
import pytest
import yaml
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert

from app.db.models import Article, Source
from app.db.session import make_engine, make_session_factory
from app.main import create_app
from app.settings import Settings
from app.state import AppState
from scripts.mock_agent import create_mock_app

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://news:news@localhost:5432/news_test"
)
TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15")
BACKEND_DIR = Path(__file__).resolve().parents[2]

TABLES = "trace_events, messages, knowledge_items, agent_cache, article_chunks, articles, sources"


async def _ensure_database() -> None:
    dsn = TEST_DATABASE_URL.replace("+asyncpg", "")
    db_name = dsn.rsplit("/", 1)[1]
    admin = await asyncpg.connect(dsn.rsplit("/", 1)[0] + "/postgres")
    try:
        if not await admin.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", db_name):
            await admin.execute(f'CREATE DATABASE "{db_name}"')
    finally:
        await admin.close()


@pytest.fixture(scope="session", autouse=True)
def _migrated_database() -> None:
    try:
        asyncio.run(_ensure_database())
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"Postgres not available for integration tests: {exc}")
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_DIR, check=True, env={**os.environ, "DATABASE_URL": TEST_DATABASE_URL},
    )


@pytest.fixture
async def sessions():
    engine = make_engine(TEST_DATABASE_URL)
    factory = make_session_factory(engine)
    async with factory() as s:
        await s.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))
        await s.execute(insert(Source).values(
            code="testnews", name="Test News", rss_url="https://news.test/rss", category="tong-hop"
        ))
        await s.commit()
    yield factory
    await engine.dispose()


@pytest.fixture
async def redis():
    r = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    await r.flushdb()
    yield r
    await r.aclose()


# --- A small agent network: this agent plus in-process mock agents ---------------------------------------

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
    # Route the agent's outgoing calls (and the dashboard's mock controls) to the in-process mocks.
    mock_http = httpx.AsyncClient(mounts={f"http://{name}": TimedTransport(m) for name, m in mocks.items()})
    direct = getattr(state.infra.broker, "_inner", state.infra.broker)  # unwrap the fault-injection wrapper
    direct._client = mock_http
    real_http, state.http = state.http, mock_http
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as api:
        yield Network(api, state, mocks)
    state.http = real_http
    await mock_http.aclose()
    await state.http.aclose()
    await state.redis.aclose()
    await state.engine.dispose()


