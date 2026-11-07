"""enrichment state on articles (embedding + sentiment are filled in by the enricher worker)

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-07
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE articles ADD COLUMN sentiment_source TEXT")
    op.execute("ALTER TABLE articles ADD COLUMN enriched_at TIMESTAMPTZ")
    op.execute("ALTER TABLE articles ADD COLUMN enrich_attempts INT NOT NULL DEFAULT 0")
    op.execute("CREATE INDEX articles_pending_enrich_idx ON articles (id DESC) WHERE enriched_at IS NULL")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS articles_pending_enrich_idx")
    op.execute("ALTER TABLE articles DROP COLUMN enrich_attempts")
    op.execute("ALTER TABLE articles DROP COLUMN enriched_at")
    op.execute("ALTER TABLE articles DROP COLUMN sentiment_source")
