"""atrativo_buscas — every paid cascade search, stored whole.

The description cascade searches Parallel (docs/poc/gemini-viability.md §29) and writes with
Gemini 2.5 Flash. The raw results are kept so descriptions can be regenerated later with
another model without paying the search again.

Keyed by canonical_key, NO foreign key to rio_records: a reset-brave-db rebuilds the Rio rows
with the same canonical_key, and the paid searches must survive it — the table joins the
reset skill's REFERENCE_TABLES.

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-15
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "atrativo_buscas",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        # One row per attempt — not unique; readers take the latest created_at.
        sa.Column("canonical_key", sa.String(256), nullable=False),
        sa.Column("nome", sa.String(512), nullable=False),
        sa.Column("municipio", sa.String(128), nullable=True),
        sa.Column("uf", sa.String(2), nullable=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("queries", sa.JSON(), nullable=False),
        sa.Column("search_id", sa.String(128), nullable=True),
        # JSONB: the raw results are meant to be queried (by url, publish_date), not only read.
        sa.Column("results", postgresql.JSONB(), nullable=False),
        sa.Column("usage", sa.JSON(), nullable=True),
        sa.Column("warnings", sa.JSON(), nullable=True),
        sa.Column("usd_cost", sa.Numeric(10, 6), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_atrativo_buscas_canonical_key", "atrativo_buscas", ["canonical_key"])


def downgrade() -> None:
    op.drop_index("ix_atrativo_buscas_canonical_key", table_name="atrativo_buscas")
    op.drop_table("atrativo_buscas")
