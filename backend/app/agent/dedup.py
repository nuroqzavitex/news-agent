"""Idempotency theo message_id bằng Redis (spec §6.3).

- Lần đầu: SET dedup:{id} "processing" NX → caller xử lý, rồi complete() lưu reply.
- Lần sau: trả reply đã lưu; nếu bản đầu đang xử lý thì chờ tới khi có reply.
- Marker "processing" có TTL ngắn để replica chết giữa chừng không khóa message mãi.
"""

from __future__ import annotations

import asyncio
import time

from redis.asyncio import Redis

from app.contracts import Envelope

PROCESSING = "__processing__"


class DuplicateInProgress(Exception):
    """Bản gốc vẫn đang xử lý sau khi đã chờ hết thời gian."""


class Dedup:
    def __init__(
        self,
        redis: Redis,
        reply_ttl_s: int = 600,
        processing_ttl_s: int = 30,
        poll_interval_s: float = 0.1,
    ) -> None:
        self._r = redis
        self._reply_ttl = reply_ttl_s
        self._processing_ttl = processing_ttl_s
        self._poll = poll_interval_s

    @staticmethod
    def _key(message_id: str) -> str:
        return f"dedup:{message_id}"

    async def claim(self, message_id: str, wait_s: float) -> Envelope | None:
        """None = message mới, caller sở hữu việc xử lý. Envelope = reply đã lưu của lần trước."""
        key = self._key(message_id)
        deadline = time.monotonic() + wait_s
        while True:
            if await self._r.set(key, PROCESSING, nx=True, ex=self._processing_ttl):
                return None
            value = await self._r.get(key)
            if value is not None:
                text = value.decode() if isinstance(value, bytes) else value
                if text != PROCESSING:
                    return Envelope.model_validate_json(text)
            if time.monotonic() >= deadline:
                raise DuplicateInProgress(message_id)
            await asyncio.sleep(self._poll)

    async def complete(self, message_id: str, reply: Envelope) -> None:
        await self._r.set(self._key(message_id), reply.model_dump_json(by_alias=True), ex=self._reply_ttl)

    async def release(self, message_id: str) -> None:
        """Bỏ claim khi xử lý lỗi tạm thời, để retry của bên gửi được xử lý lại."""
        await self._r.delete(self._key(message_id))
