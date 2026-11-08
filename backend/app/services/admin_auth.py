"""Dashboard login: one admin password (ADMIN_PASSWORD), exchanged for a signed session token (spec §6.4)."""

from __future__ import annotations

import hashlib
import hmac

from app.adapters.local.identity import LocalIdentity

SESSION_TTL_S = 12 * 3600
SUBJECT = "dashboard-admin"


class AdminAuth:
    def __init__(self, password: str, secret: str) -> None:
        self._password = password.encode()
        # Changing the password invalidates every existing session.
        key = hashlib.sha256(b"admin-session|" + secret.encode() + b"|" + self._password).hexdigest()
        self._signer = LocalIdentity(key, ttl_s=SESSION_TTL_S)

    def check_password(self, password: str) -> bool:
        return hmac.compare_digest(password.encode(), self._password)

    async def issue(self) -> str:
        return await self._signer.issue_token(SUBJECT)

    async def verify(self, token: str) -> bool:
        principal = await self._signer.validate(token)
        return principal is not None and principal.agent_id == SUBJECT
