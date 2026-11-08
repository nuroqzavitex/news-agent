"""Dashboard REST that changes things: login, fault injection, test messages to other agents (spec §6.4)."""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app.agent.client import AgentReply
from app.api.deps import get_state
from app.contracts import AgentCard, new_id
from app.services.admin_auth import SESSION_TTL_S
from app.services.faults import FaultConfig
from app.state import AppState

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


async def require_admin(
    authorization: str | None = Header(default=None), state: AppState = Depends(get_state)
) -> None:
    token = (authorization or "").removeprefix("Bearer ").strip()
    if not token or not await state.admin.verify(token):
        raise HTTPException(401, "đăng nhập để dùng chức năng này", headers={"WWW-Authenticate": "Bearer"})


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=200)


class LoginResponse(BaseModel):
    token: str
    expires_in_s: int


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest, state: AppState = Depends(get_state)) -> LoginResponse:
    if not state.admin.check_password(body.password):
        raise HTTPException(401, "sai mật khẩu")
    return LoginResponse(token=await state.admin.issue(), expires_in_s=SESSION_TTL_S)


@router.get("/session", dependencies=[Depends(require_admin)])
async def session() -> dict[str, bool]:
    return {"ok": True}


class FaultState(BaseModel):
    enabled: bool
    config: FaultConfig
    active: list[str]


@router.get("/faults", response_model=FaultState, dependencies=[Depends(require_admin)])
async def get_faults(state: AppState = Depends(get_state)) -> FaultState:
    config = await state.faults.get()
    return FaultState(enabled=state.faults.enabled, config=config, active=config.active())


@router.put("/faults", response_model=FaultState, dependencies=[Depends(require_admin)])
async def set_faults(config: FaultConfig, state: AppState = Depends(get_state)) -> FaultState:
    if not state.faults.enabled:
        raise HTTPException(403, "fault injection is disabled (FAULTS_ENABLED=false)")
    await state.faults.set(config)
    return FaultState(enabled=True, config=config, active=config.active())


class MockFault(BaseModel):
    delay_s: float = Field(default=0.0, ge=0, le=60)
    fail_status: int | None = Field(default=None, ge=400, le=599)
    fail_times: int | None = Field(default=None, ge=1, le=100)


class MockAgentState(BaseModel):
    agent_id: str
    capabilities: list[str]
    reachable: bool
    fault: MockFault | None


async def _mock_cards(state: AppState) -> list[AgentCard]:
    return [a for a in await state.infra.registry.list_agents() if a.group_id == "mock"]


@router.get("/mocks", response_model=list[MockAgentState], dependencies=[Depends(require_admin)])
async def list_mocks(state: AppState = Depends(get_state)) -> list[MockAgentState]:
    """Mock agents and their current faults. Only mocks (group "mock") can be controlled from here."""
    result = []
    for card in await _mock_cards(state):
        try:
            resp = await state.http.get(card.endpoint.rstrip("/") + "/fault", timeout=2)
            fault = MockFault.model_validate(resp.json()) if resp.status_code == 200 else None
            reachable = resp.status_code == 200
        except (httpx.HTTPError, ValueError):
            fault, reachable = None, False
        result.append(
            MockAgentState(agent_id=card.agent_id, capabilities=card.capabilities, reachable=reachable, fault=fault)
        )
    return result


@router.put("/mocks/{agent_id}/fault", response_model=MockFault, dependencies=[Depends(require_admin)])
async def set_mock_fault(agent_id: str, fault: MockFault, state: AppState = Depends(get_state)) -> MockFault:
    if not state.faults.enabled:
        raise HTTPException(403, "fault injection is disabled (FAULTS_ENABLED=false)")
    card = next((c for c in await _mock_cards(state) if c.agent_id == agent_id), None)
    if card is None:
        raise HTTPException(404, f"{agent_id} is not a known mock agent")
    try:
        resp = await state.http.post(card.endpoint.rstrip("/") + "/fault", json=fault.model_dump(), timeout=3)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"{agent_id} is unreachable: {type(exc).__name__}") from exc
    return MockFault.model_validate(resp.json())


class CallRequest(BaseModel):
    agent_id: str
    capability: str
    payload: dict[str, Any] = {}


class CallResponse(BaseModel):
    ok: bool
    agent_id: str
    trace_id: str
    attempts: int
    latency_ms: int | None
    payload: dict[str, Any] | None
    error: str | None


@router.post("/agents/call", response_model=CallResponse, dependencies=[Depends(require_admin)])
async def call_agent(body: CallRequest, state: AppState = Depends(get_state)) -> CallResponse:
    """Send a test message to one specific agent, as this agent, through the real client (retry included)."""
    try:
        agents = await state.infra.registry.list_agents()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, f"registry unreachable: {exc}") from exc
    card = next((a for a in agents if a.agent_id == body.agent_id), None)
    if card is None:
        raise HTTPException(404, f"{body.agent_id} is not registered")
    trace_id = new_id("tr")
    result = await state.client.call_agent(card, body.capability, body.payload, trace_id=trace_id)
    if isinstance(result, AgentReply):
        return CallResponse(ok=True, agent_id=card.agent_id, trace_id=trace_id, attempts=result.attempts,
                            latency_ms=result.latency_ms, payload=result.payload, error=None)
    return CallResponse(ok=False, agent_id=card.agent_id, trace_id=trace_id, attempts=result.attempts,
                        latency_ms=None, payload=None, error=result.error)
