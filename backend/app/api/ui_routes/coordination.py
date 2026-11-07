"""REST for the dashboard: ask a question, inspect what the agent has learned."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.agent.coordinator import AskResult
from app.api.deps import get_state
from app.state import AppState

router = APIRouter(prefix="/api/v1", tags=["coordination"])


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)


@router.post("/ask", response_model=AskResult)
async def ask(body: AskRequest, state: AppState = Depends(get_state)) -> AskResult:
    return await state.coordinator.ask(body.question.strip())


class KnowledgeOut(BaseModel):
    id: int
    key: str
    content: dict[str, Any]
    source_agent: str
    source_message_id: str | None
    version: int
    confidence: float
    created_at: datetime
    expires_at: datetime | None
    superseded_by: int | None
    conflict_group: UUID | None


@router.get("/memory", response_model=list[KnowledgeOut])
async def list_memory(
    limit: int = Query(default=100, ge=1, le=500),
    include_superseded: bool = False,
    state: AppState = Depends(get_state),
) -> list[KnowledgeOut]:
    items = await state.memory.list_items(limit=limit, include_superseded=include_superseded)
    return [KnowledgeOut.model_validate(i, from_attributes=True) for i in items]


@router.get("/memory/{key}/versions", response_model=list[KnowledgeOut])
async def memory_versions(key: str, state: AppState = Depends(get_state)) -> list[KnowledgeOut]:
    return [KnowledgeOut.model_validate(i, from_attributes=True) for i in await state.memory.versions(key)]
