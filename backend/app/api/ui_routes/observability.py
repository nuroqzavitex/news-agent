"""REST cho dashboard: nhật ký message, trace, trạng thái hạ tầng, danh sách agent."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import case, func, or_, select

from app.api.deps import get_state
from app.contracts import AgentCard
from app.db.models import AgentCache, Message, TraceEvent
from app.services.metrics import Metrics, compute_metrics
from app.state import AppState

router = APIRouter(prefix="/api/v1", tags=["observability"])


class MessageOut(BaseModel):
    message_id: str
    conversation_id: str
    trace_id: str
    direction: str
    from_agent: str
    to_agent: str
    type: str
    capability: str | None
    reply_to: str | None
    payload: dict[str, Any]
    status: str
    attempts: int
    latency_ms: int | None
    error: str | None
    created_at: datetime
    updated_at: datetime


class MessagePage(BaseModel):
    items: list[MessageOut]
    total: int


@router.get("/messages", response_model=MessagePage)
async def list_messages(
    direction: str | None = None,
    status: str | None = None,
    peer: str | None = None,
    capability: str | None = None,
    trace_id: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    state: AppState = Depends(get_state),
) -> MessagePage:
    filters = []
    if direction:
        filters.append(Message.direction == direction)
    if status:
        filters.append(Message.status == status)
    if peer:
        filters.append(or_(Message.from_agent == peer, Message.to_agent == peer))
    if capability:
        filters.append(Message.capability == capability)
    if trace_id:
        filters.append(Message.trace_id == trace_id)
    async with state.sessions() as s:
        total = await s.scalar(select(func.count()).select_from(Message).where(*filters))
        rows = (await s.scalars(
            select(Message).where(*filters).order_by(Message.created_at.desc())
            .limit(page_size).offset((page - 1) * page_size)
        )).all()
    return MessagePage(items=[MessageOut.model_validate(r, from_attributes=True) for r in rows],
                       total=total or 0)


class TraceEventOut(BaseModel):
    trace_id: str
    message_id: str | None
    agent_id: str
    event: str
    attrs: dict[str, Any]
    ts: datetime


@router.get("/traces/{trace_id}", response_model=list[TraceEventOut])
async def get_trace(trace_id: str, state: AppState = Depends(get_state)) -> list[TraceEventOut]:
    async with state.sessions() as s:
        rows = (await s.scalars(
            select(TraceEvent).where(TraceEvent.trace_id == trace_id).order_by(TraceEvent.ts, TraceEvent.id)
        )).all()
    return [TraceEventOut.model_validate(r, from_attributes=True) for r in rows]


@router.get("/agents", response_model=list[AgentCard])
async def list_agents(state: AppState = Depends(get_state)) -> list[AgentCard]:
    return await state.infra.registry.list_agents()


@router.get("/infra")
async def infra_status(state: AppState = Depends(get_state)) -> dict[str, Any]:
    infra = state.infra
    result = {}
    for name, port in (("registry", infra.registry), ("broker", infra.broker),
                       ("identity", infra.identity), ("tracer", infra.tracer)):
        try:
            ok = await port.ping()
        except Exception:  # noqa: BLE001
            ok = False
        result[name] = {"mode": infra.modes[name], "ok": ok}
    return result


@router.get("/metrics", response_model=Metrics)
async def metrics(
    window_minutes: int = Query(default=60, ge=5, le=24 * 60), state: AppState = Depends(get_state)
) -> Metrics:
    return await compute_metrics(state.sessions, window_minutes)


class NetworkNode(BaseModel):
    agent_id: str
    domain: str
    capabilities: list[str]
    endpoint: str
    status: str
    is_self: bool
    source: str  # self | registry | cache (registry unreachable: last known card)


class NetworkEdge(BaseModel):
    peer: str
    direction: str  # outbound: this agent called the peer; inbound: the peer called this agent
    messages: int
    failed: int
    avg_latency_ms: float | None
    last_at: datetime


class Network(BaseModel):
    window_minutes: int
    registry_ok: bool
    nodes: list[NetworkNode]
    edges: list[NetworkEdge]


@router.get("/network", response_model=Network)
async def network(
    window_minutes: int = Query(default=60, ge=5, le=24 * 60), state: AppState = Depends(get_state)
) -> Network:
    me = state.card()
    nodes = [NetworkNode(**me.model_dump(exclude={"group_id", "version"}), is_self=True, source="self")]
    registry_ok = True
    try:
        cards = [(c, "registry") for c in await state.infra.registry.list_agents() if c.agent_id != me.agent_id]
    except Exception:  # noqa: BLE001 — show what is known instead
        registry_ok = False
        async with state.sessions() as s:
            cached = (await s.scalars(select(AgentCache))).all()
        cards = [(AgentCard.model_validate(c.card), "cache") for c in cached]
    nodes += [
        NetworkNode(**c.model_dump(exclude={"group_id", "version"}), is_self=False, source=source)
        for c, source in cards
    ]

    peer = case((Message.direction == "outbound", Message.to_agent), else_=Message.from_agent).label("peer")
    since = datetime.now(UTC) - timedelta(minutes=window_minutes)
    async with state.sessions() as s:
        rows = (
            await s.execute(
                select(
                    peer,
                    Message.direction,
                    func.count(),
                    func.count().filter(Message.status == "failed"),
                    func.avg(Message.latency_ms),
                    func.max(Message.updated_at),
                )
                .where(Message.type == "request", Message.created_at >= since)
                .group_by(peer, Message.direction)
            )
        ).all()
    edges = [
        NetworkEdge(peer=p, direction=d, messages=n, failed=f, avg_latency_ms=round(a, 1) if a else None, last_at=t)
        for p, d, n, f, a, t in rows
        if p != me.agent_id
    ]
    return Network(window_minutes=window_minutes, registry_ok=registry_ok, nodes=nodes, edges=edges)
