import asyncio

import pytest
from pydantic import BaseModel

from app.adapters.local.identity import LocalIdentity
from app.adapters.local.tracer import LogTracer
from app.agent.capabilities import CapabilityRegistry
from app.agent.dedup import Dedup
from app.agent.handler import MessageHandler
from app.contracts import Envelope
from app.services.tracing import TraceRecorder
from tests.conftest import MemoryMessageLog, MemoryTraceSink

ME = "team-08-news"


class EchoPayload(BaseModel):
    text: str


@pytest.fixture
def setup(redis, identity: LocalIdentity):
    caps = CapabilityRegistry()
    calls = {"echo": 0}

    @caps.register("echo", EchoPayload)
    async def echo(p: EchoPayload, ctx):
        calls["echo"] += 1
        return {"echo": p.text}

    @caps.register("slow", EchoPayload)
    async def slow(p: EchoPayload, ctx):
        await asyncio.sleep(1)
        return {}

    @caps.register("boom", EchoPayload)
    async def boom(p: EchoPayload, ctx):
        raise RuntimeError("boom")

    sink = MemoryTraceSink()
    log = MemoryMessageLog()
    handler = MessageHandler(
        agent_id=ME,
        identity=identity,
        dedup=Dedup(redis, poll_interval_s=0.01),
        capabilities=caps,
        message_log=log,
        tracer=TraceRecorder(ME, LogTracer(), sink),
        capability_timeout_s=0.1,
    )
    return handler, calls, sink, log


async def _request(identity: LocalIdentity, capability="echo", sender="agent-a", **kw) -> dict:
    env = Envelope(from_=sender, to=ME, capability=capability, payload={"text": "hi"},
                   token=await identity.issue_token(sender), **kw)
    return env.wire()


async def test_happy_path(setup, identity):
    handler, calls, sink, log = setup
    raw = await _request(identity)
    res = await handler.handle(raw)
    assert res.status_code == 200
    assert res.envelope.type == "response"
    assert res.envelope.payload == {"echo": "hi"}
    assert res.envelope.reply_to == raw["message_id"]
    assert res.envelope.trace_id == raw["trace_id"]
    assert sink.names() == ["msg.received", "msg.replied"]
    assert {r["status"] for r in log.rows} >= {"processing", "replied", "delivered"}


async def test_duplicate_is_not_processed_twice(setup, identity):
    handler, calls, sink, _ = setup
    raw = await _request(identity)
    first = await handler.handle(raw)
    second = await handler.handle(raw)
    assert calls["echo"] == 1
    assert second.envelope == first.envelope
    assert "msg.duplicate" in sink.names()


async def test_missing_bad_and_spoofed_tokens_are_401(setup, identity):
    handler, calls, sink, _ = setup
    raw = await _request(identity)

    no_token = {**raw, "token": None}
    bad_token = {**raw, "token": "x.y"}
    spoofed = {**raw, "from": "someone-else"}
    for r in (no_token, bad_token, spoofed):
        res = await handler.handle(r)
        assert res.status_code == 401
        assert res.envelope.payload["error"] == "unauthorized"
    assert calls["echo"] == 0
    assert sink.names().count("msg.rejected") == 3


async def test_malformed_envelope_is_400(setup):
    handler, *_ = setup
    res = await handler.handle({"to": ME, "message_id": "m-x"})
    assert res.status_code == 400
    assert res.envelope.reply_to == "m-x"


async def test_unknown_capability_and_bad_payload_are_400(setup, identity):
    handler, *_ = setup
    res = await handler.handle(await _request(identity, capability="nope"))
    assert (res.status_code, res.envelope.payload["error"]) == (400, "bad_request")

    raw = await _request(identity)
    raw["payload"] = {"wrong": 1}
    res = await handler.handle(raw)
    assert (res.status_code, res.envelope.payload["error"]) == (400, "bad_request")


async def test_timeout_is_504_and_retry_is_reprocessed(setup, identity):
    handler, *_ = setup
    raw = await _request(identity, capability="slow")
    res = await handler.handle(raw)
    assert (res.status_code, res.envelope.payload["error"]) == (504, "timeout")
    # lỗi tạm thời không bị cache → retry cùng message_id lại chạy (và lại timeout)
    res2 = await handler.handle(raw)
    assert res2.envelope.message_id != res.envelope.message_id


async def test_internal_error_is_500(setup, identity):
    handler, *_ = setup
    res = await handler.handle(await _request(identity, capability="boom"))
    assert (res.status_code, res.envelope.payload["error"]) == (500, "internal")
