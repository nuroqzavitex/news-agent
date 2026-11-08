"""Tính trạng thái agent (spec §9): online / degraded / offline."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, text

from app.db.models import Source
from app.services.llm import LAST_ERROR_KEY
from app.state import AppState

PING_TIMEOUT_S = 2.0


async def _check(coro: Any) -> bool:
    try:
        return bool(await asyncio.wait_for(coro, PING_TIMEOUT_S))
    except Exception:  # noqa: BLE001
        return False


async def _llm_status(state: AppState) -> str:
    if not state.settings.gemini_api_key:
        return "not_configured"
    try:
        error = await asyncio.wait_for(state.redis.get(LAST_ERROR_KEY), PING_TIMEOUT_S)
    except Exception:  # noqa: BLE001 — Redis trouble is reported separately
        return "unknown"
    if error is None:
        return "ok"
    return f"error ({error.decode() if isinstance(error, bytes) else error})"


async def compute_health(state: AppState) -> dict[str, Any]:
    deps: dict[str, str] = {}

    db_ok = False
    last_crawl: datetime | None = None
    try:
        async with state.sessions() as s:
            await asyncio.wait_for(s.execute(text("SELECT 1")), PING_TIMEOUT_S)
            last_crawl = await s.scalar(select(func.max(Source.last_crawled_at)))
        db_ok = True
    except Exception:  # noqa: BLE001
        pass
    deps["db"] = "ok" if db_ok else "down"
    deps["redis"] = "ok" if await _check(state.redis.ping()) else "down"

    if not db_ok:
        deps["crawler"] = "unknown"
    elif last_crawl is None:
        deps["crawler"] = "never_ran"
    else:
        age = (datetime.now(UTC) - last_crawl).total_seconds()
        deps["crawler"] = "ok" if age <= state.settings.crawler_stale_after_s else f"stale ({int(age)}s)"

    deps["llm"] = await _llm_status(state)

    infra = state.infra
    for name, port in (("registry", infra.registry), ("broker", infra.broker),
                       ("identity", infra.identity), ("tracer", infra.tracer)):
        deps[name] = "ok" if await _check(port.ping()) else "down"

    if not db_ok:
        status = "offline"
    elif deps["llm"].startswith("error") or any(
        deps[k] != "ok" for k in ("redis", "crawler", "registry", "broker", "identity", "tracer")
    ):
        status = "degraded"
    else:
        status = "online"

    return {
        "status": status,
        "agent_id": state.settings.agent_id,
        "uptime_s": int(time.time() - state.started_at),
        "freshness": last_crawl.isoformat() if last_crawl else None,
        "deps": deps,
        "modes": infra.modes,
        "faults": (await state.faults.get()).active(),
    }
