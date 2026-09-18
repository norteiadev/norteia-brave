"""mar_records.push_hash / pushed_at — skip the POST when the payload is unchanged.

push_hash is the sha256 of the last payload norteia-api accepted (2xx) for the row;
the push tasks compare it before POSTing. Both nullable: NULL means "never pushed",
so every existing row is pushed once more and stamped then. No backfill.

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-18
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("mar_records", sa.Column("push_hash", sa.String(64), nullable=True))
    op.add_column(
        "mar_records", sa.Column("pushed_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("mar_records", "pushed_at")
    op.drop_column("mar_records", "push_hash")
