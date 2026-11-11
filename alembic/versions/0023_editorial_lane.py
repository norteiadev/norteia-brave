"""Editorial lane: blog posts, extracted claims and the destination outbox.

editorial_documents    one row per blog post (body, KML points, batch state in columns)
editorial_claims       one row per (post, entity, column): the extracted fact + match result
editorial_destinations outbox of destination editorial columns (destinations are not in Mar)

Revision ID: 0023
Revises: 0022
Create Date: 2026-11-11
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "editorial_documents",
        sa.Column("url", sa.String(512), primary_key=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("uf", sa.String(2), nullable=False),
        sa.Column("published_time", sa.Date, nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("body_text", sa.Text, nullable=False),
        sa.Column("kml_points", sa.JSON, nullable=False),
        sa.Column(
            "fetched_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("batch_id", sa.String(64), nullable=True),
        sa.Column("batch_submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("extracted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text, nullable=True),
    )
    op.create_index("ix_editorial_documents_uf", "editorial_documents", ["uf"])
    op.create_index(
        "ix_editorial_documents_batch_id",
        "editorial_documents",
        ["batch_id"],
        postgresql_where=sa.text("batch_id IS NOT NULL"),
    )

    op.create_table(
        "editorial_claims",
        sa.Column("id", sa.UUID, primary_key=True),
        sa.Column(
            "document_url",
            sa.String(512),
            sa.ForeignKey("editorial_documents.url", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("entity_kind", sa.String(16), nullable=False),
        sa.Column("entity_name", sa.String(256), nullable=False),
        sa.Column("municipio_text", sa.String(128), nullable=True),
        sa.Column("lat", sa.Float, nullable=True),
        sa.Column("lon", sa.Float, nullable=True),
        sa.Column("canonical_key", sa.String(256), nullable=True),
        sa.Column("match_method", sa.String(24), nullable=True),
        sa.Column("match_score", sa.Float, nullable=True),
        sa.Column("field", sa.String(48), nullable=False),
        sa.Column("value", sa.JSON, nullable=False),
        sa.Column("stability", sa.String(8), nullable=False),
        sa.Column("evidence", sa.Text, nullable=True),
        sa.Column("published_time", sa.Date, nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_editorial_claims_document_url", "editorial_claims", ["document_url"])
    op.create_index("ix_editorial_claims_canonical_key", "editorial_claims", ["canonical_key"])

    op.create_table(
        "editorial_destinations",
        sa.Column("source_ref", sa.String(256), primary_key=True),
        sa.Column("uf", sa.String(2), nullable=False),
        sa.Column("municipio_ibge", sa.String(7), nullable=False),
        sa.Column("tourist_name", sa.String(256), nullable=False),
        sa.Column("fields", sa.JSON, nullable=False),
        sa.Column("push_hash", sa.String(64), nullable=True),
        sa.Column("pushed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("push_error", sa.Text, nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_editorial_destinations_uf", "editorial_destinations", ["uf"])


def downgrade() -> None:
    op.drop_table("editorial_destinations")
    op.drop_table("editorial_claims")
    op.drop_table("editorial_documents")
