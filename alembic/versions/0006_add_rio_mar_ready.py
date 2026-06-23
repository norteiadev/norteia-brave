"""Add rio_records.mar_ready — TA promote-override gate (TA-05).

Adds a Boolean column and B-tree index to rio_records so the API can
efficiently filter for attractions that are ready for steward promote-override.

The mar_ready flag is set by route_by_score (brave/core/rio/routing.py) for
TripAdvisor attractions whose atualidade + corroboracao bars are met. It is
never set by the caller; the score engine owns it exclusively (T-11-03-01).

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-23
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "rio_records",
        sa.Column("mar_ready", sa.Boolean, nullable=False, server_default="false"),
    )
    # Standard B-tree index — not CONCURRENTLY (inside Alembic transaction).
    # CONCURRENTLY cannot run inside a transaction; this is a new column on a
    # potentially large table — acceptable at migration time (offline window).
    op.create_index("ix_rio_records_mar_ready", "rio_records", ["mar_ready"])


def downgrade() -> None:
    op.drop_index("ix_rio_records_mar_ready", table_name="rio_records")
    op.drop_column("rio_records", "mar_ready")
