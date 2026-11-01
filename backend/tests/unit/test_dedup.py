import asyncio

import pytest

from app.agent.dedup import Dedup, DuplicateInProgress
from app.contracts import Envelope


def _reply() -> Envelope:
    return Envelope(from_="me", to="you", type="response", payload={"n": 1})


async def test_first_claim_owns_then_replay_returns_stored_reply(redis):
    d = Dedup(redis)
    assert await d.claim("m-1", wait_s=0) is None
    reply = _reply()
    await d.complete("m-1", reply)
    assert await d.claim("m-1", wait_s=0) == reply


async def test_concurrent_duplicate_waits_for_original(redis):
    d = Dedup(redis, poll_interval_s=0.01)
    assert await d.claim("m-2", wait_s=0) is None
    reply = _reply()

    async def finish_later():
        await asyncio.sleep(0.05)
        await d.complete("m-2", reply)

    task = asyncio.create_task(finish_later())
    assert await d.claim("m-2", wait_s=1) == reply
    await task


async def test_duplicate_in_progress_times_out(redis):
    d = Dedup(redis, poll_interval_s=0.01)
    await d.claim("m-3", wait_s=0)
    with pytest.raises(DuplicateInProgress):
        await d.claim("m-3", wait_s=0.05)


async def test_release_lets_retry_reprocess(redis):
    d = Dedup(redis)
    await d.claim("m-4", wait_s=0)
    await d.release("m-4")
    assert await d.claim("m-4", wait_s=0) is None
