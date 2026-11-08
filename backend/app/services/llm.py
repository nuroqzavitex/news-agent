"""Gemini REST client. Every Gemini dependency lives in this file: changing provider means changing only this.

Callers must always handle LLMError and fall back to something that works without a model (spec §1.2, §9).
The API key travels in a header, so it can never end up in a logged URL.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx
from redis.asyncio import Redis

from app.db.models import EMBEDDING_DIM
from app.services.faults import FaultSwitch

log = logging.getLogger(__name__)

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

MAX_EMBED_BATCH = 100
LAST_ERROR_KEY = "llm:last_error"
LAST_ERROR_TTL_S = 300

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$")


class LLMError(Exception):
    """The model call failed. Default assumption: temporary (network, quota, wrong key), so retry later."""


class LLMNotConfigured(LLMError):
    """No API key configured."""


class LLMBadRequest(LLMError):
    """The API rejected this input; sending the same input again will fail again."""


class LLMBadOutput(LLMError):
    """The model answered, but not in the shape we asked for (or the answer was blocked)."""


class GeminiClient:
    def __init__(
        self,
        *,
        api_key: str,
        http: httpx.AsyncClient,
        llm_model: str,
        embedding_model: str,
        redis: Redis | None = None,
        faults: FaultSwitch | None = None,
    ) -> None:
        self._faults = faults
        self._key = api_key
        self._http = http
        self.llm_model = llm_model
        self.embedding_model = embedding_model
        self._redis = redis

    @property
    def configured(self) -> bool:
        return bool(self._key)

    async def generate_json(self, prompt: str, *, timeout_s: float, temperature: float = 0.2) -> Any:
        body = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": temperature, "responseMimeType": "application/json"},
        }
        data = await self._post(f"models/{self.llm_model}:generateContent", body, timeout_s)
        try:
            parts = data["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts)
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMBadOutput("response has no text (possibly blocked)") from exc
        try:
            return json.loads(_FENCE_RE.sub("", text.strip()))
        except json.JSONDecodeError as exc:
            raise LLMBadOutput("response is not valid JSON") from exc

    async def embed(self, texts: list[str], *, timeout_s: float) -> list[list[float]]:
        """Embed texts in order. Task prefixes are the caller's job (see services/embedding.py)."""
        vectors: list[list[float]] = []
        model = f"models/{self.embedding_model}"
        for start in range(0, len(texts), MAX_EMBED_BATCH):
            batch = texts[start : start + MAX_EMBED_BATCH]
            body = {
                "requests": [
                    {"model": model, "content": {"parts": [{"text": t}]}, "output_dimensionality": EMBEDDING_DIM}
                    for t in batch
                ]
            }
            data = await self._post(f"{model}:batchEmbedContents", body, timeout_s)
            try:
                got = [e["values"] for e in data["embeddings"]]
            except (KeyError, TypeError) as exc:
                raise LLMBadOutput("embedding response has an unexpected shape") from exc
            if len(got) != len(batch) or any(len(v) != EMBEDDING_DIM for v in got):
                raise LLMBadOutput(f"expected {len(batch)} vectors of {EMBEDDING_DIM} dimensions")
            vectors.extend(got)
        return vectors

    async def _post(self, path: str, body: dict[str, Any], timeout_s: float) -> dict[str, Any]:
        if not self.configured:
            raise LLMNotConfigured("GEMINI_API_KEY is not set")
        if self._faults is not None and (await self._faults.get()).llm_down:
            await self._record_failure("fault injected")
            raise LLMError("Gemini unreachable (fault injected)")
        try:
            resp = await self._http.post(
                f"{BASE_URL}/{path}", json=body, headers={"x-goog-api-key": self._key}, timeout=timeout_s
            )
        except httpx.HTTPError as exc:
            await self._record_failure(f"{type(exc).__name__}")
            raise LLMError(f"cannot reach Gemini: {type(exc).__name__}") from exc

        if resp.status_code >= 400:
            await self._record_failure(f"HTTP {resp.status_code}")
            # An invalid key is also reported as 400, but it is a config problem rather than a bad input.
            if resp.status_code == 400 and "API_KEY" not in resp.text and "API key" not in resp.text:
                raise LLMBadRequest(f"Gemini rejected the request: HTTP 400: {resp.text[:300]}")
            raise LLMError(f"Gemini returned HTTP {resp.status_code}")
        await self._clear_failure()
        return resp.json()

    async def _record_failure(self, reason: str) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.set(LAST_ERROR_KEY, reason, ex=LAST_ERROR_TTL_S)
        except Exception:  # noqa: BLE001 — health bookkeeping must not hide the real error
            log.debug("could not record LLM failure", exc_info=True)

    async def _clear_failure(self) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.delete(LAST_ERROR_KEY)
        except Exception:  # noqa: BLE001
            log.debug("could not clear LLM failure", exc_info=True)
