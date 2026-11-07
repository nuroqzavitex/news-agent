"""AgentClient: call another agent's capability, surviving what the network and other agents do (spec §9).

- Discovery: Redis cache, then the Registry; if the Registry is down, the last known cards from agent_cache.
- Retry with backoff, keeping the same message_id so the receiver can deduplicate.
- Fan-out to several agents in parallel; failover to the remaining agents when all of those fail.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.handler import MessageLog
from app.contracts import AgentCard, Envelope, new_id
from app.db.models import AgentCache
from app.ports import BrokerPort, IdentityPort, InfraError, RegistryPort
from app.services.tracing import TraceRecorder

log = logging.getLogger(__name__)

BACKOFF_S = (0.5, 1.0, 2.0)
DISCOVERY_CACHE_TTL_S = 60


@dataclass
class AgentReply:
    agent_id: str
    payload: dict[str, Any]
    message_id: str
    attempts: int
    latency_ms: int


@dataclass
class AgentFailure:
    agent_id: str
    error: str
    attempts: int


@dataclass
class CallOutcome:
    capability: str
    replies: list[AgentReply] = field(default_factory=list)
    failures: list[AgentFailure] = field(default_factory=list)
    no_agents: bool = False


class AgentClient:
    def __init__(
        self,
        *,
        agent_id: str,
        registry: RegistryPort,
        broker: BrokerPort,
        identity: IdentityPort,
        tracer: TraceRecorder,
        message_log: MessageLog,
        sessions: async_sessionmaker[AsyncSession],
        redis: Redis,
        call_timeout_s: float = 2.5,
        retries: int = 1,
        deadline_s: float = 12.0,
    ) -> None:
        self.agent_id = agent_id
        self._registry = registry
        self._broker = broker
        self._identity = identity
        self._trace = tracer
        self._log = message_log
        self._sessions = sessions
        self._redis = redis
        self._call_timeout = call_timeout_s
        self._retries = retries
        self._deadline = deadline_s

    async def discover(self, capability: str, *, trace_id: str) -> list[AgentCard]:
        """Agents offering a capability, excluding this agent. Never raises because of the Registry."""
        key = f"cache:cap:{capability}"
        try:
            cached = await self._redis.get(key)
            if cached:
                return [AgentCard.model_validate(c) for c in json.loads(cached)]
        except Exception:  # noqa: BLE001 — the cache is optional
            log.debug("discovery cache read failed", exc_info=True)

        try:
            cards = [c for c in await self._registry.find_by_capability(capability) if c.agent_id != self.agent_id]
        except Exception as exc:  # noqa: BLE001 — Registry adapters may raise anything network-related
            log.warning("registry lookup failed, using agent_cache: %s", exc)
            cards = await self._cached_cards(capability)
            await self._trace.event(
                "registry.fallback_cache", trace_id=trace_id, capability=capability, agents=[c.agent_id for c in cards]
            )
            return cards

        await self._trace.event(
            "client.discover", trace_id=trace_id, capability=capability, agents=[c.agent_id for c in cards]
        )
        await self._remember_cards(key, cards)
        return cards

    async def known_capabilities(self) -> set[str]:
        """Capabilities other agents offer, for the planner. Empty when the Registry is unreachable."""
        try:
            agents = await self._registry.list_agents()
        except Exception as exc:  # noqa: BLE001
            log.warning("registry list failed: %s", exc)
            return set()
        return {c for a in agents if a.agent_id != self.agent_id for c in a.capabilities}

    async def call(
        self,
        capability: str,
        payload: dict[str, Any],
        *,
        trace_id: str,
        conversation_id: str | None = None,
        fan_out: int = 1,
    ) -> CallOutcome:
        """Ask up to `fan_out` agents in parallel. When none of them answers, try the remaining agents one by one."""
        outcome = CallOutcome(capability)
        agents = await self.discover(capability, trace_id=trace_id)
        if not agents:
            outcome.no_agents = True
            return outcome

        conversation = conversation_id or new_id("conv")
        deadline = time.monotonic() + self._deadline

        async def ask(agent: AgentCard) -> AgentReply | AgentFailure:
            return await self._ask_with_retry(agent, capability, payload, trace_id, conversation, deadline)

        for result in await asyncio.gather(*(ask(a) for a in agents[:fan_out])):
            self._collect(outcome, result)
        for agent in agents[fan_out:]:
            if outcome.replies:
                break
            await self._trace.event(
                "client.failover", trace_id=trace_id, capability=capability, to_agent=agent.agent_id,
                failed=[f.agent_id for f in outcome.failures],
            )
            self._collect(outcome, await ask(agent))
        return outcome

    @staticmethod
    def _collect(outcome: CallOutcome, result: AgentReply | AgentFailure) -> None:
        if isinstance(result, AgentReply):
            outcome.replies.append(result)
        else:
            outcome.failures.append(result)

    async def _ask_with_retry(
        self,
        agent: AgentCard,
        capability: str,
        payload: dict[str, Any],
        trace_id: str,
        conversation_id: str,
        deadline: float,
    ) -> AgentReply | AgentFailure:
        started = time.monotonic()
        envelope = Envelope(
            from_=self.agent_id,
            to=agent.agent_id,
            capability=capability,
            payload=payload,
            trace_id=trace_id,
            conversation_id=conversation_id,
        )
        error = "no attempt made"
        attempts = 0
        for attempt in range(self._retries + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                error = "deadline exceeded"
                break
            attempts = attempt + 1
            try:
                envelope.token = await self._identity.issue_token(self.agent_id)
                await self._trace.event(
                    "broker.send", trace_id=trace_id, message_id=envelope.message_id,
                    to_agent=agent.agent_id, capability=capability, attempt=attempts,
                )
                reply = await self._broker.send(envelope, agent, min(self._call_timeout, remaining))
            except (httpx.TimeoutException, InfraError) as exc:
                error = str(exc) or type(exc).__name__
            except Exception as exc:  # noqa: BLE001 — a failing identity or broker must only fail this one call
                log.warning("sending to %s failed", agent.agent_id, exc_info=True)
                error = str(exc) or type(exc).__name__
            else:
                if reply.reply_to != envelope.message_id:
                    error = f"reply_to {reply.reply_to} does not match the request"
                elif reply.type == "error":
                    # The agent understood and refused (bad request, not authorized): asking again will not help.
                    error = str(reply.payload.get("message") or reply.payload.get("error") or "error reply")
                    break
                else:
                    latency_ms = int((time.monotonic() - started) * 1000)
                    await self._record(envelope, "delivered", attempts, latency_ms, None)
                    await self._record_reply(reply)
                    return AgentReply(agent.agent_id, reply.payload, envelope.message_id, attempts, latency_ms)

            if attempt < self._retries:
                delay = BACKOFF_S[min(attempt, len(BACKOFF_S) - 1)]
                await self._trace.event(
                    "broker.retry", trace_id=trace_id, message_id=envelope.message_id,
                    to_agent=agent.agent_id, attempt=attempts, delay_s=delay, error=error,
                )
                await asyncio.sleep(max(0.0, min(delay, deadline - time.monotonic())))

        await self._record(envelope, "failed", attempts, int((time.monotonic() - started) * 1000), error)
        return AgentFailure(agent.agent_id, error, attempts)

    async def _record(
        self, envelope: Envelope, status: str, attempts: int, latency_ms: int, error: str | None
    ) -> None:
        try:
            await self._log.record(
                envelope, direction="outbound", status=status, latency_ms=latency_ms, error=error, attempts=attempts
            )
        except Exception:  # noqa: BLE001 — losing a log line must not lose the answer
            log.warning("message log failed for %s", envelope.message_id, exc_info=True)

    async def _record_reply(self, reply: Envelope) -> None:
        try:
            await self._log.record(reply, direction="inbound", status="received")
        except Exception:  # noqa: BLE001
            log.warning("message log failed for %s", reply.message_id, exc_info=True)

    async def _remember_cards(self, key: str, cards: list[AgentCard]) -> None:
        try:
            await self._redis.set(key, json.dumps([c.model_dump() for c in cards]), ex=DISCOVERY_CACHE_TTL_S)
        except Exception:  # noqa: BLE001
            log.debug("discovery cache write failed", exc_info=True)
        if not cards:
            return
        try:
            stmt = insert(AgentCache).values(
                [{"agent_id": c.agent_id, "card": c.model_dump(), "source": "registry"} for c in cards]
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=[AgentCache.agent_id],
                set_={"card": stmt.excluded.card, "source": stmt.excluded.source, "last_seen_at": func.now()},
            )
            async with self._sessions() as s:
                await s.execute(stmt)
                await s.commit()
        except Exception:  # noqa: BLE001
            log.warning("agent_cache write failed", exc_info=True)

    async def _cached_cards(self, capability: str) -> list[AgentCard]:
        try:
            async with self._sessions() as s:
                rows = (await s.scalars(select(AgentCache).order_by(AgentCache.last_seen_at.desc()))).all()
        except Exception:  # noqa: BLE001
            log.warning("agent_cache read failed", exc_info=True)
            return []
        cards = [AgentCard.model_validate(r.card) for r in rows]
        return [c for c in cards if capability in c.capabilities and c.agent_id != self.agent_id]
