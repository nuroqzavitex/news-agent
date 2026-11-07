import json

import httpx
import pytest

from app.db.models import EMBEDDING_DIM
from app.services.llm import LAST_ERROR_KEY, LLMBadOutput, LLMBadRequest, LLMError, LLMNotConfigured
from tests.fake_gemini import FakeGemini


async def test_not_configured_makes_no_request():
    fake = FakeGemini()
    llm = fake.client(api_key="")
    assert llm.configured is False
    with pytest.raises(LLMNotConfigured):
        await llm.generate_json("x", timeout_s=1)
    assert fake.requests == []


async def test_embed_request_shape_and_key_in_header_only():
    fake = FakeGemini()
    vectors = await fake.client().embed(["giá vàng", "bóng đá"], timeout_s=1)
    assert [len(v) for v in vectors] == [EMBEDDING_DIM, EMBEDDING_DIM]
    request = fake.requests[0]
    assert request.url.path.endswith("/models/test-embedding:batchEmbedContents")
    assert request.headers["x-goog-api-key"] == "test-key"
    assert "test-key" not in str(request.url)
    entry = json.loads(request.content)["requests"][0]
    assert entry["model"] == "models/test-embedding"
    assert entry["output_dimensionality"] == EMBEDDING_DIM
    assert "task_type" not in entry  # gemini-embedding-2 rejects it


async def test_embed_splits_large_batches():
    fake = FakeGemini()
    assert len(await fake.client().embed(["x"] * 250, timeout_s=1)) == 250
    assert len(fake.requests) == 3


async def test_embed_rejects_wrong_dimension():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"embeddings": [{"values": [0.1, 0.2]}]})

    fake = FakeGemini()
    llm = fake.client()
    llm._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(LLMBadOutput):
        await llm.embed(["x"], timeout_s=1)


@pytest.mark.parametrize("text", ['{"a": 1}', '```json\n{"a": 1}\n```'])
async def test_generate_json_accepts_fenced_output(text):
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": text}]}}]})

    llm = FakeGemini().client()
    llm._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert await llm.generate_json("x", timeout_s=1) == {"a": 1}


@pytest.mark.parametrize(
    "payload", [{"candidates": []}, {"candidates": [{"finishReason": "SAFETY"}]},
                {"candidates": [{"content": {"parts": [{"text": "not json"}]}}]}]
)
async def test_generate_json_bad_output(payload):
    llm = FakeGemini().client()
    llm._http = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)))
    with pytest.raises(LLMBadOutput):
        await llm.generate_json("x", timeout_s=1)


async def test_error_classification():
    def respond(status: int, text: str):
        return httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(status, text=text)))

    llm = FakeGemini().client()
    llm._http = respond(400, "invalid argument")
    with pytest.raises(LLMBadRequest):
        await llm.generate_json("x", timeout_s=1)
    # A wrong key is a config problem: it must not count against the article being processed.
    llm._http = respond(400, '{"reason": "API_KEY_INVALID"}')
    with pytest.raises(LLMError) as exc:
        await llm.generate_json("x", timeout_s=1)
    assert not isinstance(exc.value, LLMBadRequest)
    for status in (401, 403, 404, 429, 500):
        llm._http = respond(status, "nope")
        with pytest.raises(LLMError) as exc:
            await llm.generate_json("x", timeout_s=1)
        assert not isinstance(exc.value, LLMBadRequest)


async def test_network_failure_is_llm_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    llm = FakeGemini().client()
    llm._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(LLMError):
        await llm.embed(["x"], timeout_s=1)


async def test_failures_are_recorded_for_health_and_cleared_on_success(redis):
    fake = FakeGemini()
    llm = fake.client(redis=redis)
    fake.fail_with = 429
    with pytest.raises(LLMError):
        await llm.embed(["x"], timeout_s=1)
    assert await redis.get(LAST_ERROR_KEY) == b"HTTP 429"
    fake.fail_with = None
    await llm.embed(["x"], timeout_s=1)
    assert await redis.get(LAST_ERROR_KEY) is None
