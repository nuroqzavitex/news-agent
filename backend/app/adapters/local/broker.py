"""Broker local: gửi HTTP trực tiếp tới `{endpoint}/messages` của agent đích."""

from __future__ import annotations

import httpx

from app.contracts import AgentCard, Envelope
from app.ports import InfraError


class DirectHttpBroker:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def send(self, envelope: Envelope, target: AgentCard, timeout_s: float) -> Envelope:
        url = target.endpoint.rstrip("/") + "/messages"
        try:
            resp = await self._client.post(url, json=envelope.wire(), timeout=timeout_s)
        except httpx.TimeoutException:
            raise
        except httpx.HTTPError as exc:
            raise InfraError(f"cannot reach {target.agent_id}: {exc}") from exc
        if resp.status_code >= 500:
            raise InfraError(f"{target.agent_id} returned {resp.status_code}")
        return Envelope.model_validate(resp.json())

    async def ping(self) -> bool:
        return True
