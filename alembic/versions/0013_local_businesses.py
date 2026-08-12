"""local_businesses — Cadastur (MTur) tourism-service providers, reference table.

Fourth member of the static carga-inicial reference set (municipios / distritos /
uf_geoids / local_businesses). Row data lives in the importer
(scripts/cadastur_import.py), NOT in this migration — same split as 0011.

Source: the 12 MTur "Prestadores de Serviços Turísticos" datasets on dados.gov.br
(slugs cadastur-01 … cadastur-12), one resource per quarter since ~2006. It is the
freshest dataset in the whole federal catalogue (2ºTri/2026 at the time of writing).

Column names mirror `local_businesses` in norteia-api so a future push maps 1:1.
`latitude`/`longitude`/`destination_id` are deliberately absent: Cadastur carries no
coordinates, only a textual address, so those need a geocoding pass that is out of
scope for the import.

Carries no pipeline state and nothing FK-references it → a reset-brave-db wipe
preserves it, like the other three (REFERENCE_TABLES in the reset skill).

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-12
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "local_businesses",
        # COMPOSITE primary key (cadastur_dataset, cadastur). Measured: 3 CNPJs appear
        # in BOTH cadastur-05 and cadastur-08, and the overlap is routine across the 12
        # (an agency that also operates transport). Keying on the certificate alone let
        # one category silently overwrite the other.
        #
        # "Número do Certificado" is MTur's own registration number — the CNPJ digits
        # for legal entities. Present in all 12 datasets including Guias de Turismo,
        # which is why the key is this and not cnpj.
        sa.Column("cadastur", sa.String(64), primary_key=True, nullable=False),
        sa.Column("cadastur_dataset", sa.String(16), primary_key=True, nullable=False),
        sa.Column("business_type", sa.String(32), nullable=False),
        sa.Column("trade_name", sa.String(300), nullable=False),
        sa.Column("company_name", sa.String(300), nullable=True),
        sa.Column("cnpj", sa.String(18), nullable=True),
        sa.Column("legal_type", sa.String(120), nullable=True),
        sa.Column("uf", sa.String(2), nullable=True),
        sa.Column("municipio", sa.String(128), nullable=True),
        # Resolved against `municipios` by (nome, uf); NULL when ambiguous or absent.
        sa.Column("municipio_ibge", sa.String(7), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("phone", sa.String(64), nullable=True),
        sa.Column("email", sa.String(256), nullable=True),
        sa.Column("website", sa.String(512), nullable=True),
        # JSONB, not JSON, unlike the pipeline tables: those payloads are read whole by
        # the app, while this is a reference table meant to be QUERIED
        # ("extra ? 'uhs_acessiveis'", "extra->>'tipo_hospedagem' = 'Hotel'"). The `?`
        # containment operator does not exist for plain json.
        sa.Column("languages", postgresql.JSONB(), nullable=True),
        # "Validade do Certificado" — ISO date string. The register's only per-row
        # freshness signal; stored, never used as a drop filter (MTur leaves stale
        # dates on otherwise Regular rows).
        sa.Column("certificate_valid_until", sa.String(10), nullable=True),
        # Type-specific sheet columns (UHs/Leitos Acessíveis, área do parque, …).
        sa.Column("extra", postgresql.JSONB(), nullable=True),
        # The resource TITLE, not a bare quarter: "Meios de Hospedagem - 2º
        # Trimestre/2026" (38 chars — a 32 here truncates on the real files).
        sa.Column("source_quarter", sa.String(128), nullable=True),
        sa.Column(
            "imported_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    # No index on cadastur_dataset alone — it is the leading column of the composite
    # primary key, so the PK index already serves those lookups.
    op.create_index("ix_local_businesses_cnpj", "local_businesses", ["cnpj"])
    op.create_index("ix_local_businesses_uf", "local_businesses", ["uf"])
    op.create_index(
        "ix_local_businesses_municipio_ibge", "local_businesses", ["municipio_ibge"]
    )


def downgrade() -> None:
    op.drop_index("ix_local_businesses_municipio_ibge", table_name="local_businesses")
    op.drop_index("ix_local_businesses_uf", table_name="local_businesses")
    op.drop_index("ix_local_businesses_cnpj", table_name="local_businesses")
    op.drop_table("local_businesses")
