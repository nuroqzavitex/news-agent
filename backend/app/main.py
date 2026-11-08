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
from app.agent.client import AgentClient
from app.agent.coordinator import Coordinator
from app.agent.dedup import Dedup
from app.agent.handler import MessageHandler
from app.api import agent_routes
from app.api.ui_routes import admin, articles, coordination, observability
from app.db.repositories import SqlMessageLog, SqlTraceSink
from app.db.session import make_engine, make_session_factory
from app.factory import build_infra
from app.logging_setup import setup_logging
from app.services.admin_auth import AdminAuth
from app.services.faults import FaultSwitch
from app.services.health import compute_health
from app.services.llm import GeminiClient
from app.services.memory import KnowledgeStore
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
    faults = FaultSwitch(redis, settings.faults_enabled)
    infra = build_infra(settings, http, faults, redis)
    capabilities = load_builtin_capabilities()
    llm = GeminiClient(
        api_key=settings.gemini_api_key,
        http=http,
        llm_model=settings.llm_model,
        embedding_model=settings.embedding_model,
        redis=redis,
        faults=faults,
    )
    search = SearchService(sessions, llm, redis, query_embed_timeout_s=settings.query_embed_timeout_s)
    tracer = TraceRecorder(settings.agent_id, infra.tracer, SqlTraceSink(sessions))
    message_log = SqlMessageLog(sessions)
    deps = {
        "search": search, "sessions": sessions, "redis": redis, "llm": llm, "tracer": tracer, "settings": settings,
    }
    handler = MessageHandler(
        agent_id=settings.agent_id,
        identity=infra.identity,
        dedup=Dedup(redis, reply_ttl_s=settings.dedup_ttl_s,
                    processing_ttl_s=int(settings.capability_timeout_s * 3)),
        capabilities=capabilities,
        message_log=message_log,
        tracer=tracer,
        capability_timeout_s=settings.capability_timeout_s,
        deps=deps,
    )
    client = AgentClient(
        agent_id=settings.agent_id,
        registry=infra.registry,
        broker=infra.broker,
        identity=infra.identity,
        tracer=tracer,
        message_log=message_log,
        sessions=sessions,
        redis=redis,
        call_timeout_s=settings.agent_call_timeout_s,
        retries=settings.agent_call_retries,
        deadline_s=settings.agent_call_deadline_s,
    )
    memory = KnowledgeStore(sessions)
    coordinator = Coordinator(
        agent_id=settings.agent_id,
        capabilities=capabilities,
        deps=deps,
        client=client,
        memory=memory,
        llm=llm,
        tracer=tracer,
        llm_timeout_s=settings.llm_timeout_s,
        capability_timeout_s=settings.capability_timeout_s,
    )
    return AppState(settings=settings, engine=engine, sessions=sessions, redis=redis, http=http,
                    infra=infra, capabilities=capabilities, search=search, tracer=tracer, handler=handler,
                    client=client, memory=memory, coordinator=coordinator, faults=faults,
                    admin=AdminAuth(settings.admin_password, settings.local_shared_secret))


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
    app.include_router(coordination.router)
    app.include_router(admin.router)
    return app


app = create_app()
