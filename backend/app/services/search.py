"""Article search: keyword (Postgres FTS, with or without diacritics), semantic (pgvector) and hybrid (RRF).

Semantic and hybrid need a query embedding. When that is impossible (no key, Gemini down, nothing embedded yet)
the search degrades to keyword and says so in `mode_used`.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import and_, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Article, ArticleChunk, Source
from app.services.embedding import query_text
from app.services.llm import GeminiClient, LLMError

log = logging.getLogger(__name__)

VN_TZ = timezone(timedelta(hours=7))
SearchMode = Literal["keyword", "semantic", "hybrid"]
SentimentLabel = Literal["positive", "neutral", "negative"]

RRF_K = 60
CANDIDATE_POOL = 100  # articles each ranking contributes to the merge
CHUNK_POOL = 400  # nearest chunks to look at; several can belong to one article
QUERY_VECTOR_TTL_S = 86_400
# Nearest neighbours always exist, however unrelated. Chunks further than this do not count as matches.
# Tune on real data: this value has not been checked against production embeddings yet.
MAX_COSINE_DISTANCE = 0.65


class SearchHit(BaseModel):
    id: str
    title: str
    snippet: str
    url: str
    source: str
    published_at: datetime | None
    score: float
    sentiment: str | None


class SearchResult(BaseModel):
    results: list[SearchHit]
    count: int
    freshness: datetime | None
    mode_used: SearchMode


def article_public_id(article_id: int) -> str:
    return f"a_{article_id}"


def parse_article_id(public_id: str) -> int | None:
    raw = public_id.removeprefix("a_")
    return int(raw) if raw.isdigit() else None


def rrf_merge(*rankings: list[int]) -> list[tuple[int, float]]:
    """Reciprocal rank fusion: reward items ranked high in any list, more so when in several."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1 / (RRF_K + rank)
    return sorted(scores.items(), key=lambda kv: (-kv[1], -kv[0]))


class SearchService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        llm: GeminiClient | None = None,
        redis: Redis | None = None,
        query_embed_timeout_s: float = 1.5,
    ) -> None:
        self._sessions = sessions
        self._llm = llm
        self._redis = redis
        self._embed_timeout = query_embed_timeout_s

    async def search(
        self,
        *,
        query: str,
        limit: int = 10,
        offset: int = 0,
        from_date: date | None = None,
        to_date: date | None = None,
        sources: list[str] | None = None,
        sentiment: SentimentLabel | None = None,
        mode: SearchMode = "keyword",
    ) -> SearchResult:
        filters = self._filters(from_date, to_date, sources, sentiment)
        if mode != "keyword":
            vector = await self._query_vector(query)
            if vector is not None:
                result = await self._vector_search(query, vector, filters, limit, offset, mode)
                if result is not None:
                    return result
        return await self._keyword_search(query, filters, limit, offset)

    async def latest(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
        sources: list[str] | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
        sentiment: SentimentLabel | None = None,
    ) -> SearchResult:
        filters = self._filters(from_date, to_date, sources, sentiment)
        async with self._sessions() as s:
            total = await s.scalar(select(func.count()).select_from(Article).join(Source).where(and_(*filters)))
            rows = (
                await s.execute(
                    select(Article, Source.code)
                    .join(Source)
                    .where(and_(*filters))
                    .order_by(Article.published_at.desc().nullslast(), Article.id.desc())
                    .limit(limit)
                    .offset(offset)
                )
            ).all()
            freshness = await s.scalar(select(func.max(Source.last_crawled_at)))
        return SearchResult(
            results=[self._hit(a, code, 0.0) for a, code in rows],
            count=total or 0,
            freshness=freshness,
            mode_used="keyword",
        )

    async def _keyword_search(self, query: str, filters: list, limit: int, offset: int) -> SearchResult:
        tsq = self._tsquery(query)
        rank = func.ts_rank_cd(Article.tsv, tsq)
        where = and_(Article.tsv.op("@@")(tsq), *filters)
        async with self._sessions() as s:
            total = await s.scalar(select(func.count()).select_from(Article).join(Source).where(where))
            rows = (
                await s.execute(
                    select(Article, Source.code, rank.label("rank"))
                    .join(Source)
                    .where(where)
                    .order_by(rank.desc(), Article.published_at.desc().nullslast())
                    .limit(limit)
                    .offset(offset)
                )
            ).all()
            freshness = await s.scalar(select(func.max(Source.last_crawled_at)))
        top = max((float(r.rank) for r in rows), default=0.0) or 1.0
        return SearchResult(
            results=[self._hit(a, code, float(r) / top) for a, code, r in rows],
            count=total or 0,
            freshness=freshness,
            mode_used="keyword",
        )

    async def _vector_search(
        self, query: str, vector: list[float], filters: list, limit: int, offset: int, mode: SearchMode
    ) -> SearchResult | None:
        """None means no article has embeddings matching the filters, so the caller should use keyword search."""
        async with self._sessions() as s:
            semantic = await self._semantic_ranking(s, vector, filters)
            if not semantic:
                return None
            ranked: list[tuple[int, float]]
            if mode == "semantic":
                ranked = [(article_id, 1.0 - distance) for article_id, distance in semantic]
            else:
                tsq = self._tsquery(query)
                keyword = (
                    await s.scalars(
                        select(Article.id)
                        .join(Source)
                        .where(Article.tsv.op("@@")(tsq), *filters)
                        .order_by(func.ts_rank_cd(Article.tsv, tsq).desc())
                        .limit(CANDIDATE_POOL)
                    )
                ).all()
                merged = rrf_merge(list(keyword), [article_id for article_id, _ in semantic])
                top = merged[0][1]
                ranked = [(article_id, score / top) for article_id, score in merged]

            page = ranked[offset : offset + limit]
            ids = [article_id for article_id, _ in page]
            rows = (await s.execute(select(Article, Source.code).join(Source).where(Article.id.in_(ids)))).all()
            freshness = await s.scalar(select(func.max(Source.last_crawled_at)))

        by_id = {a.id: (a, code) for a, code in rows}
        return SearchResult(
            results=[self._hit(*by_id[i], score) for i, score in page if i in by_id],
            count=len(ranked),
            freshness=freshness,
            mode_used=mode,
        )

    @staticmethod
    async def _semantic_ranking(s: AsyncSession, vector: list[float], filters: list) -> list[tuple[int, float]]:
        # The HNSW index returns at most ef_search rows (default 40), which is too few once rows are grouped.
        await s.execute(text(f"SET LOCAL hnsw.ef_search = {CHUNK_POOL}"))
        distance = ArticleChunk.embedding.cosine_distance(vector).label("distance")
        nearest = select(ArticleChunk.article_id, distance).order_by(distance).limit(CHUNK_POOL).subquery()
        close_enough = nearest.c.distance < MAX_COSINE_DISTANCE
        best = func.min(nearest.c.distance).label("best")
        rows = await s.execute(
            select(nearest.c.article_id, best)
            .join(Article, Article.id == nearest.c.article_id)
            .join(Source, Source.id == Article.source_id)
            .where(close_enough, *filters)
            .group_by(nearest.c.article_id)
            .order_by(best)
            .limit(CANDIDATE_POOL)
        )
        return [(article_id, float(d)) for article_id, d in rows.all()]

    async def _query_vector(self, query: str) -> list[float] | None:
        if self._llm is None or not self._llm.configured:
            return None
        key = f"cache:qvec:{self._llm.embedding_model}:{hashlib.sha1(query.encode()).hexdigest()}"
        if self._redis is not None:
            try:
                cached = await self._redis.get(key)
                if cached:
                    return json.loads(cached)
            except Exception:  # noqa: BLE001 — the cache is optional
                log.debug("query vector cache read failed", exc_info=True)
        try:
            [vector] = await self._llm.embed([query_text(query)], timeout_s=self._embed_timeout)
        except LLMError as exc:
            log.warning("query embedding failed, using keyword search: %s", exc)
            return None
        if self._redis is not None:
            try:
                await self._redis.set(key, json.dumps(vector), ex=QUERY_VECTOR_TTL_S)
            except Exception:  # noqa: BLE001
                log.debug("query vector cache write failed", exc_info=True)
        return vector

    @staticmethod
    def _tsquery(query: str):
        return func.websearch_to_tsquery("simple", func.immutable_unaccent(query))

    @staticmethod
    def _filters(
        from_date: date | None, to_date: date | None, sources: list[str] | None, sentiment: SentimentLabel | None
    ) -> list:
        filters = [Article.duplicate_of.is_(None)]
        if from_date:
            filters.append(Article.published_at >= datetime.combine(from_date, datetime.min.time(), VN_TZ))
        if to_date:
            end = datetime.combine(to_date, datetime.min.time(), VN_TZ) + timedelta(days=1)
            filters.append(Article.published_at < end)
        if sources:
            filters.append(Source.code.in_(sources))
        if sentiment:
            filters.append(Article.sentiment_label == sentiment)
        return filters

    @staticmethod
    def _hit(a: Article, source_code: str, score: float) -> SearchHit:
        snippet = a.summary or (a.content or "")[:300]
        return SearchHit(
            id=article_public_id(a.id),
            title=a.title,
            snippet=snippet[:300],
            url=a.url,
            source=source_code,
            published_at=a.published_at,
            score=round(score, 4),
            sentiment=a.sentiment_label,
        )
