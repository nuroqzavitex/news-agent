"""Knowledge learned from other agents, with provenance, versions, TTL and conflicts (spec §7.1)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from app.db.models import KnowledgeItem

PURGE_AFTER = timedelta(days=7)
# Fields that differ on every answer and say nothing about whether two agents agree.
VOLATILE_FIELDS = {"as_of", "timestamp", "confidence"}


def comparable(content: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in content.items() if k not in VOLATILE_FIELDS}


@dataclass
class Recall:
    best: KnowledgeItem | None = None
    stale: bool = False  # `best` has expired: use it only when it cannot be refreshed
    conflicting: list[KnowledgeItem] = field(default_factory=list)  # current items that disagree with `best`

    @property
    def fresh(self) -> bool:
        return self.best is not None and not self.stale


class KnowledgeStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def remember(
        self,
        *,
        key: str,
        content: dict[str, Any],
        source_agent: str,
        source_message_id: str | None = None,
        confidence: float = 0.5,
        ttl_s: int = 900,
    ) -> tuple[KnowledgeItem, bool]:
        """Store what an agent said. Returns the item and whether it contradicts another agent's current answer."""
        now = datetime.now(UTC)
        async with self._sessions() as s, s.begin():
            # Replicas may learn the same key at once; serialize per key.
            await s.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(key, 0))))
            current = (
                await s.scalars(
                    select(KnowledgeItem).where(KnowledgeItem.key == key, KnowledgeItem.superseded_by.is_(None))
                )
            ).all()
            previous = [i for i in current if i.source_agent == source_agent]
            others = [i for i in current if i.source_agent != source_agent and (i.expires_at or now) > now]
            disagree = [i for i in others if comparable(i.content) != comparable(content)]

            group = next((i.conflict_group for i in disagree if i.conflict_group), None)
            if disagree and group is None:
                group = uuid.uuid4()
            for item in disagree:
                item.conflict_group = group

            item = KnowledgeItem(
                key=key,
                content=content,
                source_agent=source_agent,
                source_message_id=source_message_id,
                version=max((i.version for i in previous), default=0) + 1,
                confidence=confidence,
                expires_at=now + timedelta(seconds=ttl_s),
                conflict_group=group,
            )
            s.add(item)
            await s.flush()
            for old in previous:
                old.superseded_by = item.id
        return item, bool(disagree)

    async def recall(self, key: str) -> Recall:
        """The answer to use now: fresh beats stale, then higher confidence, then newer."""
        now = datetime.now(UTC)
        async with self._sessions() as s:
            current = (
                await s.scalars(
                    select(KnowledgeItem).where(KnowledgeItem.key == key, KnowledgeItem.superseded_by.is_(None))
                )
            ).all()
        if not current:
            return Recall()

        def is_fresh(item: KnowledgeItem) -> bool:
            return item.expires_at is None or item.expires_at > now

        fresh = [i for i in current if is_fresh(i)]
        pool = fresh or list(current)
        best = max(pool, key=lambda i: (i.confidence, i.created_at))
        conflicting = [
            i
            for i in fresh
            if i.source_agent != best.source_agent and comparable(i.content) != comparable(best.content)
        ]
        return Recall(best=best, stale=not fresh, conflicting=conflicting)

    async def versions(self, key: str) -> list[KnowledgeItem]:
        async with self._sessions() as s:
            rows = await s.scalars(
                select(KnowledgeItem)
                .where(KnowledgeItem.key == key)
                .order_by(KnowledgeItem.created_at.desc(), KnowledgeItem.id.desc())
            )
            return list(rows.all())

    async def list_items(self, *, limit: int = 100, include_superseded: bool = False) -> list[KnowledgeItem]:
        query = select(KnowledgeItem).order_by(KnowledgeItem.created_at.desc(), KnowledgeItem.id.desc()).limit(limit)
        if not include_superseded:
            query = query.where(KnowledgeItem.superseded_by.is_(None))
        async with self._sessions() as s:
            return list((await s.scalars(query)).all())

    async def purge_expired(self) -> int:
        """Delete items expired for over 7 days. An item another row still points to goes on a later pass."""
        newer = aliased(KnowledgeItem)
        async with self._sessions() as s, s.begin():
            referenced = exists().where(newer.superseded_by == KnowledgeItem.id)
            doomed = select(KnowledgeItem).where(
                KnowledgeItem.expires_at < datetime.now(UTC) - PURGE_AFTER, ~referenced
            )
            rows = (await s.scalars(doomed)).all()
            for row in rows:
                await s.delete(row)
        return len(rows)

