"""trending_topics (spec §5.4): computed on demand from headlines, cached briefly in Redis."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.agent.capabilities import CapabilityContext, capability
from app.agent.capabilities._common import WINDOW_PATTERN, parse_window
from app.db.models import Article
from app.services.search import article_public_id
from app.services.trending import trending

CACHE_TTL_S = 300
MAX_ARTICLES = 10_000
BASELINE_SPANS = 8  # compare the window with the 7 windows before it


class TrendingPayload(BaseModel):
    window: str = Field(default="24h", pattern=WINDOW_PATTERN)
    limit: int = Field(default=10, ge=1, le=50)
    category: str | None = Field(default=None, max_length=100)


@capability("trending_topics", TrendingPayload)
async def trending_topics(p: TrendingPayload, ctx: CapabilityContext) -> dict[str, Any]:
    redis = ctx.deps["redis"]
    cache_key = "cache:trending:" + hashlib.sha1(f"{p.window}|{p.limit}|{p.category}".encode()).hexdigest()
    cached = await redis.get(cache_key)
    if cached:
        return json.loads(cached)

    now = datetime.now(UTC)
    span = parse_window(p.window)
    when = func.coalesce(Article.published_at, Article.crawled_at)
    filters = [Article.duplicate_of.is_(None), when >= now - BASELINE_SPANS * span]
    if p.category:
        filters.append(Article.category == p.category)
    async with ctx.deps["sessions"]() as s:
        rows = (
            await s.execute(
                select(Article.id, Article.title, Article.summary, when.label("when"))
                .where(*filters)
                .order_by(when.desc())
                .limit(MAX_ARTICLES)
            )
        ).all()

    def text(row: Any) -> str:
        return f"{row.title}. {row.summary or ''}"

    current = [(r.id, text(r)) for r in rows if r.when >= now - span]
    previous = [text(r) for r in rows if now - 2 * span <= r.when < now - span]
    baseline = [text(r) for r in rows if r.when < now - span]
    result = {
        "topics": [
            {
                "topic": t.topic,
                "count": t.count,
                "delta_pct": t.delta_pct,
                "sample_article_ids": [article_public_id(i) for i in t.sample_ids],
            }
            for t in trending(current, previous, baseline, limit=p.limit)
        ],
        "window": p.window,
        "n_articles": len(current),
    }
    await redis.set(cache_key, json.dumps(result, ensure_ascii=False), ex=CACHE_TTL_S)
    return result
