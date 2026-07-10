"""reference_tables — static carga-inicial territorial base (municipios/distritos/uf_geoids).

Adds the three DB-backed reference tables that replace the mtur destino-seed lane.
Instead of running static CSV data through the whole Brave pipeline (Nascente → Rio
→ Mar) only to materialize parent "destino" records, the collection lanes read their
parent destinos / geo resolution directly from these tables.

Tables (all seeded by scripts/seed_reference_data.py — row data lives in the seed,
NOT in this migration):
  - municipios  — IBGE municipality + folded-in mtur turistic categoria/regiao_turistica
  - distritos   — IBGE distrito (DTB), name-only resolution, scoped by parent município
  - uf_geoids   — TripAdvisor UF → geoId

These carry no pipeline state and nothing FK-references them; a reset-brave-db wipe
preserves them (they are static carga-inicial, re-seeded only at migrate time).

Mirrors 0009_config_settings.py (plain create_table inside the Alembic txn).

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-10
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "municipios",
        sa.Column("ibge_code", sa.String(7), primary_key=True, nullable=False),
        sa.Column("nome", sa.String(128), nullable=False),
        sa.Column("uf", sa.String(2), nullable=False),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lng", sa.Float(), nullable=False),
        # mtur fold-in — nullable (only ~2922/5571 rows carry a turistic categorization).
        sa.Column("categoria", sa.String(32), nullable=True),
        sa.Column("regiao_turistica", sa.String(128), nullable=True),
    )
    op.create_index("ix_municipios_uf", "municipios", ["uf"])

    op.create_table(
        "distritos",
        sa.Column("distrito_code", sa.String(9), primary_key=True, nullable=False),
        sa.Column("nome", sa.String(128), nullable=False),
        sa.Column("ibge_code", sa.String(7), nullable=False),
        sa.Column("municipio_nome", sa.String(128), nullable=False),
        sa.Column("uf", sa.String(2), nullable=False),
    )
    op.create_index("ix_distritos_ibge_code", "distritos", ["ibge_code"])

    op.create_table(
        "uf_geoids",
        sa.Column("uf", sa.String(2), primary_key=True, nullable=False),
        sa.Column("geo_id", sa.Integer(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("uf_geoids")
    op.drop_index("ix_distritos_ibge_code", table_name="distritos")
    op.drop_table("distritos")
    op.drop_index("ix_municipios_uf", table_name="municipios")
    op.drop_table("municipios")
