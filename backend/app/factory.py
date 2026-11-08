"""Chọn adapter cho 4 port theo cấu hình (local | remote)."""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from redis.asyncio import Redis

from app.adapters.caching_identity import CachingIdentity
from app.adapters.local.broker import DirectHttpBroker
from app.adapters.local.identity import LocalIdentity
from app.adapters.local.registry import LocalRegistry
from app.adapters.local.tracer import LogTracer
from app.ports import BrokerPort, IdentityPort, RegistryPort, TracerPort
from app.services.faults import FaultSwitch, FaultyIdentity, FaultyRegistry, FaultyTracer, PassThroughBroker
from app.settings import Settings


@dataclass
class Infra:
    registry: RegistryPort
    broker: BrokerPort
    identity: IdentityPort
    tracer: TracerPort
    modes: dict[str, str]


def _remote_not_ready(port: str, team: str) -> NotImplementedError:
    return NotImplementedError(
        f"{port.upper()}_MODE=remote chưa được hỗ trợ: adapter {team} sẽ có khi nhóm đó công bố API. "
        f"Đặt {port.upper()}_MODE=local để chạy."
    )


def build_infra(
    settings: Settings, http: httpx.AsyncClient, faults: FaultSwitch | None = None, redis: Redis | None = None
) -> Infra:
    if settings.registry_mode == "remote":
        raise _remote_not_ready("registry", "Team 1")
    if settings.broker_mode == "remote":
        raise _remote_not_ready("broker", "Team 2")
    if settings.identity_mode == "remote":
        raise _remote_not_ready("identity", "Team 3")
    if settings.tracer_mode == "remote":
        raise _remote_not_ready("tracer", "Team 4")

    registry: RegistryPort = LocalRegistry(settings.local_registry_file)
    broker: BrokerPort = DirectHttpBroker(http)
    identity: IdentityPort = LocalIdentity(settings.local_shared_secret)
    tracer: TracerPort = LogTracer()
    if faults is not None and faults.enabled:
        registry = FaultyRegistry(registry, faults)
        broker = PassThroughBroker(broker)
        identity = FaultyIdentity(identity, faults)
        tracer = FaultyTracer(tracer, faults)
    if redis is not None:
        identity = CachingIdentity(identity, redis)

    return Infra(
        registry=registry,
        broker=broker,
        identity=identity,
        tracer=tracer,
        modes={
            "registry": settings.registry_mode,
            "broker": settings.broker_mode,
            "identity": settings.identity_mode,
            "tracer": settings.tracer_mode,
        },
    )
