"""rio_records.sub_state_changed_at — when the atrativo FSM last moved the record.

Stamped by the ORM listener in brave/core/models.py on every sub_state change; read by
brave.redispatch_stalled_chain to find records stuck mid-chain. Nullable, no backfill:
NULL means "unknown", and the sweeper treats it as old enough. No new index — the
sweeper filters on sub_state first, which ix_rio_records_sub_state already serves.

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-25
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "rio_records",
        sa.Column("sub_state_changed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("rio_records", "sub_state_changed_at")
