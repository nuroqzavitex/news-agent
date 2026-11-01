"""Nạp/cập nhật danh sách nguồn từ sources.yaml (idempotent). Chạy: python -m scripts.seed_sources"""

import asyncio
from pathlib import Path

import yaml
from sqlalchemy.dialects.postgresql import insert

from app.db.models import Source
from app.db.session import make_engine, make_session_factory
from app.settings import get_settings

SOURCES_FILE = Path(__file__).resolve().parent.parent / "sources.yaml"


async def main() -> None:
    data = yaml.safe_load(SOURCES_FILE.read_text(encoding="utf-8"))
    engine = make_engine(get_settings().database_url)
    sessions = make_session_factory(engine)
    async with sessions() as s:
        for src in data["sources"]:
            stmt = insert(Source).values(**src)
            stmt = stmt.on_conflict_do_update(
                index_elements=[Source.code],
                set_={k: stmt.excluded[k] for k in ("name", "rss_url", "category")},
            )
            await s.execute(stmt)
        await s.commit()
    await engine.dispose()
    print(f"seeded {len(data['sources'])} sources")


if __name__ == "__main__":
    asyncio.run(main())
