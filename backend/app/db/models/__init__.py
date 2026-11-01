"""ORM models (spec §7). DDL chính thức nằm trong migrations/ — giữ hai bên khớp nhau."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

EMBEDDING_DIM = 768

TSV_EXPR = (
    "setweight(to_tsvector('simple', immutable_unaccent(coalesce(title, ''))), 'A') || "
    "setweight(to_tsvector('simple', immutable_unaccent(coalesce(summary, ''))), 'B') || "
    "setweight(to_tsvector('simple', immutable_unaccent(coalesce(content, ''))), 'C')"
)


def _now() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    rss_url: Mapped[str] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, server_default="true")
    last_crawled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_status: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()


class Article(Base):
    __tablename__ = "articles"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"))
    url: Mapped[str] = mapped_column(Text, unique=True)
    url_hash: Mapped[str] = mapped_column(Text, index=True)
    title: Mapped[str] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    content: Mapped[str | None] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    crawled_at: Mapped[datetime] = _now()
    simhash: Mapped[int | None] = mapped_column(BigInteger)
    duplicate_of: Mapped[int | None] = mapped_column(ForeignKey("articles.id"))
    sentiment_label: Mapped[str | None] = mapped_column(Text)
    sentiment_score: Mapped[float | None] = mapped_column(Float)
    tsv: Mapped[Any] = mapped_column(TSVECTOR, Computed(TSV_EXPR, persisted=True))
    lang: Mapped[str] = mapped_column(Text, server_default="vi")
    status: Mapped[str] = mapped_column(Text, server_default="processed")


class ArticleChunk(Base):
    __tablename__ = "article_chunks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"))
    chunk_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[Any] = mapped_column(Vector(EMBEDDING_DIM))


class KnowledgeItem(Base):
    __tablename__ = "knowledge_items"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    key: Mapped[str] = mapped_column(Text, index=True)
    content: Mapped[dict[str, Any]] = mapped_column(JSONB)
    source_agent: Mapped[str] = mapped_column(Text)
    source_message_id: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    confidence: Mapped[float] = mapped_column(Float, server_default="0.5")
    created_at: Mapped[datetime] = _now()
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_by: Mapped[int | None] = mapped_column(ForeignKey("knowledge_items.id"))
    conflict_group: Mapped[UUID | None] = mapped_column()


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    message_id: Mapped[str] = mapped_column(Text, unique=True)
    conversation_id: Mapped[str] = mapped_column(Text)
    trace_id: Mapped[str] = mapped_column(Text, index=True)
    direction: Mapped[str] = mapped_column(Text)
    from_agent: Mapped[str] = mapped_column(Text)
    to_agent: Mapped[str] = mapped_column(Text)
    type: Mapped[str] = mapped_column(Text)
    capability: Mapped[str | None] = mapped_column(Text)
    reply_to: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, server_default="1")
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class TraceEvent(Base):
    __tablename__ = "trace_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    trace_id: Mapped[str] = mapped_column(Text, index=True)
    message_id: Mapped[str | None] = mapped_column(Text)
    agent_id: Mapped[str] = mapped_column(Text)
    event: Mapped[str] = mapped_column(Text)
    attrs: Mapped[dict[str, Any]] = mapped_column(JSONB)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AgentCache(Base):
    __tablename__ = "agent_cache"

    agent_id: Mapped[str] = mapped_column(Text, primary_key=True)
    card: Mapped[dict[str, Any]] = mapped_column(JSONB)
    last_seen_at: Mapped[datetime] = _now()
    source: Mapped[str] = mapped_column(Text)
