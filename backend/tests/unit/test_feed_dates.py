from datetime import UTC, datetime

import pytest

from app.workers.crawler import parse_feed, parse_feed_date

EXPECTED = datetime(2026, 11, 1, 2, 41, tzinfo=UTC)


@pytest.mark.parametrize("raw", [
    "Sun, 01 Nov 2026 09:41:00 +07",       # CafeF
    "Sun, 01 Nov 2026 09:41:00 GMT+7",
    "Sun, 01 Nov 2026 09:41:00 +0700",
    "Sun, 01 Nov 2026 02:41:00 GMT",
    "2026-11-01T09:41:00+07:00",
    "2026-11-01T09:41:00",                 # no offset: Vietnam time
    "11/1/2026 9:41:00 AM",           # Tuoi Tre: US order, narrow no-break space, Vietnam time
])
def test_parse_feed_date_variants(raw):
    assert parse_feed_date(raw) == EXPECTED


def test_parse_feed_date_garbage():
    assert parse_feed_date("not a date") is None
    assert parse_feed_date(None) is None


def test_parse_feed_uses_fallback_for_short_offset():
    rss = b"""<?xml version="1.0"?><rss version="2.0"><channel>
    <item><title>T</title><link>https://x.test/a</link><pubDate>Sun, 01 Nov 2026 09:41:00 +07</pubDate></item>
    </channel></rss>"""
    [item] = parse_feed(rss)
    assert item["published_at"] == EXPECTED.isoformat()
