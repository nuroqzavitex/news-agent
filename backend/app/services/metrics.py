"""Metrics computed from the messages and trace_events tables (spec §10)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pydantic import BaseModel
from sqlalchemy import Float, and_, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Message, TraceEvent

BUCKETS = 24


class CapabilityLatency(BaseModel):
    capability: str
    requests: int
    errors: int
    p50_ms: float | None
    p95_ms: float | None


class Bucket(BaseModel):
    start: datetime
    requests: int
    errors: int
    p95_ms: float | None


class Metrics(BaseModel):
    window_minutes: int
    inbound_requests: int
    requests_per_minute: float
    error_rate: float
    p50_ms: float | None
    p95_ms: float | None
    outbound_calls: int
    outbound_failed: int
    retries: int
    duplicates: int
    rejected: int
    by_capability: list[CapabilityLatency]
    series: list[Bucket]


def _percentile(fraction: float):
    return func.percentile_cont(fraction).within_group(cast(Message.latency_ms, Float))


async def compute_metrics(sessions: async_sessionmaker[AsyncSession], window_minutes: int = 60) -> Metrics:
    now = datetime.now(UTC)
    since = now - timedelta(minutes=window_minutes)
    # Requests other agents sent us; replies we received to our own calls are inbound too, but not requests.
    inbound = and_(Message.direction == "inbound", Message.type == "request", Message.created_at >= since)
    failed = func.count().filter(Message.status == "failed")

    step = timedelta(minutes=window_minutes) / BUCKETS
    origin = now - step * BUCKETS
    bucket = func.date_bin(step, Message.created_at, origin).label("bucket")

    async with sessions() as s:
        total, errors, p50, p95 = (
            await s.execute(select(func.count(), failed, _percentile(0.5), _percentile(0.95)).where(inbound))
        ).one()
        per_capability = (
            await s.execute(
                select(Message.capability, func.count(), failed, _percentile(0.5), _percentile(0.95))
                .where(inbound)
                .group_by(Message.capability)
                .order_by(func.count().desc())
            )
        ).all()
        rows = (
            await s.execute(
                select(bucket, func.count(), failed, _percentile(0.95)).where(inbound).group_by(bucket).order_by(bucket)
            )
        ).all()
        outbound = and_(Message.direction == "outbound", Message.type == "request", Message.created_at >= since)
        out_total, out_failed, retries = (
            await s.execute(
                select(func.count(), failed, func.coalesce(func.sum(Message.attempts - 1), 0)).where(outbound)
            )
        ).one()
        event_counts = dict(
            (
                await s.execute(
                    select(TraceEvent.event, func.count())
                    .where(TraceEvent.ts >= since, TraceEvent.event.in_(("msg.duplicate", "msg.rejected")))
                    .group_by(TraceEvent.event)
                )
            ).all()
        )

    by_start = {r[0]: r for r in rows}
    series = []
    for i in range(BUCKETS):
        start = origin + step * i
        row = by_start.get(start)
        series.append(
            Bucket(start=start, requests=row[1] if row else 0, errors=row[2] if row else 0,
                   p95_ms=round(row[3], 1) if row and row[3] is not None else None)
        )

    def rounded(value: float | None) -> float | None:
        return round(value, 1) if value is not None else None

    return Metrics(
        window_minutes=window_minutes,
        inbound_requests=total,
        requests_per_minute=round(total / window_minutes, 2),
        error_rate=round(errors / total, 4) if total else 0.0,
        p50_ms=rounded(p50),
        p95_ms=rounded(p95),
        outbound_calls=out_total,
        outbound_failed=out_failed,
        retries=int(retries),
        duplicates=event_counts.get("msg.duplicate", 0),
        rejected=event_counts.get("msg.rejected", 0),
        by_capability=[
            CapabilityLatency(capability=c or "(none)", requests=n, errors=e, p50_ms=rounded(a), p95_ms=rounded(b))
            for c, n, e, a, b in per_capability
        ],
        series=series,
    )
