"""Tính trạng thái agent (spec §9): online / degraded / offline."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, text

from app.db.models import Source
from app.state import AppState

PING_TIMEOUT_S = 2.0


async def _check(coro: Any) -> bool:
    try:
        return bool(await asyncio.wait_for(coro, PING_TIMEOUT_S))
    except Exception:  # noqa: BLE001
        return False


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

    deps["llm"] = "configured" if state.settings.gemini_api_key else "not_configured"

    infra = state.infra
    for name, port in (("registry", infra.registry), ("broker", infra.broker),
                       ("identity", infra.identity), ("tracer", infra.tracer)):
        deps[name] = "ok" if await _check(port.ping()) else "down"

    if not db_ok:
        status = "offline"
    elif any(deps[k] not in ("ok",) for k in ("redis", "crawler", "registry", "broker", "identity", "tracer")):
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
    }
