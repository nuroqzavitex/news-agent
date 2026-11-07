"""sentiment (spec §5.3): for a raw text, for specific articles, or aggregated over articles about a topic."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Self

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select

from app.agent.capabilities import CapabilityContext, capability
from app.agent.capabilities._common import WINDOW_PATTERN, parse_window, trace_llm_call, trace_llm_fallback
from app.db.models import Article
from app.services.llm import GeminiClient, LLMError, LLMNotConfigured
from app.services.search import parse_article_id
from app.services.sentiment import label_for, lexicon_sentiment, llm_sentiments

log = logging.getLogger(__name__)

MAX_ARTICLES = 200


class SentimentPayload(BaseModel):
    query: str | None = Field(default=None, min_length=1, max_length=500)
    text: str | None = Field(default=None, min_length=1, max_length=5000)
    article_ids: list[str] | None = Field(default=None, min_length=1, max_length=MAX_ARTICLES)
    window: str = Field(default="7d", pattern=WINDOW_PATTERN)

    @model_validator(mode="after")
    def _exactly_one_input(self) -> Self:
        if sum(v is not None for v in (self.query, self.text, self.article_ids)) != 1:
            raise ValueError("provide exactly one of: query, text, article_ids")
        return self


def _aggregate(rows: list[tuple[str, float | None]], window: str | None) -> dict[str, Any]:
    breakdown = {"positive": 0, "neutral": 0, "negative": 0}
    total = 0.0
    for label, score in rows:
        breakdown[label] += 1
        total += score or 0.0
    average = round(total / len(rows), 3) if rows else 0.0
    return {
        "label": label_for(average),
        "score": average,
        "breakdown": breakdown,
        "window": window,
        "n_articles": len(rows),
    }


async def _for_text(text: str, ctx: CapabilityContext) -> dict[str, Any]:
    llm: GeminiClient = ctx.deps["llm"]
    result = None
    try:
        await trace_llm_call(ctx, "sentiment", llm)
        answer = await llm_sentiments(llm, [(0, text)], timeout_s=ctx.deps["settings"].llm_timeout_s)
        result = answer.get(0)
    except LLMError as exc:
        if not isinstance(exc, LLMNotConfigured):
            log.warning("sentiment falling back to lexicon: %s", exc)
        await trace_llm_fallback(ctx, "sentiment", type(exc).__name__)
    result = result or lexicon_sentiment(text)
    return {
        "label": result.label,
        "score": result.score,
        "breakdown": {"positive": 0, "neutral": 0, "negative": 0, result.label: 1},
        "window": None,
        "n_articles": 0,
        "method": result.source,
    }


@capability("sentiment", SentimentPayload)
async def sentiment(p: SentimentPayload, ctx: CapabilityContext) -> dict[str, Any]:
    if p.text is not None:
        return await _for_text(p.text, ctx)

    columns = select(Article.sentiment_label, Article.sentiment_score).where(Article.sentiment_label.is_not(None))
    window: str | None = None
    if p.article_ids is not None:
        ids = [i for i in (parse_article_id(a) for a in p.article_ids) if i is not None]
        query = columns.where(Article.id.in_(ids))
    else:
        window = p.window
        when = func.coalesce(Article.published_at, Article.crawled_at)
        tsq = func.websearch_to_tsquery("simple", func.immutable_unaccent(p.query))
        query = (
            columns.where(
                Article.tsv.op("@@")(tsq),
                Article.duplicate_of.is_(None),
                when >= datetime.now(UTC) - parse_window(p.window),
            )
            .order_by(when.desc())
            .limit(MAX_ARTICLES)
        )
    async with ctx.deps["sessions"]() as s:
        rows = [(label, score) for label, score in (await s.execute(query)).all()]
    return _aggregate(rows, window)
