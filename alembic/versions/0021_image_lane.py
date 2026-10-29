"""Image lane: image_lookups, images, entity_images.

The image lane (P1) finds Wikidata/Commons photos for Rio entities, keeps the files in the
Brave archive (local dir or R2) and pushes them to norteia-api through its own outbox.

Keyed by canonical_key with NO foreign key to rio_records (the atrativo_buscas pattern): a
reset-brave-db rebuilds the Rio rows with the same canonical_key, and the archive and its
lookups must survive it — the three tables join the reset skill's REFERENCE_TABLES.

* ``image_lookups`` — one row per entity: idempotency marker + QID cache. ``outcome=error``
  is not final: the next run retries it.
* ``images`` — one row per archived file (sha1 of the downloaded bytes).
* ``entity_images`` — entity ↔ image, with the push outbox columns.

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-29
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "image_lookups",
        sa.Column("canonical_key", sa.String(256), primary_key=True, nullable=False),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("uf", sa.String(2), nullable=True),
        sa.Column("qid", sa.String(32), nullable=True),
        sa.Column("via", sa.String(16), nullable=True),
        sa.Column("dist_km", sa.Float(), nullable=True),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column(
            "checked_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
    )
    op.create_table(
        "images",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("sha1", sa.String(40), nullable=False, unique=True),
        sa.Column("storage_key", sa.String(128), nullable=False),
        sa.Column("commons_title", sa.String(512), nullable=False, unique=True),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("file_url", sa.Text(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("mime", sa.String(64), nullable=False),
        sa.Column("bytes", sa.Integer(), nullable=False),
        sa.Column("author", sa.String(255), nullable=True),
        sa.Column("license", sa.String(64), nullable=False),
        sa.Column("license_url", sa.Text(), nullable=True),
        sa.Column("credit_line", sa.Text(), nullable=False),
        sa.Column("assessment", sa.String(64), nullable=True),
        sa.Column("commons_status", sa.String(16), nullable=False),
        sa.Column(
            "checked_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
    )
    op.create_table(
        "entity_images",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("canonical_key", sa.String(256), nullable=False),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column(
            "image_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("images.id"), nullable=False
        ),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("lane", sa.String(16), nullable=False),
        sa.Column("wikidata_qid", sa.String(32), nullable=True),
        sa.Column("pushed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("push_error", sa.Text(), nullable=True),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.UniqueConstraint("canonical_key", "image_id", name="uq_entity_images_key_image"),
    )


def downgrade() -> None:
    op.drop_table("entity_images")
    op.drop_table("images")
    op.drop_table("image_lookups")
