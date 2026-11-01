"""Scheduler (spec §8 bước 1): chỉ một instance làm leader (Redis lock), tạo crawl job theo chu kỳ.

Mỗi nguồn có key `sched:crawl:{code}` với TTL = chu kỳ crawl: tạo được key (NX) ⇒ đến lượt crawl.
"""

from __future__ import annotations

import asyncio
import logging

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Source
from app.workers.runtime import WorkerContext, run_worker
from app.workers.streams import STREAM_CRAWL_JOBS, publish

log = logging.getLogger("scheduler")

LOCK_KEY = "lock:scheduler"
LOCK_TTL_S = 30
TICK_S = 10

# Gia hạn lock chỉ khi mình đang giữ nó (atomic).
_RENEW = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('expire', KEYS[1], ARGV[2])
end
return 0
"""


async def acquire_leadership(redis: Redis, instance_id: str) -> bool:
    if await redis.set(LOCK_KEY, instance_id, nx=True, ex=LOCK_TTL_S):
        return True
    return bool(await redis.eval(_RENEW, 1, LOCK_KEY, instance_id, LOCK_TTL_S))


async def enqueue_crawl(redis: Redis, source_code: str) -> None:
    await publish(redis, STREAM_CRAWL_JOBS, {"source_code": source_code})


async def schedule_due_sources(
    redis: Redis, sessions: async_sessionmaker[AsyncSession], interval_s: int
) -> list[str]:
    async with sessions() as s:
        codes = (await s.scalars(select(Source.code).where(Source.enabled.is_(True)))).all()
    due = []
    for code in codes:
        if await redis.set(f"sched:crawl:{code}", 1, nx=True, ex=interval_s):
            await enqueue_crawl(redis, code)
            due.append(code)
    return due


async def main(ctx: WorkerContext) -> None:
    leader = False
    while not ctx.stop.is_set():
        try:
            now_leader = await acquire_leadership(ctx.redis, ctx.instance_id)
            if now_leader != leader:
                log.info("leadership %s", "acquired" if now_leader else "lost")
                leader = now_leader
            if leader:
                due = await schedule_due_sources(ctx.redis, ctx.sessions, ctx.settings.crawl_interval_s)
                if due:
                    log.info("enqueued crawl jobs: %s", ", ".join(due))
        except Exception:  # noqa: BLE001
            log.exception("scheduler tick failed")
        try:
            await asyncio.wait_for(ctx.stop.wait(), timeout=TICK_S)
        except TimeoutError:
            pass


if __name__ == "__main__":
    run_worker("scheduler", main)
