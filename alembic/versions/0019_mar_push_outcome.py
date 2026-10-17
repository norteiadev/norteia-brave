"""mar_records.push_attempted_at / push_error / retracted_at — outbox outcome.

push_attempted_at: last publish attempt; the 15-min re-dispatch skips rows attempted
recently, so overlapping retry chains of the same row stop piling up (F65).
push_error: norteia-api's permanent 4xx rejection; the row leaves the dispatch window for
a day instead of hogging the 500-row cap forever (F46).
retracted_at: the row was hidden on norteia-api (visibility=hidden) because its Rio left
'mar' (F47). All nullable, no backfill: NULL = never attempted / not rejected / visible.

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-17
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "mar_records", sa.Column("push_attempted_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("mar_records", sa.Column("push_error", sa.Text(), nullable=True))
    op.add_column(
        "mar_records", sa.Column("retracted_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("mar_records", "retracted_at")
    op.drop_column("mar_records", "push_error")
    op.drop_column("mar_records", "push_attempted_at")
