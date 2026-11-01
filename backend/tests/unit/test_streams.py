import json

import fakeredis
import pytest

from app.workers.streams import StreamConsumer, publish


@pytest.fixture
def r():
    return fakeredis.FakeAsyncRedis(decode_responses=True)


async def _consumer(r, handler, **kw) -> StreamConsumer:
    c = StreamConsumer(r, stream="s", group="g", consumer="c1", handler=handler,
                       dlq_stream="dlq", block_ms=10, **kw)
    await c.ensure_group()
    await c.ensure_group()  # idempotent
    return c


async def test_success_acks(r):
    seen = []

    async def handler(data):
        seen.append(data)

    c = await _consumer(r, handler)
    await publish(r, "s", {"url": "u1"})
    assert await c.run_once() == 1
    assert seen == [{"url": "u1"}]
    assert (await r.xpending("s", "g"))["pending"] == 0


async def test_failure_is_retried_then_sent_to_dlq(r):
    calls = 0

    async def handler(data):
        nonlocal calls
        calls += 1
        raise RuntimeError("down")

    c = await _consumer(r, handler, max_attempts=3, min_idle_ms=0)
    await publish(r, "s", {"url": "u1"})
    for _ in range(3):
        await c.run_once()
    assert calls == 3
    assert (await r.xpending("s", "g"))["pending"] == 0
    dlq = await r.xrange("dlq")
    assert len(dlq) == 1
    entry = json.loads(dlq[0][1]["data"])
    assert entry["data"] == {"url": "u1"} and entry["attempts"] == 3


async def test_pending_from_dead_consumer_is_reclaimed(r):
    async def boom(data):
        raise RuntimeError("crash")

    dead = await _consumer(r, boom, min_idle_ms=0)
    await publish(r, "s", {"url": "u1"})
    await dead.run_once()  # nhận message nhưng không ack

    seen = []

    async def ok(data):
        seen.append(data)

    alive = StreamConsumer(r, stream="s", group="g", consumer="c2", handler=ok, block_ms=10, min_idle_ms=0)
    await alive.run_once()
    assert seen == [{"url": "u1"}]
    assert (await r.xpending("s", "g"))["pending"] == 0
