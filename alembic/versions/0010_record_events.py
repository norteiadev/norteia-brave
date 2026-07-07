"""record_events — append-only per-record Brave timeline (Log tab).

Adds the ``record_events`` table that persists one row per pipeline stage a
record passes through (TripAdvisor synced → município resolved → validated →
ingested → deduped → scored → routed, or a terminal ``quarantined`` on failure).
Powers the drawer "Log" tab timeline. Written by
``brave.observability.record_events.record_event`` alongside the existing
emission points, always behind the idempotency early-returns so a re-sweep does
not re-emit DB-stage events.

Two indexes:
  - ``ix_record_events_source_ref`` (source, source_ref) — the universal drawer
    lookup key (a TA attraction's source_ref == RioRecord.canonical_key).
  - ``ix_record_events_rio_id`` (rio_id) — Rio-card lookup.

LGPD: the ``data`` JSON column carries ONLY public-geo + engineering fields
(score, routing, dlq_reason, IBGE reason, name/uf, locationId) — never a phone,
PII, review text, or a username.

Mirrors 0009_config_settings.py (plain create_table inside the Alembic txn).

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-06
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "record_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("source_ref", sa.String(256), nullable=False),
        sa.Column("entity_type", sa.String(64), nullable=True),
        sa.Column("uf", sa.String(2), nullable=True),
        sa.Column("nascente_id", UUID(as_uuid=True), nullable=True),
        sa.Column("rio_id", UUID(as_uuid=True), nullable=True),
        sa.Column("stage", sa.String(48), nullable=False),
        # 'ok' | 'fail' | 'skip'
        sa.Column("status", sa.String(8), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("data", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            # clock_timestamp() (not now()): now() is constant within a txn, so the
            # ~7 events of one atrativo (one per-atrativo commit) would share created_at
            # and the ASC Log timeline would be ambiguous. clock_timestamp() advances
            # within a txn so intra-transaction order is preserved.
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_record_events_source_ref",
        "record_events",
        ["source", "source_ref"],
    )
    op.create_index(
        "ix_record_events_rio_id",
        "record_events",
        ["rio_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_record_events_rio_id", table_name="record_events")
    op.drop_index("ix_record_events_source_ref", table_name="record_events")
    op.drop_table("record_events")
