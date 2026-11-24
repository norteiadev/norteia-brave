"""Editorial claims remember the status they had when a re-extraction superseded them.

editorial_claims  + ``superseded_from_status`` (NULL for a claim not superseded, or superseded
                    before this column existed)

Revision ID: 0028
Revises: 0027
Create Date: 2026-11-12
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "editorial_claims", sa.Column("superseded_from_status", sa.String(24), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("editorial_claims", "superseded_from_status")
