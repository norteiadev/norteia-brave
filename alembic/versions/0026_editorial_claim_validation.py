"""Editorial claims: the web validation of perishable facts.

editorial_claims  + ``validation_verdict`` (confirmado | desatualizado | sem_evidencia)
                  + ``validation_sources`` (JSON: URLs the model cited + the pages the Google
                    Search grounding rests on)
                  + ``validation_note`` (short note from the validator)
                  + ``validated_at``
                  ``status`` widens from 16 to 24 characters: ``pending_validation`` (18),
                  ``outdated``, ``unverified``, ``held_copied`` join the existing values.

Nothing is deleted: a claim the validation rejects changes status and keeps its row.

DEPLOY: the rule that parks perishable facts of old posts lives in the resolver, not here.
Run ``python -m brave.cli editorial-resolve --all`` after this upgrade and before any
Publicação, or the Rio / destination outbox keep sending the old values (docs/RUNBOOK-EXECUCAO.md).

Revision ID: 0026
Revises: 0025
Create Date: 2026-11-12
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("editorial_claims", sa.Column("validation_verdict", sa.String(16), nullable=True))
    op.add_column("editorial_claims", sa.Column("validation_sources", sa.JSON, nullable=True))
    op.add_column("editorial_claims", sa.Column("validation_note", sa.Text, nullable=True))
    op.add_column(
        "editorial_claims", sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.alter_column(
        "editorial_claims",
        "status",
        existing_type=sa.String(16),
        type_=sa.String(24),
        existing_nullable=False,
    )


def downgrade() -> None:
    # Shrinking the status column fails loudly if a longer status (pending_validation) is
    # still stored: an operator resolves those claims first, nothing is truncated.
    op.alter_column(
        "editorial_claims",
        "status",
        existing_type=sa.String(24),
        type_=sa.String(16),
        existing_nullable=False,
    )
    op.drop_column("editorial_claims", "validated_at")
    op.drop_column("editorial_claims", "validation_note")
    op.drop_column("editorial_claims", "validation_sources")
    op.drop_column("editorial_claims", "validation_verdict")
