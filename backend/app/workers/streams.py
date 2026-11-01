"""Redis Streams helper cho pipeline nội bộ (spec §7, §8).

At-least-once: message chỉ XACK sau khi handler thành công. Message lỗi nằm lại trong pending list
và được nhận lại bằng XAUTOCLAIM sau `min_idle_ms` (đóng vai backoff). Lỗi `max_attempts` lần → DLQ.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import ResponseError

log = logging.getLogger(__name__)

STREAM_CRAWL_JOBS = "crawl.jobs"
STREAM_NEWS_RAW = "news.raw"
STREAM_NEWS_PROCESSED = "news.processed"
STREAM_NEWS_DLQ = "news.dlq"

MAXLEN = 10_000

Handler = Callable[[dict[str, Any]], Awaitable[None]]


async def publish(redis: Redis, stream: str, data: dict[str, Any]) -> str:
    msg_id = await redis.xadd(
        stream, {"data": json.dumps(data, ensure_ascii=False, default=str)}, maxlen=MAXLEN, approximate=True
    )
    return msg_id.decode() if isinstance(msg_id, bytes) else str(msg_id)


def _decode(fields: dict[Any, Any]) -> dict[str, Any]:
    raw = fields.get("data") or fields.get(b"data")
    if isinstance(raw, bytes):
        raw = raw.decode()
    return json.loads(raw) if raw else {}


class StreamConsumer:
    def __init__(
        self,
        redis: Redis,
        *,
        stream: str,
        group: str,
        consumer: str,
        handler: Handler,
        dlq_stream: str = STREAM_NEWS_DLQ,
        max_attempts: int = 3,
        min_idle_ms: int = 60_000,
        block_ms: int = 5_000,
        batch: int = 10,
    ) -> None:
        self._r = redis
        self.stream = stream
        self.group = group
        self.consumer = consumer
        self._handler = handler
        self._dlq = dlq_stream
        self._max_attempts = max_attempts
        self._min_idle_ms = min_idle_ms
        self._block_ms = block_ms
        self._batch = batch

    async def ensure_group(self) -> None:
        try:
            await self._r.xgroup_create(self.stream, self.group, id="0", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def run_once(self) -> int:
        """Xử lý một lô: ưu tiên message bị bỏ dở (pending quá hạn), sau đó message mới."""
        reclaimed = await self._r.xautoclaim(
            self.stream, self.group, self.consumer,
            min_idle_time=self._min_idle_ms, start_id="0-0", count=self._batch,
        )
        entries = reclaimed[1]
        if not entries:
            resp = await self._r.xreadgroup(
                self.group, self.consumer, {self.stream: ">"}, count=self._batch, block=self._block_ms
            )
            entries = resp[0][1] if resp else []
        for msg_id, fields in entries:
            await self._process(msg_id, fields)
        return len(entries)

    async def run_forever(self, stop: asyncio.Event) -> None:
        await self.ensure_group()
        log.info("consumer %s/%s started on %s", self.group, self.consumer, self.stream)
        while not stop.is_set():
            try:
                await self.run_once()
            except Exception:  # noqa: BLE001 — Redis chập chờn: chờ rồi thử lại
                log.exception("consumer loop error")
                await asyncio.sleep(2)

    async def _process(self, msg_id: Any, fields: dict[Any, Any] | None) -> None:
        mid = msg_id.decode() if isinstance(msg_id, bytes) else str(msg_id)
        if fields is None:  # entry đã bị trim khỏi stream
            await self._r.xack(self.stream, self.group, mid)
            return
        data = _decode(fields)
        try:
            await self._handler(data)
        except Exception as exc:  # noqa: BLE001
            key = f"stream:attempts:{self.stream}:{mid}"
            attempts = int(await self._r.incr(key))
            await self._r.expire(key, 86_400)
            log.warning("handler failed (%s/%s) for %s: %s", attempts, self._max_attempts, mid, exc)
            if attempts >= self._max_attempts:
                await publish(self._r, self._dlq, {
                    "stream": self.stream, "message_id": mid, "data": data,
                    "error": repr(exc), "attempts": attempts,
                })
                await self._r.xack(self.stream, self.group, mid)
                await self._r.delete(key)
            return
        await self._r.xack(self.stream, self.group, mid)
