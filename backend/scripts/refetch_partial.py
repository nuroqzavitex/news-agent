"""Re-queue articles stored without full content (status='partial') so the processor fetches them again.

The processor upserts by url, so re-processing simply fills in the content. Fetching still goes through
robots.txt checks and the per-host rate limit.

  python -m scripts.refetch_partial                 # all partial articles from the last 3 days
  python -m scripts.refetch_partial --source dantri --days 1
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime, timedelta

from redis.asyncio import Redis
from sqlalchemy import select

from app.db.models import Article, Source
from app.db.session import make_engine, make_session_factory
from app.settings import get_settings
from app.workers.streams import STREAM_NEWS_RAW, publish


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--source")
    p.add_argument("--days", type=int, default=3)
    args = p.parse_args()

    settings = get_settings()
    engine = make_engine(settings.database_url)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    query = (
        select(Article, Source.code)
        .join(Source)
        .where(Article.status == "partial", Article.crawled_at > datetime.now(UTC) - timedelta(days=args.days))
    )
    if args.source:
        query = query.where(Source.code == args.source)
    async with make_session_factory(engine)() as s:
        rows = (await s.execute(query)).all()
    for a, code in rows:
        await publish(redis, STREAM_NEWS_RAW, {
            "url": a.url, "url_hash": a.url_hash, "title": a.title, "summary": a.summary, "author": a.author,
            "published_at": a.published_at.isoformat() if a.published_at else None,
            "source_id": a.source_id, "source_code": code, "category": a.category,
        })
    print(f"re-queued {len(rows)} partial articles")
    await redis.aclose()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
