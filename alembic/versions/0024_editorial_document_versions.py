"""Editorial documents keep every version of a post (append-only raw).

editorial_document_versions  one row per download whose text differs from the one before:
                             raw page, raw KML per map, body, hash, published / modified dates
editorial_documents          gains ``current_version_id`` (the version the row mirrors)

Existing documents get an initial version built from their stored body (``html`` NULL: the
raw page was never kept) and point to it.

Revision ID: 0024
Revises: 0023
Create Date: 2026-11-12
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "editorial_document_versions",
        sa.Column("id", sa.UUID, primary_key=True),
        sa.Column(
            "document_url",
            sa.String(512),
            sa.ForeignKey("editorial_documents.url"),
            nullable=False,
        ),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("html", sa.Text, nullable=True),
        sa.Column("kml_raw", sa.JSON, nullable=False),
        sa.Column("body_text", sa.Text, nullable=False),
        sa.Column("published_time", sa.Date, nullable=True),
        sa.Column("modified_time", sa.Date, nullable=True),
        sa.Column(
            "fetched_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_editorial_document_versions_document_url",
        "editorial_document_versions",
        ["document_url"],
    )
    op.add_column("editorial_documents", sa.Column("current_version_id", sa.UUID, nullable=True))
    op.create_foreign_key(
        "fk_editorial_documents_current_version",
        "editorial_documents",
        "editorial_document_versions",
        ["current_version_id"],
        ["id"],
    )
    # Initial version of every document already stored: its body is all we have.
    op.execute(
        """
        INSERT INTO editorial_document_versions
            (id, document_url, content_hash, html, kml_raw, body_text, published_time,
             modified_time, fetched_at)
        SELECT gen_random_uuid(), url, content_hash, NULL, CAST('{}' AS json), body_text,
               published_time, NULL, fetched_at
          FROM editorial_documents
        """
    )
    op.execute(
        """
        UPDATE editorial_documents d
           SET current_version_id = v.id
          FROM editorial_document_versions v
         WHERE v.document_url = d.url
        """
    )


def downgrade() -> None:
    # Reverting drops the version table, and with it the raw pages: take a dump first.
    op.drop_constraint("fk_editorial_documents_current_version", "editorial_documents")
    op.drop_column("editorial_documents", "current_version_id")
    op.drop_table("editorial_document_versions")
