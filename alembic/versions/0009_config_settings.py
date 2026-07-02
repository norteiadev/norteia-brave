"""config_settings — operator-tunable runtime config overlay (Phase D).

Adds the sparse ``config_settings`` key→value table that overlays the
env/AppConfig defaults at runtime (brave/config/runtime.py::load_effective_config).
Each row is one dotted config key (e.g. "score.threshold_mar",
"source.tripadvisor.enabled", "engine.mode") with a JSON ``{"v": <any>}`` value
wrapper. Absent rows → the effective config equals the env defaults
(behavior-neutral); the idempotent seed writes rows equal to the current
env-effective values so a seeded base behaves identically to an empty one.

FIRST table in the repo with an ``updated_at`` column: ``server_default now()``
on INSERT. The ORM model adds an ``onupdate=now()`` bump (ORM-flush level, NOT a
DB trigger), which is intentionally NOT emitted into this DDL — raw SQL UPDATEs
will not bump the column, only ORM flushes do.

Mirrors 0007_add_runs_history.py (plain create_table inside the Alembic txn).

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-02
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "config_settings",
        sa.Column("key", sa.String(128), primary_key=True, nullable=False),
        # Always the {"v": <any>} wrapper — never NULL (see the ORM model docstring).
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("updated_by", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("config_settings")
