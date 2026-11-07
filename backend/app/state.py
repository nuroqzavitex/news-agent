"""Các đối tượng dùng chung trong một process agent-api (tạo trong lifespan)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import httpx
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.agent.capabilities import CapabilityRegistry
from app.agent.client import AgentClient
from app.agent.coordinator import Coordinator
from app.agent.handler import MessageHandler
from app.contracts import AgentCard
from app.factory import Infra
from app.services.memory import KnowledgeStore
from app.services.search import SearchService
from app.services.tracing import TraceRecorder
from app.settings import Settings


@dataclass
class AppState:
    settings: Settings
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]
    redis: Redis
    http: httpx.AsyncClient
    infra: Infra
    capabilities: CapabilityRegistry
    search: SearchService
    tracer: TraceRecorder
    handler: MessageHandler
    client: AgentClient
    memory: KnowledgeStore
    coordinator: Coordinator
    started_at: float = field(default_factory=time.time)

    def card(self, status: str = "online") -> AgentCard:
        return AgentCard(
            agent_id=self.settings.agent_id,
            group_id=self.settings.group_id,
            domain="news",
            capabilities=self.capabilities.names(),
            endpoint=self.settings.agent_public_endpoint,
            status=status,  # type: ignore[arg-type]
            version=self.settings.agent_version,
        )
