"""Keeps the agent accepting known tokens while the identity service is down (spec §9).

Successful validations are cached for 60 seconds (never past the token's expiry). When the identity service
fails, a token validated recently is still accepted; an unknown token is still rejected.
"""

from __future__ import annotations

import hashlib
import logging
import time

from redis.asyncio import Redis

from app.contracts import Principal
from app.ports import IdentityPort

log = logging.getLogger(__name__)

CACHE_TTL_S = 60


class CachingIdentity:
    def __init__(self, inner: IdentityPort, redis: Redis, ttl_s: int = CACHE_TTL_S) -> None:
        self._inner = inner
        self._redis = redis
        self._ttl_s = ttl_s

    @staticmethod
    def _key(token: str) -> str:
        return "identity:valid:" + hashlib.sha256(token.encode()).hexdigest()

    async def issue_token(self, agent_id: str) -> str:
        return await self._inner.issue_token(agent_id)

    async def validate(self, token: str) -> Principal | None:
        try:
            principal = await self._inner.validate(token)
        except Exception:
            cached = await self._cached(token)
            if cached is None:
                raise
            log.warning("identity service failed; accepted %s from the validation cache", cached.agent_id)
            return cached
        if principal is not None:
            ttl = min(self._ttl_s, int(principal.expires_at - time.time()))
            if ttl > 0:
                try:
                    await self._redis.set(self._key(token), principal.model_dump_json(), ex=ttl)
                except Exception:  # noqa: BLE001 — the cache is optional
                    log.debug("identity cache write failed", exc_info=True)
        return principal

    async def _cached(self, token: str) -> Principal | None:
        try:
            raw = await self._redis.get(self._key(token))
        except Exception:  # noqa: BLE001
            return None
        if raw is None:
            return None
        principal = Principal.model_validate_json(raw)
        return principal if principal.expires_at > time.time() else None

    async def ping(self) -> bool:
        return await self._inner.ping()
