"""Agent protocol (spec §6.2): GET /card, GET /health, POST /messages."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.contracts import AgentCard
from app.services.health import compute_health
from app.state import AppState

from .deps import get_state

router = APIRouter(tags=["agent"])


@router.get("/card", response_model=AgentCard)
async def card(state: AppState = Depends(get_state)) -> AgentCard:
    health = await compute_health(state)
    return state.card(status=health["status"])


@router.get("/health")
async def health(state: AppState = Depends(get_state)) -> JSONResponse:
    data = await compute_health(state)
    return JSONResponse(data, status_code=503 if data["status"] == "offline" else 200)


@router.post("/messages")
async def messages(request: Request, state: AppState = Depends(get_state)) -> JSONResponse:
    faults = await state.faults.get()
    if faults.inbound_delay_s:
        await asyncio.sleep(faults.inbound_delay_s)
    if faults.inbound_fail_status:
        return JSONResponse(
            {"error": "unavailable", "message": "fault injected"}, status_code=faults.inbound_fail_status
        )
    raw: Any
    try:
        raw = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        raw = None
    result = await state.handler.handle(raw)
    return JSONResponse(result.envelope.wire(), status_code=result.status_code)
