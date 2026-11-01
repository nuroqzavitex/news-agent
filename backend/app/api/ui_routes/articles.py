"""REST cho dashboard: bài báo, nguồn, crawl thủ công."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select

from app.api.deps import get_state
from app.db.models import Article, Source
from app.services.search import SearchResult, article_public_id, parse_article_id
from app.state import AppState
from app.workers.scheduler import enqueue_crawl

router = APIRouter(prefix="/api/v1", tags=["news"])


@router.get("/articles", response_model=SearchResult)
async def list_articles(
    q: str | None = None,
    source: list[str] | None = Query(default=None),
    from_: date | None = Query(default=None, alias="from"),
    to: date | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    state: AppState = Depends(get_state),
) -> SearchResult:
    offset = (page - 1) * page_size
    if q:
        return await state.search.search(query=q, limit=page_size, offset=offset,
                                         from_date=from_, to_date=to, sources=source)
    return await state.search.latest(limit=page_size, offset=offset, sources=source,
                                     from_date=from_, to_date=to)


class ArticleDetail(BaseModel):
    id: str
    title: str
    url: str
    source: str
    summary: str | None
    content: str | None
    author: str | None
    category: str | None
    published_at: datetime | None
    crawled_at: datetime
    sentiment_label: str | None
    sentiment_score: float | None
    duplicate_of: str | None
    status: str


@router.get("/articles/{article_id}", response_model=ArticleDetail)
async def get_article(article_id: str, state: AppState = Depends(get_state)) -> ArticleDetail:
    pk = parse_article_id(article_id)
    if pk is None:
        raise HTTPException(404, "article not found")
    async with state.sessions() as s:
        row = (await s.execute(select(Article, Source.code).join(Source).where(Article.id == pk))).first()
    if row is None:
        raise HTTPException(404, "article not found")
    a, code = row
    return ArticleDetail(
        id=article_public_id(a.id), title=a.title, url=a.url, source=code, summary=a.summary,
        content=a.content, author=a.author, category=a.category, published_at=a.published_at,
        crawled_at=a.crawled_at, sentiment_label=a.sentiment_label, sentiment_score=a.sentiment_score,
        duplicate_of=article_public_id(a.duplicate_of) if a.duplicate_of else None, status=a.status,
    )


class SourceOut(BaseModel):
    code: str
    name: str
    rss_url: str
    category: str | None
    enabled: bool
    last_crawled_at: datetime | None
    last_status: str | None


@router.get("/sources", response_model=list[SourceOut])
async def list_sources(state: AppState = Depends(get_state)) -> list[SourceOut]:
    async with state.sessions() as s:
        rows = (await s.scalars(select(Source).order_by(Source.code))).all()
    return [SourceOut.model_validate(r, from_attributes=True) for r in rows]


class CrawlTrigger(BaseModel):
    source_code: str | None = None


@router.post("/crawl/trigger")
async def trigger_crawl(body: CrawlTrigger, state: AppState = Depends(get_state)) -> dict:
    async with state.sessions() as s:
        query = select(Source.code).where(Source.enabled.is_(True))
        if body.source_code:
            query = query.where(Source.code == body.source_code)
        codes = (await s.scalars(query)).all()
    if body.source_code and not codes:
        raise HTTPException(404, f"source {body.source_code} không tồn tại hoặc đang tắt")
    for code in codes:
        await enqueue_crawl(state.redis, code)
    return {"enqueued": list(codes)}


class SourceActivity(BaseModel):
    code: str
    name: str
    counts: list[int]
    partial: list[int]
    last_crawled_at: datetime | None
    last_status: str | None


class CrawlGrid(BaseModel):
    hours: list[datetime]
    sources: list[SourceActivity]


@router.get("/stats/crawl-grid", response_model=CrawlGrid)
async def crawl_grid(hours: int = Query(default=24, ge=1, le=168), state: AppState = Depends(get_state)) -> CrawlGrid:
    """Articles stored per source per hour (by crawled_at), oldest hour first."""
    end = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    buckets = [end - timedelta(hours=hours - 1 - i) for i in range(hours)]
    index = {b: i for i, b in enumerate(buckets)}
    hour = func.date_trunc("hour", Article.crawled_at).label("hour")
    async with state.sessions() as s:
        sources = (await s.scalars(select(Source).order_by(Source.code))).all()
        rows = (await s.execute(
            select(Article.source_id, hour, func.count(), func.count().filter(Article.status == "partial"))
            .where(Article.crawled_at >= buckets[0])
            .group_by(Article.source_id, hour)
        )).all()
    activity = {
        src.id: SourceActivity(code=src.code, name=src.name, counts=[0] * hours, partial=[0] * hours,
                               last_crawled_at=src.last_crawled_at, last_status=src.last_status)
        for src in sources
    }
    for source_id, bucket, total, partial in rows:
        i = index.get(bucket.astimezone(UTC))
        if i is not None and source_id in activity:
            activity[source_id].counts[i] = total
            activity[source_id].partial[i] = partial
    return CrawlGrid(hours=buckets, sources=list(activity.values()))
