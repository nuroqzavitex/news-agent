"""REST cho dashboard: nhật ký message, trace, trạng thái hạ tầng, danh sách agent."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select

from app.api.deps import get_state
from app.contracts import AgentCard
from app.db.models import Message, TraceEvent
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
