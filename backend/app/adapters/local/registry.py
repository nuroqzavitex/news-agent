"""Registry local: danh sách agent tĩnh từ YAML + các agent tự đăng ký trong process."""

from __future__ import annotations

from pathlib import Path

import yaml

from app.contracts import AgentCard, AgentStatus


class LocalRegistry:
    def __init__(self, static_file: str | None = None) -> None:
        self._agents: dict[str, AgentCard] = {}
        if static_file and Path(static_file).exists():
            data = yaml.safe_load(Path(static_file).read_text(encoding="utf-8")) or {}
            for raw in data.get("agents", []):
                card = AgentCard.model_validate(raw)
                self._agents[card.agent_id] = card

    async def register(self, card: AgentCard) -> None:
        self._agents[card.agent_id] = card

    async def heartbeat(self, agent_id: str, status: AgentStatus) -> None:
        if agent_id in self._agents:
            self._agents[agent_id] = self._agents[agent_id].model_copy(update={"status": status})

    async def find_by_capability(self, capability: str) -> list[AgentCard]:
        return [
            c for c in self._agents.values() if capability in c.capabilities and c.status != "offline"
        ]

    async def list_agents(self) -> list[AgentCard]:
        return list(self._agents.values())

    async def ping(self) -> bool:
        return True
