"""Helpers shared by capabilities."""

from __future__ import annotations

import re
from datetime import timedelta

from app.agent.capabilities import CapabilityContext
from app.services.llm import GeminiClient

WINDOW_PATTERN = r"^\d{1,3}[hd]$"
_WINDOW_RE = re.compile(r"^(\d{1,3})([hd])$")


def parse_window(window: str) -> timedelta:
    """'24h' or '7d' to a timedelta. The payload models have already validated the format."""
    match = _WINDOW_RE.match(window)
    if match is None:
        raise ValueError(f"invalid window: {window}")
    amount, unit = int(match[1]), match[2]
    return timedelta(hours=amount) if unit == "h" else timedelta(days=amount)


async def trace_llm_fallback(ctx: CapabilityContext, capability: str, reason: str) -> None:
    tracer = ctx.deps.get("tracer")
    if tracer is not None:
        await tracer.event(
            "llm.fallback",
            trace_id=ctx.envelope.trace_id,
            message_id=ctx.envelope.message_id,
            capability=capability,
            reason=reason,
        )


async def trace_llm_call(ctx: CapabilityContext, capability: str, llm: GeminiClient) -> None:
    tracer = ctx.deps.get("tracer")
    if tracer is not None and llm.configured:
        await tracer.event(
            "llm.call",
            trace_id=ctx.envelope.trace_id,
            message_id=ctx.envelope.message_id,
            capability=capability,
            model=llm.llm_model,
        )
