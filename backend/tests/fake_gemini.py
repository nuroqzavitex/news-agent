"""A stand-in for the Gemini REST API, built on httpx.MockTransport.

Embeddings: texts that mention "vàng" point in one direction and everything else in another, which is enough
to test ranking. Sentiment: any text containing "tăng" is positive, everything else negative.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable

import httpx

from app.db.models import EMBEDDING_DIM
from app.services.llm import GeminiClient


def vector_for(text: str) -> list[float]:
    vector = [0.0] * EMBEDDING_DIM
    vector[0 if "vàng" in text else 1] = 1.0
    return vector


def _embed(body: dict) -> httpx.Response:
    return httpx.Response(
        200, json={"embeddings": [{"values": vector_for(r["content"]["parts"][0]["text"])} for r in body["requests"]]}
    )


def _generate(body: dict) -> httpx.Response:
    prompt = body["contents"][0]["parts"][0]["text"]
    if '"summary"' in prompt:
        answer = json.dumps({"summary": "Tóm tắt từ model.", "key_points": ["Ý một", "Ý hai"]}, ensure_ascii=False)
    else:
        items = json.loads(prompt[prompt.index("Texts:\n") + len("Texts:\n") :])
        answer = json.dumps(
            [
                {"id": i["id"], "label": "positive" if "tăng" in i["text"] else "negative",
                 "score": 0.8 if "tăng" in i["text"] else -0.8}
                for i in items
            ]
        )
    return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": answer}]}}]})


class FakeGemini:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.fail_with: int | None = None  # answer every call with this status
        self.fail_generate_with: Callable[[], httpx.Response] | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail_with is not None:
            return httpx.Response(self.fail_with, json={"error": {"message": "failure"}})
        body = json.loads(request.content)
        if re.search(r":batchEmbedContents$", request.url.path):
            return _embed(body)
        if self.fail_generate_with is not None:
            return self.fail_generate_with()
        return _generate(body)

    def client(self, redis=None, api_key: str = "test-key") -> GeminiClient:
        return GeminiClient(
            api_key=api_key,
            http=httpx.AsyncClient(transport=httpx.MockTransport(self)),
            llm_model="test-llm",
            embedding_model="test-embedding",
            redis=redis,
        )
