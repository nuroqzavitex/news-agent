"""Integration tests: real Postgres (pgvector) + Redis.

Locally: `docker compose up -d postgres redis` then `uv run pytest -m integration`.
Uses a dedicated database (`news_test`) and Redis DB 15, so dev data is never touched.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import asyncpg
import pytest
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert

from app.db.models import Source
from app.db.session import make_engine, make_session_factory

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
