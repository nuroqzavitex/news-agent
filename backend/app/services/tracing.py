"""Ghi trace event: lưu local (trace_events) và đẩy sang TracerPort (Team 4). Không bao giờ ném lỗi."""

from __future__ import annotations

import logging
import time
from typing import Any, Protocol

from app.ports import TracerPort

log = logging.getLogger(__name__)


class TraceSink(Protocol):
    async def save(self, event: dict[str, Any]) -> None: ...


class TraceRecorder:
    def __init__(self, agent_id: str, tracer: TracerPort, sink: TraceSink | None = None) -> None:
        self._agent_id = agent_id
        self._tracer = tracer
        self._sink = sink

    async def event(self, name: str, *, trace_id: str, message_id: str | None = None, **attrs: Any) -> None:
        event = {
            "trace_id": trace_id,
            "message_id": message_id,
            "agent_id": self._agent_id,
            "event": name,
            "attrs": attrs,
            "ts": time.time(),
        }
        if self._sink is not None:
            try:
                await self._sink.save(event)
            except Exception:  # noqa: BLE001 — trace không được làm hỏng request
                log.warning("trace sink failed for %s", name, exc_info=True)
        try:
            await self._tracer.emit(event)
        except Exception:  # noqa: BLE001
            log.warning("tracer port failed for %s", name, exc_info=True)
