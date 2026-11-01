"""Crawler → processor → Postgres, with the network mocked by httpx.MockTransport."""

from __future__ import annotations

import json

import httpx
import pytest
from sqlalchemy import func, select

from app.db.models import Article
from app.services.politeness import Politeness
from app.workers.crawler import crawl_source
from app.workers.processor import RateLimited, process_item
from app.workers.streams import STREAM_NEWS_RAW

pytestmark = pytest.mark.integration

BODY = (
    "Giá vàng miếng SJC hôm nay tăng mạnh thêm 1,5 triệu đồng mỗi lượng, lên mức cao kỷ lục trong bối cảnh "
    "giá vàng thế giới liên tục lập đỉnh mới do lo ngại lạm phát và căng thẳng địa chính trị kéo dài. "
) * 6

RSS = """<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>t</title>
<item><title>Giá vàng tăng mạnh</title><link>https://news.test/gia-vang.html?utm_source=rss</link>
<description><![CDATA[<a href="x"><img src="y"/></a>Giá vàng &amp; USD]]></description>
<pubDate>Sat, 01 Nov 2026 07:00:00 +0700</pubDate></item>
<item><title>Bóng đá Việt Nam</title><link>https://news.test/bong-da.html</link>
<description>Đội tuyển thắng</description></item>
</channel></rss>"""


def article_html(body: str) -> str:
    return f"<html><head><title>t</title></head><body><article><h1>Tiêu đề</h1><p>{body}</p></article></body></html>"


def make_http(pages: dict[str, httpx.Response]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return pages.get(str(request.url), httpx.Response(404))

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def item(url: str, title: str = "Giá vàng tăng mạnh") -> dict:
    return {"url": url, "url_hash": url, "title": title, "summary": "tóm tắt", "source_id": 1,
            "source_code": "testnews", "published_at": "2026-11-01T00:00:00+00:00"}


async def test_crawler_enqueues_only_new_canonical_urls(sessions, redis):
    http = make_http({"https://news.test/rss": httpx.Response(200, text=RSS)})
    assert await crawl_source("testnews", sessions=sessions, redis=redis, http=http) == 2
    entries = await redis.xrange(STREAM_NEWS_RAW)
    urls = sorted(json.loads(e[1]["data"])["url"] for e in entries)
    assert urls == ["https://news.test/bong-da.html", "https://news.test/gia-vang.html"]
    first = json.loads(entries[0][1]["data"])
    assert "<" not in first["summary"]
    # second crawl: same items are not enqueued again
    assert await crawl_source("testnews", sessions=sessions, redis=redis, http=http) == 0


async def test_crawler_records_rss_failure_without_crashing(sessions, redis):
    http = make_http({"https://news.test/rss": httpx.Response(500)})
    assert await crawl_source("testnews", sessions=sessions, redis=redis, http=http) == 0


async def test_processor_upsert_is_idempotent(sessions, redis):
    url = "https://news.test/a.html"
    http = make_http({url: httpx.Response(200, text=article_html(BODY))})
    polite = Politeness(redis, http, "test", min_interval_ms=1)
    first = await process_item(item(url), sessions=sessions, http=http, polite=polite)
    second = await process_item(item(url), sessions=sessions, http=http, polite=polite)
    assert first == second
    async with sessions() as s:
        assert await s.scalar(select(func.count()).select_from(Article)) == 1
        a = await s.get(Article, first)
    assert a.status == "processed" and "SJC" in a.content


async def test_processor_links_near_duplicates(sessions, redis):
    a_url, b_url = "https://news.test/a.html", "https://news.test/b.html"
    http = make_http({
        a_url: httpx.Response(200, text=article_html(BODY)),
        b_url: httpx.Response(200, text=article_html(BODY.replace("kỷ lục", "kỷ lục mới", 1))),
    })
    polite = Politeness(redis, http, "test", min_interval_ms=1)
    a_id = await process_item(item(a_url), sessions=sessions, http=http, polite=polite)
    b_id = await process_item(item(b_url), sessions=sessions, http=http, polite=polite)
    async with sessions() as s:
        assert (await s.get(Article, b_id)).duplicate_of == a_id


async def test_processor_keeps_summary_on_4xx_and_retries_on_429(sessions, redis):
    gone, limited = "https://news.test/gone.html", "https://news.test/limited.html"
    http = make_http({gone: httpx.Response(410), limited: httpx.Response(429)})
    polite = Politeness(redis, http, "test", min_interval_ms=1)
    article_id = await process_item(item(gone), sessions=sessions, http=http, polite=polite)
    async with sessions() as s:
        a = await s.get(Article, article_id)
    assert a.status == "partial" and a.content is None and a.summary == "tóm tắt"
    with pytest.raises(RateLimited):
        await process_item(item(limited), sessions=sessions, http=http, polite=polite)


async def test_processor_keeps_summary_when_host_unreachable(sessions, redis):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        raise httpx.ConnectTimeout("timed out", request=request)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    polite = Politeness(redis, http, "test", min_interval_ms=1)
    article_id = await process_item(item("https://news.test/down.html"), sessions=sessions, http=http, polite=polite)
    async with sessions() as s:
        assert (await s.get(Article, article_id)).status == "partial"
