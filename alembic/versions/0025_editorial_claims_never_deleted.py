"""Editorial claims are never deleted: a re-extraction supersedes them.

editorial_claims  + ``version_id`` (the post version the claim was extracted from)
                  + ``superseded_at`` (set when a later extraction replaced it; status
                    ``superseded``)
                  - ``ON DELETE CASCADE`` of document_url → editorial_documents: deleting a
                    post must fail while claims reference it, never take them with it

Existing claims point to their document's current version.

Revision ID: 0025
Revises: 0024
Create Date: 2026-11-12
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | None = None
depends_on: str | None = None

_FK = "editorial_claims_document_url_fkey"


def upgrade() -> None:
    op.add_column("editorial_claims", sa.Column("version_id", sa.UUID, nullable=True))
    op.add_column(
        "editorial_claims", sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_foreign_key(
        "fk_editorial_claims_version",
        "editorial_claims",
        "editorial_document_versions",
        ["version_id"],
        ["id"],
    )
    op.execute(
        """
        UPDATE editorial_claims c
           SET version_id = d.current_version_id
          FROM editorial_documents d
         WHERE d.url = c.document_url
        """
    )
    op.drop_constraint(_FK, "editorial_claims", type_="foreignkey")
    op.create_foreign_key(_FK, "editorial_claims", "editorial_documents", ["document_url"], ["url"])


def downgrade() -> None:
    op.drop_constraint(_FK, "editorial_claims", type_="foreignkey")
    op.create_foreign_key(
        _FK,
        "editorial_claims",
        "editorial_documents",
        ["document_url"],
        ["url"],
        ondelete="CASCADE",
    )
    op.drop_constraint("fk_editorial_claims_version", "editorial_claims", type_="foreignkey")
    op.drop_column("editorial_claims", "superseded_at")
    op.drop_column("editorial_claims", "version_id")
