"""Truy cập DB cho agent protocol: nhật ký message, trace event."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.contracts import Envelope
from app.db.models import Message, TraceEvent


class SqlMessageLog:
    """Upsert theo message_id: một message có thể đổi trạng thái processing → replied/failed."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def record(
        self,
        envelope: Envelope,
        *,
        direction: str,
        status: str,
        latency_ms: int | None = None,
        error: str | None = None,
    ) -> None:
        values = {
            "message_id": envelope.message_id,
            "conversation_id": envelope.conversation_id,
            "trace_id": envelope.trace_id,
            "direction": direction,
            "from_agent": envelope.from_,
            "to_agent": envelope.to,
            "type": envelope.type,
            "capability": envelope.capability,
            "reply_to": envelope.reply_to,
            "payload": envelope.payload,
            "status": status,
            "latency_ms": latency_ms,
            "error": error,
        }
        stmt = insert(Message).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[Message.message_id],
            set_={
                "status": stmt.excluded.status,
                "latency_ms": func.coalesce(stmt.excluded.latency_ms, Message.latency_ms),
                "error": func.coalesce(stmt.excluded.error, Message.error),
                "updated_at": func.now(),
            },
        )
        async with self._sessions() as s:
            await s.execute(stmt)
            await s.commit()


class SqlTraceSink:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def save(self, event: dict[str, Any]) -> None:
        async with self._sessions() as s:
            s.add(
                TraceEvent(
                    trace_id=event["trace_id"],
                    message_id=event.get("message_id"),
                    agent_id=event["agent_id"],
                    event=event["event"],
                    attrs=event.get("attrs") or {},
                    ts=datetime.fromtimestamp(event["ts"], tz=UTC),
                )
            )
            await s.commit()
