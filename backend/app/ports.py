"""4 interface tới hạ tầng chung của lớp (spec §1.2 — ports & adapters).

Mỗi port có bản `local` (tự chạy) và bản `remote` (Team 1–4); chọn bằng env trong factory.py.
"""

from __future__ import annotations

from typing import Any, Protocol

from app.contracts import AgentCard, AgentStatus, Envelope, Principal


class InfraError(Exception):
    """Hạ tầng không phản hồi / lỗi — caller quyết định fallback."""


class RegistryPort(Protocol):
    """Team 1 — đăng ký và tìm agent."""

    async def register(self, card: AgentCard) -> None: ...
    async def heartbeat(self, agent_id: str, status: AgentStatus) -> None: ...
    async def find_by_capability(self, capability: str) -> list[AgentCard]: ...
    async def list_agents(self) -> list[AgentCard]: ...
    async def ping(self) -> bool: ...


class BrokerPort(Protocol):
    """Team 2 — chuyển message tới agent khác, trả về reply envelope."""

    async def send(self, envelope: Envelope, target: AgentCard, timeout_s: float) -> Envelope: ...
    async def ping(self) -> bool: ...


class IdentityPort(Protocol):
    """Team 3 — cấp và xác thực token."""

    async def issue_token(self, agent_id: str) -> str: ...
    async def validate(self, token: str) -> Principal | None: ...
    async def ping(self) -> bool: ...


class TracerPort(Protocol):
    """Team 4 — nhận trace event."""

    async def emit(self, event: dict[str, Any]) -> None: ...
    async def ping(self) -> bool: ...
