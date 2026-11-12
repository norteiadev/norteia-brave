"""Editorial claims: bounded web validation attempts.

editorial_claims  + ``validation_attempts`` (failed attempts so far; 0 default)
                  + ``last_validation_error`` (why the last attempt failed)
                  + ``last_attempt_at``
                  A claim that fails ``editorial_validate_max_attempts`` times gets status
                  ``validation_failed``: kept, never sent, never paid for again by itself.

Revision ID: 0027
Revises: 0026
Create Date: 2026-11-12
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "editorial_claims",
        sa.Column("validation_attempts", sa.Integer, nullable=False, server_default="0"),
    )
    op.add_column("editorial_claims", sa.Column("last_validation_error", sa.Text, nullable=True))
    op.add_column(
        "editorial_claims", sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("editorial_claims", "last_attempt_at")
    op.drop_column("editorial_claims", "last_validation_error")
    op.drop_column("editorial_claims", "validation_attempts")
