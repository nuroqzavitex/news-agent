"""Crawler (spec §8 bước 2): nhận job từ `crawl.jobs`, đọc RSS, đẩy URL mới vào `news.raw`.

Tải HTML và trích nội dung do processor đảm nhiệm, để stream chỉ chứa metadata nhỏ
và việc tải bài được chia đều cho các processor.
"""

from __future__ import annotations

import asyncio
import calendar
import email.utils
import logging
import re
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import feedparser
import httpx
from redis.asyncio import Redis
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Article, Source
from app.services.textproc import canonical_url, strip_html, url_hash
from app.workers.runtime import WorkerContext, run_worker
from app.workers.streams import STREAM_CRAWL_JOBS, STREAM_NEWS_RAW, StreamConsumer, publish

log = logging.getLogger("crawler")

SEEN_TTL_S = 2 * 86_400
VN_TZ = timezone(timedelta(hours=7))


async def fetch_with_retry(http: httpx.AsyncClient, url: str, retries: int = 2) -> httpx.Response:
    delay = 0.5
    for attempt in range(retries + 1):
        try:
            resp = await http.get(url)
            if resp.status_code < 500:
                return resp
            if attempt == retries:
                return resp
        except httpx.TransportError:
            if attempt == retries:
                raise
        await asyncio.sleep(delay)
        delay *= 2
    raise AssertionError("unreachable")


_SHORT_OFFSET = re.compile(r"(?:GMT|UTC)?\s*([+-])(\d{1,2})(?::?(\d{2}))?$")


def parse_feed_date(raw: str | None) -> datetime | None:
    """Parse the date formats Vietnamese feeds use: RFC 822 and ISO, offsets like '+07' or 'GMT+7',
    and US-style '11/7/2026 8:26:00 AM' (Tuoi Tre). Dates without an offset are Vietnam time."""
    if not raw:
        return None
    text = " ".join(raw.split())  # also normalizes the narrow no-break space in Tuoi Tre's dates
    match = _SHORT_OFFSET.search(text)
    if match:
        sign, hours, minutes = match.groups()
        text = f"{text[: match.start()].rstrip()} {sign}{int(hours):02d}{minutes or '00'}"

    def us_style(value: str) -> datetime:
        return datetime.strptime(value, "%m/%d/%Y %I:%M:%S %p")

    for parse in (email.utils.parsedate_to_datetime, datetime.fromisoformat, us_style):
        try:
            value = parse(text)
        except (TypeError, ValueError, IndexError):
            continue
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=VN_TZ).astimezone(UTC)
    return None


def entry_published(entry: Any) -> datetime | None:
    if entry.get("published_parsed"):
        return datetime.fromtimestamp(calendar.timegm(entry["published_parsed"]), tz=UTC)
    return parse_feed_date(entry.get("published") or entry.get("updated"))


def parse_feed(content: bytes) -> list[dict[str, Any]]:
    feed = feedparser.parse(content)
    items = []
    for e in feed.entries:
        link = e.get("link")
        title = strip_html(e.get("title"))
        if not link or not title:
            continue
        published = entry_published(e)
        url = canonical_url(link)
        items.append({
            "url": url,
            "url_hash": url_hash(url),
            "title": title,
            "summary": strip_html(e.get("summary")),
            "author": e.get("author"),
            "published_at": published.isoformat() if published else None,
        })
    return items


async def crawl_source(
    code: str, *, sessions: async_sessionmaker[AsyncSession], redis: Redis, http: httpx.AsyncClient
) -> int:
    async with sessions() as s:
        source = await s.scalar(select(Source).where(Source.code == code))
    if source is None or not source.enabled:
        log.info("skip source %s (missing or disabled)", code)
        return 0

    try:
        resp = await fetch_with_retry(http, source.rss_url)
        resp.raise_for_status()
        items = parse_feed(resp.content)
    except Exception as exc:  # noqa: BLE001 — ghi trạng thái nguồn, không làm chết worker
        await _mark(sessions, source.id, f"error: {exc!r}"[:300])
        log.warning("crawl %s failed: %s", code, exc)
        return 0

    hashes = [i["url_hash"] for i in items]
    async with sessions() as s:
        existing = set((await s.scalars(select(Article.url_hash).where(Article.url_hash.in_(hashes)))).all())

    new = 0
    for item in items:
        if item["url_hash"] in existing:
            continue
        # Bài đã đẩy vào stream nhưng processor chưa lưu → không đẩy lại.
        if not await redis.set(f"seen:{item['url_hash']}", 1, nx=True, ex=SEEN_TTL_S):
            continue
        await publish(redis, STREAM_NEWS_RAW, {
            **item, "source_id": source.id, "source_code": source.code, "category": source.category,
        })
        new += 1

    await _mark(sessions, source.id, f"ok: {new} new / {len(items)} items")
    log.info("crawled %s: %d new / %d items", code, new, len(items))
    return new


async def _mark(sessions: async_sessionmaker[AsyncSession], source_id: int, status: str) -> None:
    async with sessions() as s:
        await s.execute(
            update(Source).where(Source.id == source_id)
            .values(last_crawled_at=datetime.now(UTC), last_status=status)
        )
        await s.commit()


async def main(ctx: WorkerContext) -> None:
    async def handle(job: dict[str, Any]) -> None:
        await crawl_source(job["source_code"], sessions=ctx.sessions, redis=ctx.redis, http=ctx.http)

    consumer = StreamConsumer(
        ctx.redis, stream=STREAM_CRAWL_JOBS, group="crawler", consumer=ctx.instance_id, handler=handle
    )
    await consumer.run_forever(ctx.stop)


if __name__ == "__main__":
    run_worker("crawler", main)
