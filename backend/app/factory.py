"""Chọn adapter cho 4 port theo cấu hình (local | remote)."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.adapters.local.broker import DirectHttpBroker
from app.adapters.local.identity import LocalIdentity
from app.adapters.local.registry import LocalRegistry
from app.adapters.local.tracer import LogTracer
from app.ports import BrokerPort, IdentityPort, RegistryPort, TracerPort
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


def build_infra(settings: Settings, http: httpx.AsyncClient) -> Infra:
    if settings.registry_mode == "remote":
        raise _remote_not_ready("registry", "Team 1")
    if settings.broker_mode == "remote":
        raise _remote_not_ready("broker", "Team 2")
    if settings.identity_mode == "remote":
        raise _remote_not_ready("identity", "Team 3")
    if settings.tracer_mode == "remote":
        raise _remote_not_ready("tracer", "Team 4")

    return Infra(
        registry=LocalRegistry(settings.local_registry_file),
        broker=DirectHttpBroker(http),
        identity=LocalIdentity(settings.local_shared_secret),
        tracer=LogTracer(),
        modes={
            "registry": settings.registry_mode,
            "broker": settings.broker_mode,
            "identity": settings.identity_mode,
            "tracer": settings.tracer_mode,
        },
    )
