"""Fault injection for demonstrating spec §9 (dev only, FAULTS_ENABLED).

The switches live in Redis, so every replica and worker sees the same faults. Each switch makes one dependency
fail the way it would in production, through wrappers around the ports, so the real fallbacks are exercised.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from pydantic import BaseModel, Field
from redis.asyncio import Redis

from app.contracts import AgentCard, AgentStatus, Envelope, Principal
from app.ports import BrokerPort, IdentityPort, InfraError, RegistryPort, TracerPort

log = logging.getLogger(__name__)

KEY = "faults:config"
CACHE_S = 1.0


class FaultConfig(BaseModel):
    inbound_delay_s: float = Field(default=0.0, ge=0, le=60, description="Delay before answering /messages")
    inbound_fail_status: int | None = Field(default=None, ge=500, le=599, description="Answer /messages with this")
    llm_down: bool = False
    registry_down: bool = False
    identity_down: bool = False
    tracer_down: bool = False

    def active(self) -> list[str]:
        return [name for name, value in self.model_dump().items() if value not in (0, 0.0, None, False)]


class FaultSwitch:
    def __init__(self, redis: Redis, enabled: bool) -> None:
        self._redis = redis
        self.enabled = enabled
        self._cached: tuple[float, FaultConfig] | None = None

    async def get(self) -> FaultConfig:
        if not self.enabled:
            return FaultConfig()
        if self._cached and time.monotonic() - self._cached[0] < CACHE_S:
            return self._cached[1]
        try:
            raw = await self._redis.get(KEY)
            config = FaultConfig.model_validate_json(raw) if raw else FaultConfig()
        except Exception:  # noqa: BLE001 — a broken switch must never break the agent
            log.warning("could not read fault config", exc_info=True)
            config = FaultConfig()
        self._cached = (time.monotonic(), config)
        return config

    async def set(self, config: FaultConfig) -> FaultConfig:
        await self._redis.set(KEY, config.model_dump_json())
        self._cached = None
        log.warning("fault config changed: %s", config.active() or "none")
        return config


class FaultyRegistry:
    def __init__(self, inner: RegistryPort, faults: FaultSwitch) -> None:
        self._inner, self._faults = inner, faults

    async def _check(self) -> None:
        if (await self._faults.get()).registry_down:
            raise InfraError("registry unreachable (fault injected)")

    async def register(self, card: AgentCard) -> None:
        await self._check()
        await self._inner.register(card)

    async def heartbeat(self, agent_id: str, status: AgentStatus) -> None:
        await self._check()
        await self._inner.heartbeat(agent_id, status)

    async def find_by_capability(self, capability: str) -> list[AgentCard]:
        await self._check()
        return await self._inner.find_by_capability(capability)

    async def list_agents(self) -> list[AgentCard]:
        await self._check()
        return await self._inner.list_agents()

    async def ping(self) -> bool:
        return not (await self._faults.get()).registry_down and await self._inner.ping()


class FaultyIdentity:
    def __init__(self, inner: IdentityPort, faults: FaultSwitch) -> None:
        self._inner, self._faults = inner, faults

    async def _check(self) -> None:
        if (await self._faults.get()).identity_down:
            raise InfraError("identity unreachable (fault injected)")

    async def issue_token(self, agent_id: str) -> str:
        await self._check()
        return await self._inner.issue_token(agent_id)

    async def validate(self, token: str) -> Principal | None:
        await self._check()
        return await self._inner.validate(token)

    async def ping(self) -> bool:
        return not (await self._faults.get()).identity_down and await self._inner.ping()


class FaultyTracer:
    def __init__(self, inner: TracerPort, faults: FaultSwitch) -> None:
        self._inner, self._faults = inner, faults

    async def emit(self, event: dict[str, Any]) -> None:
        if (await self._faults.get()).tracer_down:
            raise InfraError("tracer unreachable (fault injected)")
        await self._inner.emit(event)

    async def ping(self) -> bool:
        return not (await self._faults.get()).tracer_down and await self._inner.ping()


class PassThroughBroker:
    """No broker fault yet: with the local adapter the broker is a direct HTTP call (see spec §9)."""

    def __init__(self, inner: BrokerPort) -> None:
        self._inner = inner

    async def send(self, envelope: Envelope, target: AgentCard, timeout_s: float) -> Envelope:
        return await self._inner.send(envelope, target, timeout_s)

    async def ping(self) -> bool:
        return await self._inner.ping()
