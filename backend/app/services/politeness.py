"""Crawl lịch sự (spec §8): tôn trọng robots.txt và giãn cách request tới cùng một host.

Giãn cách dùng Redis nên áp dụng chung cho mọi processor/crawler đang chạy.
"""

from __future__ import annotations

import asyncio
import logging
import time
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from redis.asyncio import Redis

log = logging.getLogger(__name__)

ROBOTS_TTL_S = 3600


class Politeness:
    def __init__(
        self, redis: Redis, http: httpx.AsyncClient, user_agent: str, min_interval_ms: int = 1500
    ) -> None:
        self._r = redis
        self._http = http
        self._ua = user_agent
        self._min_interval_ms = min_interval_ms
        self._robots: dict[str, tuple[float, RobotFileParser | None]] = {}

    async def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        cached = self._robots.get(origin)
        if cached is None or cached[0] < time.monotonic():
            parser: RobotFileParser | None = None
            try:
                resp = await self._http.get(origin + "/robots.txt", timeout=10)
                if resp.status_code == 200:
                    parser = RobotFileParser()
                    parser.parse(resp.text.splitlines())
            except httpx.HTTPError:
                log.info("robots.txt unavailable for %s, assuming allowed", origin)
            cached = (time.monotonic() + ROBOTS_TTL_S, parser)
            self._robots[origin] = cached
        parser = cached[1]
        return True if parser is None else parser.can_fetch(self._ua, url)

    async def wait_turn(self, url: str) -> None:
        """Chờ tới lượt request host này (tối đa 1 request / min_interval_ms trên toàn hệ thống)."""
        key = f"rate:host:{urlsplit(url).netloc}"
        while not await self._r.set(key, 1, nx=True, px=self._min_interval_ms):
            ttl_ms = await self._r.pttl(key)
            await asyncio.sleep(max(ttl_ms, 50) / 1000)
