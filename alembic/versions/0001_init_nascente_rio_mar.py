"""init_nascente_rio_mar

Creates all Phase 1 tables in one migration:
  - nascente_records
  - rio_records
  - mar_records
  - llm_generations
  - audit_log
  - poison_quarantine

Revision ID: 0001
Revises:
Create Date: 2026-06-11
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enable pgvector extension (idempotent — required before creating Vector columns)
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")

    # ------------------------------------------------------------------ #
    # nascente_records — immutable raw payload store (D-01, D-04)        #
    # ------------------------------------------------------------------ #
    op.create_table(
        "nascente_records",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("source_ref", sa.String(256), nullable=False),
        sa.Column("entity_type", sa.String(64), nullable=False),
        sa.Column("uf", sa.String(2), nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("superseded_by_id", UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["superseded_by_id"],
            ["nascente_records.id"],
            name="fk_nascente_superseded_by",
        ),
    )
    op.create_index("ix_nascente_source_ref", "nascente_records", ["source_ref"])
    op.create_index("ix_nascente_content_hash", "nascente_records", ["content_hash"])

    # ------------------------------------------------------------------ #
    # rio_records — mutable working area with routing/sub_state (D-02)   #
    # ------------------------------------------------------------------ #
    op.create_table(
        "rio_records",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("nascente_id", UUID(as_uuid=True), nullable=False),
        sa.Column("entity_type", sa.String(64), nullable=False),
        sa.Column("uf", sa.String(2), nullable=False),
        sa.Column("municipio_id", sa.String(64), nullable=True),
        # Routing: "in_progress" | "mar" | "dlq" | "descarte"
        sa.Column(
            "routing",
            sa.String(32),
            nullable=False,
            server_default="in_progress",
        ),
        sa.Column("dlq_reason", sa.String(256), nullable=True),
        # Sub-state for Atrativos lane (Phase 3)
        sa.Column("sub_state", sa.String(64), nullable=True),
        sa.Column("normalized", sa.JSON, nullable=True),
        # Vector column added after pgvector extension is enabled
        sa.Column(
            "embedding",
            sa.Text,  # placeholder; real type set via raw SQL below
            nullable=True,
        ),
        sa.Column("score", sa.Numeric(5, 2), nullable=True),
        sa.Column("score_breakdown", sa.JSON, nullable=True),
        sa.Column("score_version", sa.String(64), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("canonical_key", sa.String(256), nullable=True),
        sa.ForeignKeyConstraint(
            ["nascente_id"],
            ["nascente_records.id"],
            name="fk_rio_nascente_id",
        ),
    )
    # Add the actual vector column type now that extension is enabled
    op.execute("ALTER TABLE rio_records DROP COLUMN embedding;")
    op.execute("ALTER TABLE rio_records ADD COLUMN embedding vector(1536);")

    op.create_index("ix_rio_municipio_id", "rio_records", ["municipio_id"])
    op.create_index("ix_rio_routing", "rio_records", ["routing"])
    op.create_unique_constraint(
        "uq_rio_canonical_key", "rio_records", ["canonical_key"]
    )

    # ------------------------------------------------------------------ #
    # mar_records — canonical store; versioned by supersession (D-03)    #
    # ------------------------------------------------------------------ #
    op.create_table(
        "mar_records",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("rio_id", UUID(as_uuid=True), nullable=False),
        sa.Column("entity_type", sa.String(64), nullable=False),
        sa.Column("source_ref", sa.String(256), nullable=False),
        sa.Column("canonical", sa.JSON, nullable=False),
        sa.Column("provenance", sa.JSON, nullable=False),
        sa.Column("reliability_score", sa.Numeric(5, 2), nullable=False),
        sa.Column("score_version", sa.String(64), nullable=False),
        sa.Column("parent_mar_id", UUID(as_uuid=True), nullable=True),
        sa.Column("superseded_by_id", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "published_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["rio_id"],
            ["rio_records.id"],
            name="fk_mar_rio_id",
        ),
        sa.ForeignKeyConstraint(
            ["parent_mar_id"],
            ["mar_records.id"],
            name="fk_mar_parent_mar_id",
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by_id"],
            ["mar_records.id"],
            name="fk_mar_superseded_by",
        ),
        sa.UniqueConstraint("source_ref", name="uq_mar_source_ref"),
    )
    op.create_index("ix_mar_source_ref", "mar_records", ["source_ref"])

    # ------------------------------------------------------------------ #
    # llm_generations — observability: every LLM call logged (D-20)      #
    # ------------------------------------------------------------------ #
    op.create_table(
        "llm_generations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("lane", sa.String(64), nullable=False),
        sa.Column("model_slug", sa.String(128), nullable=False),
        sa.Column("resolved_provider", sa.String(128), nullable=True),
        sa.Column("prompt_tokens", sa.Integer, nullable=False),
        sa.Column("completion_tokens", sa.Integer, nullable=False),
        sa.Column("usd_cost", sa.Numeric(10, 6), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # ------------------------------------------------------------------ #
    # audit_log — steward + pipeline action audit trail (D-21, OBS-04)   #
    # ------------------------------------------------------------------ #
    op.create_table(
        "audit_log",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("action", sa.String(128), nullable=False),
        sa.Column("entity_type", sa.String(64), nullable=True),
        sa.Column("record_id", UUID(as_uuid=True), nullable=True),
        sa.Column("before_state", sa.JSON, nullable=True),
        sa.Column("after_state", sa.JSON, nullable=True),
        sa.Column("actor", sa.String(128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # ------------------------------------------------------------------ #
    # poison_quarantine — Celery poison messages (separate from DLQ)     #
    # ------------------------------------------------------------------ #
    op.create_table(
        "poison_quarantine",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("nascente_id", UUID(as_uuid=True), nullable=True),
        sa.Column("task_name", sa.String(256), nullable=False),
        sa.Column("error_message", sa.Text, nullable=False),
        sa.Column("payload", sa.JSON, nullable=True),
        sa.Column(
            "quarantined_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("poison_quarantine")
    op.drop_table("audit_log")
    op.drop_table("llm_generations")
    op.drop_index("ix_mar_source_ref", table_name="mar_records")
    op.drop_table("mar_records")
    op.drop_index("ix_rio_routing", table_name="rio_records")
    op.drop_index("ix_rio_municipio_id", table_name="rio_records")
    op.drop_table("rio_records")
    op.drop_index("ix_nascente_content_hash", table_name="nascente_records")
    op.drop_index("ix_nascente_source_ref", table_name="nascente_records")
    op.drop_table("nascente_records")
    op.execute("DROP EXTENSION IF EXISTS vector;")
