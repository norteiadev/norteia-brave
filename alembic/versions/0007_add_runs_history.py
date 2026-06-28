"""Add runs_history — durable engine-sweep run trail (UI-PAINEL-2, Varreduras).

Engine runs lived only in Redis until now (no DB audit trail). This migration
adds the persisted `runs_history` envelope table written at engine-start and
finalized when the orchestrator returns to idle. The Varreduras dashboard view
lists these runs (filter by uf/source/depth) and recomputes synced/failed
on-read over each run's time window (RESEARCH #2).

Mirrors 0006_add_rio_mar_ready.py: plain (non-CONCURRENTLY) index inside the
Alembic transaction.

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-28
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "runs_history",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ufs", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("depth", sa.String(32), nullable=False),
        sa.Column("lane", sa.String(32), server_default="both", nullable=False),
        sa.Column("ufs_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ufs_dispatched", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total", sa.Integer(), nullable=True),
        sa.Column("synced", sa.Integer(), nullable=True),
        sa.Column("failed", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="running"),
    )
    # Standard B-tree index — not CONCURRENTLY (inside the Alembic transaction),
    # mirroring 0006. The list endpoint orders/filters by started_at.
    op.create_index("ix_runs_history_started_at", "runs_history", ["started_at"])


def downgrade() -> None:
    op.drop_index("ix_runs_history_started_at", table_name="runs_history")
    op.drop_table("runs_history")
