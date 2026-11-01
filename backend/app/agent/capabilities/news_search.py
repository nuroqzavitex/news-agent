"""news_search (spec §5.1). M1: keyword (Postgres FTS). semantic/hybrid bật từ M4."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from app.agent.capabilities import CapabilityContext, capability
from app.services.search import SearchService


class NewsSearchPayload(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=10, ge=1, le=50)
    from_date: date | None = None
    to_date: date | None = None
    sources: list[str] | None = None
    mode: Literal["keyword", "semantic", "hybrid"] = "hybrid"


@capability("news_search", NewsSearchPayload)
async def news_search(p: NewsSearchPayload, ctx: CapabilityContext) -> dict:
    search: SearchService = ctx.deps["search"]
    result = await search.search(
        query=p.query,
        limit=p.limit,
        from_date=p.from_date,
        to_date=p.to_date,
        sources=p.sources,
        mode=p.mode,
    )
    return result.model_dump(mode="json")
