"""Add consent_log table for LGPD compliance (COMP-01, D-11).

Separate from audit_log: consent_log serves real-time suppression lookups
(is_opted_out check before every WhatsApp send); audit_log is the historical trail.

Indexed on phone_e164 (B-tree) for fast suppression lookups in the compliance gate.

DO NOT use CREATE INDEX CONCURRENTLY here — this is a standard B-tree index inside
Alembic's transaction block, which is correct for a new table at migration time.
(CONCURRENTLY cannot run inside a transaction; Phase 2 lesson — D-08.)

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-15
"""

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.create_table(
        "consent_log",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("phone_e164", sa.String(32), nullable=False),
        sa.Column("rio_id", UUID(as_uuid=True), nullable=False),
        sa.Column("legal_basis", sa.String(128), nullable=False),
        sa.Column("norteia_identified", sa.Boolean, nullable=False),
        sa.Column("opted_out", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("opted_out_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("opted_out_keyword", sa.String(32), nullable=True),
        sa.Column(
            "first_contact_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "last_contact_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "purpose",
            sa.String(128),
            nullable=False,
            server_default="business_validation",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["rio_id"],
            ["rio_records.id"],
            name="fk_consent_log_rio_id",
        ),
    )
    # Standard B-tree index — not CONCURRENTLY (inside Alembic transaction, new table).
    op.create_index("ix_consent_log_phone_e164", "consent_log", ["phone_e164"])


def downgrade() -> None:
    op.drop_index("ix_consent_log_phone_e164", table_name="consent_log")
    op.drop_table("consent_log")
