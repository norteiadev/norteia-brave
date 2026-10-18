"""runs_history.max_per_uf — the operator's per-UF cap of an engine run.

Written by brave.engine_sweep_run; read by PATCH /api/v1/runs/{id}/reprocess so a capped
test run is reprocessed with its cap (it used to come back as a full, uncapped, paid sweep).
Nullable, no backfill: NULL = uncapped (and unknown for older runs).

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-17
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("runs_history", sa.Column("max_per_uf", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("runs_history", "max_per_uf")
