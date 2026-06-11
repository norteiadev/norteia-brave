"""Make mar_records.source_ref unique only among active (non-superseded) rows.

The original uq_mar_source_ref UNIQUE constraint forbade supersession: a second
promote_to_mar of the same source_ref must append a new active row while the old
row is retained (D-03) — that requires two rows sharing source_ref, which a plain
UNIQUE constraint rejects. Replace it with a partial unique index that only
constrains active rows (superseded_by_id IS NULL), preserving idempotent-by-
source_ref semantics (D-15) without blocking supersession.

Revision ID: 0003
Revises: 0002
"""

from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    # Drop the full UNIQUE constraint that forbids supersession.
    op.drop_constraint("uq_mar_source_ref", "mar_records", type_="unique")
    # Partial unique index: only one ACTIVE row per source_ref.
    op.create_index(
        "uq_mar_active_source_ref",
        "mar_records",
        ["source_ref"],
        unique=True,
        postgresql_where=sa.text("superseded_by_id IS NULL"),
    )
    # Supersession needs a circular write within one transaction: the new active
    # row is INSERTed while the old row is UPDATEd to point its superseded_by_id
    # at the new row. Make the self-FK DEFERRABLE INITIALLY DEFERRED so the FK is
    # validated at COMMIT (by which point both rows exist) rather than per-statement.
    op.drop_constraint("fk_mar_superseded_by", "mar_records", type_="foreignkey")
    op.create_foreign_key(
        "fk_mar_superseded_by",
        "mar_records",
        "mar_records",
        ["superseded_by_id"],
        ["id"],
        deferrable=True,
        initially="DEFERRED",
    )


def downgrade() -> None:
    op.drop_constraint("fk_mar_superseded_by", "mar_records", type_="foreignkey")
    op.create_foreign_key(
        "fk_mar_superseded_by",
        "mar_records",
        "mar_records",
        ["superseded_by_id"],
        ["id"],
    )
    op.drop_index("uq_mar_active_source_ref", table_name="mar_records")
    op.create_unique_constraint("uq_mar_source_ref", "mar_records", ["source_ref"])
