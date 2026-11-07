"""Processor (spec §8 bước 3): `news.raw` → tải HTML → trích nội dung → simhash/khử trùng → lưu DB.

Idempotent: upsert theo `url`, nên message được giao lại (at-least-once) không tạo bản ghi trùng.
Embedding + sentiment được thêm ở M4.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import httpx
import trafilatura
from sqlalchemy import case, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Article
from app.services.politeness import Politeness
from app.services.textproc import normalize_paragraphs, normalize_text, simhash
from app.workers.crawler import fetch_with_retry
from app.workers.runtime import WorkerContext, run_worker
from app.workers.streams import STREAM_NEWS_PROCESSED, STREAM_NEWS_RAW, StreamConsumer, publish

log = logging.getLogger("processor")

NEAR_DUP_MAX_HAMMING = 3
NEAR_DUP_MIN_CHARS = 300
NEAR_DUP_WINDOW = "3 days"


class RateLimited(Exception):
    """Báo trả 429 → để stream giao lại sau (min_idle) thay vì lưu bài thiếu nội dung."""


async def fetch_content(http: httpx.AsyncClient, polite: Politeness, url: str) -> str | None:
    """Article text, or None (keep the RSS summary) when robots.txt disallows it, the page returns 4xx,
    or the host is unreachable. 5xx and 429 raise so the stream redelivers the item later."""
    if not await polite.allowed(url):
        log.info("robots.txt disallows %s, keeping RSS summary only", url)
        return None
    await polite.wait_turn(url)
    try:
        resp = await fetch_with_retry(http, url)
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        # Host refuses/drops our connections (possibly blocking us): do not hammer it with retries.
        log.warning("cannot connect to %s (%s), keeping RSS summary only", url, type(exc).__name__)
        return None
    if resp.status_code == 429:
        raise RateLimited(url)
    if 400 <= resp.status_code < 500:
        return None
    resp.raise_for_status()
    extracted = trafilatura.extract(resp.text, include_comments=False, include_tables=False, favor_precision=True)
    return normalize_paragraphs(extracted) if extracted else None


async def find_near_duplicate(s: AsyncSession, h: int, url: str) -> int | None:
    return await s.scalar(
        text(
            "SELECT id FROM articles "
            "WHERE simhash IS NOT NULL AND duplicate_of IS NULL AND url <> :url "
            f"AND crawled_at > now() - interval '{NEAR_DUP_WINDOW}' "
            "AND bit_count((simhash # :h)::bit(64)) <= :max_d "
            "ORDER BY id LIMIT 1"
        ),
        {"h": h, "url": url, "max_d": NEAR_DUP_MAX_HAMMING},
    )


async def process_item(
    item: dict[str, Any], *, sessions: async_sessionmaker[AsyncSession], http: httpx.AsyncClient, polite: Politeness
) -> int:
    content = await fetch_content(http, polite, item["url"])
    title = normalize_text(item["title"])
    summary = normalize_text(item.get("summary")) or None
    basis = content or summary or title
    h = simhash(basis)
    published = datetime.fromisoformat(item["published_at"]) if item.get("published_at") else None

    async with sessions() as s:
        duplicate_of = None
        if len(basis) >= NEAR_DUP_MIN_CHARS:
            duplicate_of = await find_near_duplicate(s, h, item["url"])
        values = {
            "source_id": item["source_id"],
            "url": item["url"],
            "url_hash": item["url_hash"],
            "title": title,
            "summary": summary,
            "content": content,
            "author": item.get("author"),
            "category": item.get("category"),
            "published_at": published,
            "simhash": h,
            "duplicate_of": duplicate_of,
            "status": "processed" if content else "partial",
        }
        stmt = insert(Article).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[Article.url],
            set_={
                **{k: stmt.excluded[k] for k in ("title", "summary", "content", "simhash", "status")},
                # New text means old embeddings are stale: send the article back to the enricher.
                "enriched_at": case(
                    (Article.content.is_distinct_from(stmt.excluded.content), None), else_=Article.enriched_at
                ),
            },
        ).returning(Article.id)
        article_id = int((await s.execute(stmt)).scalar_one())
        await s.commit()
    log.info("stored a_%s %s%s", article_id, item["url"], f" (dup of a_{duplicate_of})" if duplicate_of else "")
    return article_id


async def main(ctx: WorkerContext) -> None:
    polite = Politeness(ctx.redis, ctx.http, ctx.settings.http_user_agent)

    async def handle(item: dict[str, Any]) -> None:
        article_id = await process_item(item, sessions=ctx.sessions, http=ctx.http, polite=polite)
        await publish(ctx.redis, STREAM_NEWS_PROCESSED, {"article_id": article_id})

    consumer = StreamConsumer(
        ctx.redis, stream=STREAM_NEWS_RAW, group="processor", consumer=ctx.instance_id, handler=handle
    )
    await consumer.run_forever(ctx.stop)


if __name__ == "__main__":
    run_worker("processor", main)
