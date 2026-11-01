import fakeredis
import pytest

from app.workers.scheduler import acquire_leadership


@pytest.fixture
def r():
    return fakeredis.FakeAsyncRedis(decode_responses=True)


async def test_only_one_leader(r):
    assert await acquire_leadership(r, "a") is True
    assert await acquire_leadership(r, "b") is False
    assert await acquire_leadership(r, "a") is True  # gia hạn


async def test_leadership_moves_when_lock_expires(r):
    assert await acquire_leadership(r, "a") is True
    await r.delete("lock:scheduler")  # giả lập leader chết, lock hết hạn
    assert await acquire_leadership(r, "b") is True
    assert await acquire_leadership(r, "a") is False
