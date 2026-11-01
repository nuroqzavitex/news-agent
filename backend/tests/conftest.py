from __future__ import annotations

from typing import Any

import fakeredis
import pytest

from app.adapters.local.identity import LocalIdentity
from app.contracts import Envelope


class MemoryMessageLog:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    async def record(self, envelope: Envelope, **kwargs: Any) -> None:
        self.rows.append({"message_id": envelope.message_id, **kwargs})


class MemoryTraceSink:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def save(self, event: dict[str, Any]) -> None:
        self.events.append(event)

    def names(self) -> list[str]:
        return [e["event"] for e in self.events]


@pytest.fixture
def redis():
    return fakeredis.FakeAsyncRedis()


@pytest.fixture
def identity() -> LocalIdentity:
    return LocalIdentity("test-secret")
