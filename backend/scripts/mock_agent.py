"""A stand-in for other teams' agents (Finance, Weather), speaking the real agent protocol.

All data is made up and marked `"mock": true`. Configured by environment variables:

  MOCK_AGENT_ID=mock-finance-a  MOCK_CAPABILITIES=stock_price  MOCK_PRICE_BIAS=0.02  MOCK_CONFIDENCE=0.7
  uvicorn scripts.mock_agent:app --port 9001

POST /fault injects failures, so that retry and failover can be demonstrated:
  {"delay_s": 10}          answer slowly
  {"fail_status": 503}     answer with this HTTP status
  {"fail_times": 1}        with fail_status: fail only this many times, then recover
  {}                       back to normal
GET /stats shows what the agent received, including how many times each message_id arrived.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from app.adapters.local.identity import LocalIdentity
from app.contracts import AgentCard, Envelope

BASE_URLS = "http://{agent_id}:9001"


class Fault(BaseModel):
    delay_s: float = 0.0
    fail_status: int | None = None
    fail_times: int | None = None


@dataclass
class MockState:
    fault: Fault = field(default_factory=Fault)
    seen: dict[str, int] = field(default_factory=dict)  # message_id -> times received
    replies: dict[str, dict[str, Any]] = field(default_factory=dict)  # dedup by message_id
    handled: int = 0


def _stock_price(payload: dict[str, Any], *, bias: float, confidence: float) -> dict[str, Any]:
    symbol = str(payload.get("symbol", "")).upper()
    if not symbol:
        raise ValueError("symbol is required")
    base = 15_000 + sum(ord(c) for c in symbol) * 97
    return {
        "symbol": symbol,
        "price": round(base * (1 + bias)),
        "currency": "VND",
        "as_of": time.time(),
        "confidence": confidence,
        "mock": True,
    }


def _weather(payload: dict[str, Any], *, bias: float, confidence: float) -> dict[str, Any]:
    location = str(payload.get("location", "")).strip()
    if not location:
        raise ValueError("location is required")
    return {
        "location": location,
        "temperature_c": 20 + sum(ord(c) for c in location) % 15,
        "condition": "nhiều mây",
        "as_of": time.time(),
        "confidence": confidence,
        "mock": True,
    }


HANDLERS = {"stock_price": _stock_price, "weather": _weather}


def create_mock_app(
    *,
    agent_id: str,
    capabilities: list[str],
    secret: str,
    price_bias: float = 0.0,
    confidence: float = 0.8,
    endpoint: str | None = None,
) -> FastAPI:
    app = FastAPI(title=agent_id)
    state = MockState()
    identity = LocalIdentity(secret)
    app.state.mock = state

    @app.get("/card")
    async def card() -> AgentCard:
        return AgentCard(
            agent_id=agent_id,
            group_id="mock",
            domain="mock",
            capabilities=capabilities,
            endpoint=endpoint or BASE_URLS.format(agent_id=agent_id),
        )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "online", "agent_id": agent_id}

    @app.post("/fault")
    async def set_fault(fault: Fault) -> Fault:
        state.fault = fault
        return fault

    @app.get("/stats")
    async def stats() -> dict[str, Any]:
        return {"handled": state.handled, "seen": state.seen}

    @app.post("/messages")
    async def messages(request: Request) -> JSONResponse:
        try:
            env = Envelope.model_validate(await request.json())
        except (ValidationError, ValueError) as exc:
            return JSONResponse({"error": "bad_request", "message": str(exc)}, status_code=400)

        state.seen[env.message_id] = state.seen.get(env.message_id, 0) + 1

        principal = await identity.validate(env.token or "")
        if principal is None or principal.agent_id != env.from_:
            reply = env.error_reply(sender=agent_id, code="unauthorized", message="invalid token")
            return JSONResponse(reply.wire(), status_code=401)

        fault = state.fault
        if fault.delay_s:
            await asyncio.sleep(fault.delay_s)
        if fault.fail_status is not None and (fault.fail_times is None or fault.fail_times > 0):
            if fault.fail_times is not None:
                fault.fail_times -= 1
            reply = env.error_reply(sender=agent_id, code="unavailable", message="injected fault")
            return JSONResponse(reply.wire(), status_code=fault.fail_status)

        if env.message_id in state.replies:  # a retry: answer the same without handling it again
            return JSONResponse(state.replies[env.message_id])

        handler = HANDLERS.get(env.capability or "")
        if handler is None or env.capability not in capabilities:
            reply = env.error_reply(sender=agent_id, code="bad_request", message=f"unknown: {env.capability}")
            return JSONResponse(reply.wire(), status_code=400)
        try:
            data = handler(env.payload, bias=price_bias, confidence=confidence)
        except ValueError as exc:
            reply = env.error_reply(sender=agent_id, code="bad_request", message=str(exc))
            return JSONResponse(reply.wire(), status_code=400)

        state.handled += 1
        wire = env.reply(sender=agent_id, payload=data).wire()
        state.replies[env.message_id] = wire
        return JSONResponse(wire)

    return app


app = create_mock_app(
    agent_id=os.environ.get("MOCK_AGENT_ID", "mock-agent"),
    capabilities=[c for c in os.environ.get("MOCK_CAPABILITIES", "stock_price").split(",") if c],
    secret=os.environ.get("LOCAL_SHARED_SECRET", "dev-shared-secret"),
    price_bias=float(os.environ.get("MOCK_PRICE_BIAS", "0")),
    confidence=float(os.environ.get("MOCK_CONFIDENCE", "0.8")),
)
