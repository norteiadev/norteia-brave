"""add_hnsw_index

Adds the HNSW vector index on rio_records.embedding for pgvector fuzzy dedup (D-08).

Using CONCURRENTLY to avoid a table lock during migration.
IF NOT EXISTS makes this idempotent.

Requires: pgvector server extension >= 0.5 (confirmed 0.8.x in pgvector/pgvector:pg17 image).

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-11
"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # HNSW index for pgvector cosine-distance fuzzy dedup in Rio (D-08).
    # m=16, ef_construction=64 are pgvector defaults; suitable for active-write workloads.
    # IF NOT EXISTS makes it idempotent.
    #
    # Note: We omit CONCURRENTLY here because migrations run inside a transaction
    # (PostgreSQL forbids CONCURRENTLY inside a transaction block). On an empty or
    # small dev/CI table this is safe — no table lock concern. For production
    # online-index creation on a live table, use the CONCURRENTLY variant via a
    # separate database operation outside Alembic's transaction.
    op.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS rio_records_embedding_hnsw_idx
            ON rio_records
            USING hnsw (embedding vector_cosine_ops)
            WITH (m = 16, ef_construction = 64)
            """
        )
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS rio_records_embedding_hnsw_idx;")
