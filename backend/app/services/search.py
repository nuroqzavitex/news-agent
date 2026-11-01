"""Tìm bài báo. M1: keyword bằng Postgres FTS (có/không dấu). M4 thêm semantic (pgvector) + hybrid (RRF)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import Select, and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Article, Source

VN_TZ = timezone(timedelta(hours=7))
SearchMode = Literal["keyword", "semantic", "hybrid"]


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


class SearchService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def search(
        self,
        *,
        query: str,
        limit: int = 10,
        offset: int = 0,
        from_date: date | None = None,
        to_date: date | None = None,
        sources: list[str] | None = None,
        mode: SearchMode = "keyword",
    ) -> SearchResult:
        # semantic/hybrid cần embedding (M4) → tạm thời dùng keyword, báo mode_used để bên gọi biết.
        tsq = func.websearch_to_tsquery("simple", func.immutable_unaccent(query))
        rank = func.ts_rank_cd(Article.tsv, tsq)
        filters = [Article.tsv.op("@@")(tsq), Article.duplicate_of.is_(None)]
        filters += self._common_filters(from_date, to_date, sources)

        base: Select = select(Article, Source.code, rank.label("rank")).join(Source).where(and_(*filters))
        async with self._sessions() as s:
            total = await s.scalar(
                select(func.count()).select_from(Article).join(Source).where(and_(*filters))
            )
            rows = (
                await s.execute(
                    base.order_by(rank.desc(), Article.published_at.desc().nullslast())
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

    async def latest(
        self, *, limit: int = 20, offset: int = 0, sources: list[str] | None = None,
        from_date: date | None = None, to_date: date | None = None,
    ) -> SearchResult:
        filters = [Article.duplicate_of.is_(None), *self._common_filters(from_date, to_date, sources)]
        async with self._sessions() as s:
            total = await s.scalar(
                select(func.count()).select_from(Article).join(Source).where(and_(*filters))
            )
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

    @staticmethod
    def _common_filters(from_date: date | None, to_date: date | None, sources: list[str] | None) -> list:
        filters = []
        if from_date:
            filters.append(Article.published_at >= datetime.combine(from_date, datetime.min.time(), VN_TZ))
        if to_date:
            end = datetime.combine(to_date, datetime.min.time(), VN_TZ) + timedelta(days=1)
            filters.append(Article.published_at < end)
        if sources:
            filters.append(Source.code.in_(sources))
        return filters

    @staticmethod
    def _hit(a: Article, source_code: str, score: float) -> SearchHit:
        text = a.summary or (a.content or "")[:300]
        return SearchHit(
            id=article_public_id(a.id),
            title=a.title,
            snippet=text[:300],
            url=a.url,
            source=source_code,
            published_at=a.published_at,
            score=round(score, 4),
            sentiment=a.sentiment_label,
        )
