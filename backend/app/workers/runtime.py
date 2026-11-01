"""Khung chạy chung cho các worker: tạo kết nối, bắt tín hiệu dừng, dọn dẹp."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import socket
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass

import httpx
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.session import make_engine, make_session_factory
from app.logging_setup import setup_logging
from app.settings import Settings, get_settings


@dataclass
class WorkerContext:
    settings: Settings
    sessions: async_sessionmaker[AsyncSession]
    redis: Redis
    http: httpx.AsyncClient
    stop: asyncio.Event
    instance_id: str


@contextlib.asynccontextmanager
async def worker_context(name: str) -> AsyncIterator[WorkerContext]:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    # socket_timeout phải lớn hơn thời gian block của XREADGROUP (5s), nếu không sẽ TimeoutError khi rảnh.
    redis = Redis.from_url(settings.redis_url, decode_responses=True, socket_timeout=30, health_check_interval=30)
    http = httpx.AsyncClient(
        headers={"User-Agent": settings.http_user_agent}, follow_redirects=True, timeout=10.0
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):  # Windows không hỗ trợ add_signal_handler
            loop.add_signal_handler(sig, stop.set)
    try:
        yield WorkerContext(
            settings=settings,
            sessions=make_session_factory(engine),
            redis=redis,
            http=http,
            stop=stop,
            instance_id=f"{name}-{socket.gethostname()}-{os.getpid()}",
        )
    finally:
        await http.aclose()
        await redis.aclose()
        await engine.dispose()


def run_worker(name: str, main: Callable[[WorkerContext], Awaitable[None]]) -> None:
    setup_logging()
    log = logging.getLogger(name)

    async def _amain() -> None:
        async with worker_context(name) as ctx:
            log.info("%s started as %s", name, ctx.instance_id)
            await main(ctx)

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_amain())
