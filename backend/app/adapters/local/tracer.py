"""Tracer local: ghi trace event ra log JSON (bản lưu DB do TraceRecorder đảm nhiệm)."""

from __future__ import annotations

import json
import logging
from typing import Any

log = logging.getLogger("trace")


class LogTracer:
    async def emit(self, event: dict[str, Any]) -> None:
        log.info(json.dumps(event, ensure_ascii=False, default=str))

    async def ping(self) -> bool:
        return True
