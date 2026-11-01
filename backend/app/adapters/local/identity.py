"""Identity local: token HMAC-SHA256 ký bằng LOCAL_SHARED_SECRET (dùng chung giữa các agent khi chạy local).

Định dạng token: base64url(json{"sub", "exp"}) + "." + base64url(hmac)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from app.contracts import Principal


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


class LocalIdentity:
    def __init__(self, secret: str, ttl_s: int = 3600) -> None:
        self._secret = secret.encode()
        self._ttl_s = ttl_s

    def sign(self, agent_id: str, expires_at: float) -> str:
        body = _b64(json.dumps({"sub": agent_id, "exp": expires_at}, separators=(",", ":")).encode())
        sig = _b64(hmac.new(self._secret, body.encode(), hashlib.sha256).digest())
        return f"{body}.{sig}"

    async def issue_token(self, agent_id: str) -> str:
        return self.sign(agent_id, time.time() + self._ttl_s)

    async def validate(self, token: str) -> Principal | None:
        try:
            body, sig = token.split(".", 1)
            expected = _b64(hmac.new(self._secret, body.encode(), hashlib.sha256).digest())
            if not hmac.compare_digest(sig, expected):
                return None
            claims = json.loads(_unb64(body))
            principal = Principal(agent_id=claims["sub"], expires_at=float(claims["exp"]))
        except (ValueError, KeyError, TypeError):
            return None
        if principal.expires_at < time.time():
            return None
        return principal

    async def ping(self) -> bool:
        return True
