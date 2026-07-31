"""Add rio_records.descricao_batch_id / descricao_batch_submitted_at — batched-copy lane.

Batch state for the deferred description lane (brave/lanes/atrativos/copy_batch.py) lives in
REAL COLUMNS, not in the `normalized` JSONB, for two reasons:

  1. Lost update. A writer that reads `normalized` into a local dict, performs seconds of
     network I/O, then writes the whole JSONB column back from that stale snapshot would
     silently erase a stamp living inside it, and the same records would be submitted — and
     BILLED — a second time. PlacesEnrichmentAgent.run did exactly that (it now merges under
     a row lock); a separate column cannot be clobbered by the next writer that forgets.
  2. Queryability. The collector derives its in-flight worklist from
     ``SELECT DISTINCT descricao_batch_id``, so the DB is the single source of truth for both
     "do not resubmit" and "still to collect" (there is no Redis worklist to lose).

descricao_batch_id carries a PARTIAL index (WHERE descricao_batch_id IS NOT NULL). The column
is NULL for essentially every row, so a full B-tree would carry one entry per rio_record while
still not serving submit's ``IS NULL`` predicate (the planner seq-scans regardless). The
partial index holds only the few hundred in-flight rows, which is exactly what collect's
``SELECT DISTINCT descricao_batch_id`` reads.

DEPLOY COST — partial does NOT make this cheap to build. ``create_index`` without
``postgresql_concurrently`` takes a SHARE lock on rio_records and holds it for the whole
build, and the build must still SCAN every row to evaluate the predicate: what the partial
predicate shrinks is the resulting index, not the work or the lock. So on a whole-Brazil
rio_records this migration blocks every INSERT/UPDATE from the running pipeline workers for
the duration of one full table scan. CONCURRENTLY is not usable here — it cannot run inside
Alembic's transaction — so this is a real tradeoff to plan for (deploy in a quiet window, or
build the index by hand with CREATE INDEX CONCURRENTLY outside the migration and let this
step find it already there), not something the partial predicate removes.

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-30
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "rio_records",
        sa.Column("descricao_batch_id", sa.String(64), nullable=True),
    )
    op.add_column(
        "rio_records",
        sa.Column("descricao_batch_submitted_at", sa.DateTime(timezone=True), nullable=True),
    )
    # PARTIAL — see the module docstring, including the deploy cost: this still scans
    # rio_records under a SHARE lock. Must stay in step with RioRecord.__table_args__ or
    # `alembic check` reports drift.
    op.create_index(
        "ix_rio_records_descricao_batch_id",
        "rio_records",
        ["descricao_batch_id"],
        postgresql_where=sa.text("descricao_batch_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_rio_records_descricao_batch_id", table_name="rio_records")
    op.drop_column("rio_records", "descricao_batch_submitted_at")
    op.drop_column("rio_records", "descricao_batch_id")
