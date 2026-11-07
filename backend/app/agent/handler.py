"""Xử lý message đến (spec §6.3):

validate envelope → xác thực token, principal == from → dedup theo message_id
→ dispatch capability (có timeout) → lưu message + reply → trace → trả reply.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import ValidationError

from app.agent.capabilities import CapabilityContext, CapabilityRegistry
from app.agent.dedup import Dedup, DuplicateInProgress
from app.contracts import Envelope, ErrorCode, new_id
from app.ports import IdentityPort
from app.services.tracing import TraceRecorder

log = logging.getLogger(__name__)

HTTP_STATUS: dict[str, int] = {
    "bad_request": 400,
    "unauthorized": 401,
    "internal": 500,
    "unavailable": 503,
    "timeout": 504,
}


def _errs(exc: ValidationError) -> list[dict[str, Any]]:
    return [dict(e) for e in exc.errors(include_url=False, include_context=False, include_input=False)]


def http_status_for(envelope: Envelope) -> int:
    if envelope.type != "error":
        return 200
    return HTTP_STATUS.get(str(envelope.payload.get("error")), 500)


class MessageLog(Protocol):
    async def record(
        self,
        envelope: Envelope,
        *,
        direction: str,
        status: str,
        latency_ms: int | None = None,
        error: str | None = None,
        attempts: int | None = None,
    ) -> None: ...


@dataclass
class HandleResult:
    status_code: int
    envelope: Envelope


class MessageHandler:
    def __init__(
        self,
        *,
        agent_id: str,
        identity: IdentityPort,
        dedup: Dedup,
        capabilities: CapabilityRegistry,
        message_log: MessageLog,
        tracer: TraceRecorder,
        capability_timeout_s: float,
        deps: dict[str, Any] | None = None,
    ) -> None:
        self.agent_id = agent_id
        self._identity = identity
        self._dedup = dedup
        self._caps = capabilities
        self._log = message_log
        self._trace = tracer
        self._timeout = capability_timeout_s
        self._deps = deps or {}

    async def handle(self, raw: Any) -> HandleResult:
        started = time.monotonic()
        try:
            env = Envelope.model_validate(raw)
        except ValidationError as exc:
            return self._malformed(raw, exc)

        await self._trace.event("msg.received", trace_id=env.trace_id, message_id=env.message_id,
                                from_agent=env.from_, capability=env.capability)

        # 1. Xác thực
        rejection = await self._authenticate(env)
        if rejection is not None:
            await self._trace.event("msg.rejected", trace_id=env.trace_id, message_id=env.message_id,
                                    reason=rejection)
            await self._safe_record(env, direction="inbound", status="failed", error=rejection)
            return self._result(env.error_reply(sender=self.agent_id, code="unauthorized", message=rejection))

        if env.type != "request":
            return self._result(env.error_reply(sender=self.agent_id, code="bad_request",
                                                message=f"chỉ nhận type=request, nhận được {env.type}"))

        # 2. Idempotency
        try:
            cached = await self._dedup.claim(env.message_id, wait_s=self._timeout + 1)
        except DuplicateInProgress:
            return self._result(env.error_reply(sender=self.agent_id, code="unavailable",
                                                message="message đang được xử lý, thử lại sau"))
        if cached is not None:
            await self._trace.event("msg.duplicate", trace_id=env.trace_id, message_id=env.message_id)
            return self._result(cached)

        # 3. Dispatch
        await self._safe_record(env, direction="inbound", status="processing")
        reply, transient = await self._dispatch(env)
        latency_ms = int((time.monotonic() - started) * 1000)

        if transient:
            await self._dedup.release(env.message_id)
        else:
            await self._dedup.complete(env.message_id, reply)

        error = reply.payload.get("error") if reply.type == "error" else None
        await self._safe_record(env, direction="inbound", status="failed" if error else "replied",
                                latency_ms=latency_ms, error=error)
        await self._safe_record(reply, direction="outbound", status="delivered")
        await self._trace.event("msg.replied", trace_id=env.trace_id, message_id=env.message_id,
                                reply_id=reply.message_id, type=reply.type, latency_ms=latency_ms)
        return self._result(reply)

    async def _authenticate(self, env: Envelope) -> str | None:
        if not env.token:
            return "thiếu token"
        try:
            principal = await self._identity.validate(env.token)
        except Exception:  # noqa: BLE001
            log.warning("identity validate failed", exc_info=True)
            return "không xác thực được token (identity lỗi)"
        if principal is None:
            return "token không hợp lệ hoặc đã hết hạn"
        if principal.agent_id != env.from_:
            return f"token thuộc {principal.agent_id}, không khớp from={env.from_}"
        return None

    async def _dispatch(self, env: Envelope) -> tuple[Envelope, bool]:
        """Trả (reply, transient). transient=True → lỗi tạm thời, không lưu reply để retry xử lý lại."""
        cap = self._caps.get(env.capability or "")
        if cap is None:
            return self._error(env, "bad_request", f"không hỗ trợ capability '{env.capability}'. "
                                                   f"Hỗ trợ: {self._caps.names()}"), False
        try:
            payload = cap.payload_model.model_validate(env.payload)
        except ValidationError as exc:
            return self._error(env, "bad_request", f"payload không hợp lệ: {_errs(exc)}"), False

        ctx = CapabilityContext(envelope=env, deps=self._deps)
        try:
            result = await asyncio.wait_for(cap.handler(payload, ctx), timeout=self._timeout)
        except TimeoutError:
            return self._error(env, "timeout", f"capability vượt quá {self._timeout}s"), True
        except Exception:
            log.exception("capability %s failed", cap.name)
            return self._error(env, "internal", "lỗi nội bộ khi xử lý capability"), True
        return env.reply(sender=self.agent_id, payload=result), False

    def _error(self, env: Envelope, code: ErrorCode, message: str) -> Envelope:
        return env.error_reply(sender=self.agent_id, code=code, message=message)

    def _malformed(self, raw: Any, exc: ValidationError) -> HandleResult:
        raw_dict = raw if isinstance(raw, dict) else {}
        reply = Envelope(
            trace_id=str(raw_dict.get("trace_id") or new_id("tr")),
            conversation_id=str(raw_dict.get("conversation_id") or new_id("conv")),
            from_=self.agent_id,
            to=str(raw_dict.get("from") or "unknown"),
            type="error",
            reply_to=raw_dict.get("message_id"),
            payload={"error": "bad_request", "message": f"envelope không hợp lệ: {_errs(exc)}"},
        )
        return self._result(reply)

    @staticmethod
    def _result(envelope: Envelope) -> HandleResult:
        return HandleResult(http_status_for(envelope), envelope)

    async def _safe_record(self, envelope: Envelope, **kwargs: Any) -> None:
        try:
            await self._log.record(envelope, **kwargs)
        except Exception:  # noqa: BLE001 — mất log không được làm mất reply
            log.warning("message log failed for %s", envelope.message_id, exc_info=True)
