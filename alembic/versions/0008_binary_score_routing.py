"""Binary score routing — backfill descarte→dlq and drop rio_records.mar_ready.

Phase B (score binary) collapses the reliability score engine to a two-way gate:
score >= threshold_mar → "mar", else → "dlq" (no descarte band, no mar_ready
promote-override). Two schema/data changes follow:

1. Backfill any score-band "descarte" rows to "dlq". routing is a plain
   VARCHAR(32) (String(32) in models.py, created in 0001) — NOT a PG enum — so
   this is a bare UPDATE with NO ALTER TYPE. "descarte" remains a legal routing
   VALUE for the non-score paths (hard-descarte for CLOSED places, steward reject,
   dedup discard); this migration only re-homes the rows the score engine used to
   emit into "descarte".

2. Drop the mar_ready column + its index (added in 0006). The promote-override
   feature that consumed it was removed — a validated attraction now reaches Mar
   directly through validate_and_promote_rio under the binary threshold.

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-02
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # 1. Backfill score-band descarte → dlq FIRST (before dropping the column).
    #    routing is String(32), not an enum — no ALTER TYPE required.
    op.execute("UPDATE rio_records SET routing='dlq' WHERE routing='descarte'")

    # 2. Drop the mar_ready index then the column (mirror-reverse of 0006).
    op.drop_index("ix_rio_records_mar_ready", table_name="rio_records")
    op.drop_column("rio_records", "mar_ready")


def downgrade() -> None:
    # Re-add the mar_ready column + index (mirrors 0006 upgrade()).
    # NOTE: the descarte→dlq backfill is intentionally NOT reversed — this
    # downgrade cannot distinguish rows that were originally "descarte" from
    # rows that were always "dlq", so they remain "dlq".
    op.add_column(
        "rio_records",
        sa.Column("mar_ready", sa.Boolean, nullable=False, server_default="false"),
    )
    op.create_index("ix_rio_records_mar_ready", "rio_records", ["mar_ready"])
