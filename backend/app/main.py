"""agent-api: Agent protocol + REST cho dashboard. Stateless — state nằm ở Postgres/Redis."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis

from app.agent.capabilities import load_builtin_capabilities
from app.agent.dedup import Dedup
from app.agent.handler import MessageHandler
from app.api import agent_routes
from app.api.ui_routes import articles, observability
from app.db.repositories import SqlMessageLog, SqlTraceSink
from app.db.session import make_engine, make_session_factory
from app.factory import build_infra
from app.logging_setup import setup_logging
from app.services.health import compute_health
from app.services.search import SearchService
from app.services.tracing import TraceRecorder
from app.settings import Settings, get_settings
from app.state import AppState

log = logging.getLogger("agent-api")


def build_state(settings: Settings) -> AppState:
    engine = make_engine(settings.database_url)
    sessions = make_session_factory(engine)
    redis = Redis.from_url(settings.redis_url)
    http = httpx.AsyncClient(headers={"User-Agent": settings.http_user_agent})
    infra = build_infra(settings, http)
    capabilities = load_builtin_capabilities()
    search = SearchService(sessions)
    tracer = TraceRecorder(settings.agent_id, infra.tracer, SqlTraceSink(sessions))
    handler = MessageHandler(
        agent_id=settings.agent_id,
        identity=infra.identity,
        dedup=Dedup(redis, reply_ttl_s=settings.dedup_ttl_s,
                    processing_ttl_s=int(settings.capability_timeout_s * 3)),
        capabilities=capabilities,
        message_log=SqlMessageLog(sessions),
        tracer=tracer,
        capability_timeout_s=settings.capability_timeout_s,
        deps={"search": search, "sessions": sessions, "redis": redis},
    )
    return AppState(settings=settings, engine=engine, sessions=sessions, redis=redis, http=http,
                    infra=infra, capabilities=capabilities, search=search, tracer=tracer, handler=handler)


async def _heartbeat_loop(state: AppState) -> None:
    """Đăng ký với Registry rồi gửi heartbeat định kỳ. Registry lỗi không làm agent dừng."""
    registered = False
    while True:
        try:
            health = await compute_health(state)
            if not registered:
                await state.infra.registry.register(state.card(status=health["status"]))
                registered = True
                log.info("registered %s", state.settings.agent_id)
            else:
                await state.infra.registry.heartbeat(state.settings.agent_id, health["status"])
        except Exception:  # noqa: BLE001
            log.warning("registry register/heartbeat failed", exc_info=True)
        await asyncio.sleep(state.settings.heartbeat_interval_s)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    state: AppState = app.state.agent
    task = asyncio.create_task(_heartbeat_loop(state))
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await state.http.aclose()
        await state.redis.aclose()
        await state.engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    setup_logging()
    app = FastAPI(title="News Agent", version=settings.agent_version, lifespan=lifespan)
    app.state.agent = build_state(settings)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(agent_routes.router)
    app.include_router(articles.router)
    app.include_router(observability.router)
    return app


app = create_app()
