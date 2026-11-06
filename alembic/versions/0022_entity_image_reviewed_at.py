"""entity_images.reviewed_at: the photo outbox sends only reviewed photos.

The image lane archives whatever Commons offers; the photo review (Gemini + Claude, or a
person) is what decides a photo reaches norteia-api. Until now the global
images_push_enabled switch had to be off while a new UF was searched. With reviewed_at the
switch can stay on: a fresh photo waits until its review stamps it.

Backfill: a row already pushed or removed was reviewed. Rows reviewed and kept but still
pending (entity not on the API yet) are stamped from the review's decision files, outside
the migration.

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-06
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("entity_images", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(
        "UPDATE entity_images SET reviewed_at = coalesce(pushed_at, removed_at) "
        "WHERE pushed_at IS NOT NULL OR removed_at IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_column("entity_images", "reviewed_at")
