"""Enricher: fills in embeddings and sentiment for stored articles.

The queue is the articles table itself (`enriched_at IS NULL`, FOR UPDATE SKIP LOCKED), which gives us:
- articles stay searchable by keyword even while Gemini is down, because storing them never depends on it;
- batching, so few LLM calls are needed;
- automatic backfill: adding GEMINI_API_KEY later is enough for old articles to be processed;
- safe scaling and crash recovery: row locks are released when a worker dies.

Without an API key, only lexicon sentiment is filled in.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Article, ArticleChunk
from app.services.embedding import chunk_text, document_text
from app.services.llm import GeminiClient, LLMBadOutput, LLMBadRequest, LLMError
from app.services.sentiment import Sentiment, lexicon_sentiment, llm_sentiments
from app.workers.runtime import WorkerContext, run_worker

log = logging.getLogger("enricher")

BATCH_SIZE = 20
MAX_ATTEMPTS = 3
IDLE_DELAY_S = 30.0
MAX_BACKOFF_S = 300.0
EMBED_TIMEOUT_S = 60.0
SENTIMENT_TIMEOUT_S = 60.0


def _sentiment_text(a: Article) -> str:
    return f"{a.title}. {a.summary or (a.content or '')[:600]}"


def _apply(a: Article, s: Sentiment) -> None:
    a.sentiment_label, a.sentiment_score, a.sentiment_source = s.label, s.score, s.source


async def enrich_batch(
    sessions: async_sessionmaker[AsyncSession], llm: GeminiClient, *, batch_size: int = BATCH_SIZE
) -> int:
    """Process one batch and return how many articles it touched. Raises LLMError when it is worth backing off."""
    query = select(Article).where(Article.duplicate_of.is_(None))
    if llm.configured:
        query = query.where(Article.enriched_at.is_(None), Article.enrich_attempts < MAX_ATTEMPTS)
    else:
        query = query.where(Article.sentiment_label.is_(None))
    query = query.order_by(Article.id.desc()).limit(batch_size).with_for_update(skip_locked=True)

    async with sessions() as s:
        articles = (await s.scalars(query)).all()
        if not articles:
            return 0

        for a in articles:
            if a.sentiment_label is None:
                _apply(a, lexicon_sentiment(_sentiment_text(a)))
        if not llm.configured:
            await s.commit()
            return len(articles)

        try:
            await _embed(s, articles, llm)
        except LLMBadRequest:
            log.warning("Gemini rejected a batch of %d articles", len(articles), exc_info=True)
            for a in articles:
                a.enrich_attempts += 1
            await s.commit()
            return len(articles)
        except LLMError:
            await s.commit()  # keep the lexicon sentiment; articles stay pending
            raise

        try:
            sentiments = await llm_sentiments(
                llm, [(a.id, _sentiment_text(a)) for a in articles], timeout_s=SENTIMENT_TIMEOUT_S
            )
        except (LLMBadOutput, LLMBadRequest) as exc:
            log.warning("LLM sentiment unusable, keeping lexicon: %s", exc)
            sentiments = {}
        except LLMError:
            await s.commit()  # embeddings are kept, and replaced when this batch is retried
            raise

        now = datetime.now(UTC)
        for a in articles:
            if a.id in sentiments:
                _apply(a, sentiments[a.id])
            a.enriched_at = now
        await s.commit()
        return len(articles)


async def _embed(s: AsyncSession, articles: list[Article] | tuple, llm: GeminiClient) -> None:
    rows: list[tuple[int, int, str]] = []
    inputs: list[str] = []
    for a in articles:
        body = a.content or f"{a.title}\n{a.summary or ''}"
        for index, chunk in enumerate(chunk_text(body)):
            rows.append((a.id, index, chunk))
            inputs.append(document_text(a.title, chunk))
    vectors = await llm.embed(inputs, timeout_s=EMBED_TIMEOUT_S)
    await s.execute(delete(ArticleChunk).where(ArticleChunk.article_id.in_([a.id for a in articles])))
    s.add_all(
        ArticleChunk(article_id=article_id, chunk_index=index, text=text, embedding=vector)
        for (article_id, index, text), vector in zip(rows, vectors, strict=True)
    )
    await s.flush()


async def main(ctx: WorkerContext) -> None:
    llm = GeminiClient(
        api_key=ctx.settings.gemini_api_key,
        http=ctx.http,
        llm_model=ctx.settings.llm_model,
        embedding_model=ctx.settings.embedding_model,
        redis=ctx.redis,
    )
    if not llm.configured:
        log.info("GEMINI_API_KEY is not set: filling in lexicon sentiment only")
    backoff = 0.0
    while not ctx.stop.is_set():
        try:
            done = await enrich_batch(ctx.sessions, llm)
            backoff = 0.0
            if not done:
                delay = IDLE_DELAY_S
            else:
                # Pacing protects API rate limits; without a key there are no API calls to protect.
                delay = ctx.settings.enrich_interval_s if llm.configured else 0.1
            if done:
                log.info("enriched %d articles", done)
        except LLMError as exc:
            backoff = min(MAX_BACKOFF_S, max(IDLE_DELAY_S, backoff * 2))
            delay = backoff
            log.warning("Gemini unavailable (%s), retrying in %.0fs", exc, delay)
        except Exception:  # noqa: BLE001 — e.g. the database is restarting
            log.exception("enrich batch failed")
            delay = IDLE_DELAY_S
        try:
            await asyncio.wait_for(ctx.stop.wait(), timeout=delay)
        except TimeoutError:
            pass


if __name__ == "__main__":
    run_worker("enricher", main)
