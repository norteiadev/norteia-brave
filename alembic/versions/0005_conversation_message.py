"""Add conversation_message table — append-only WhatsApp transcript log (DASH-05, R2 Option B).

Resolves RESEARCH §4 R2 as Option B: a thin append-only log written by the existing
outreach/resume tasks at every message boundary, so the dashboard transcript read
endpoint is a trivial SELECT (offline-testable, LGPD-minimizable, decoupled from
LangGraph's internal checkpoint serialization).

LGPD (R3, T-04-24): stores phone_masked ONLY — the raw phone_e164 is NEVER persisted
in this table, so transcripts cannot leak PII.

DO NOT use CREATE INDEX CONCURRENTLY here — this is a standard B-tree index inside
Alembic's transaction block, which is correct for a new table at migration time.
(CONCURRENTLY cannot run inside a transaction; Phase 2 lesson — D-08.)

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-16
"""


import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "conversation_message",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("rio_id", UUID(as_uuid=True), nullable=False),
        # CR-03: 0-based chronological turn position; idempotency key per rio.
        sa.Column("turn_seq", sa.Integer, nullable=False),
        # LGPD-minimized phone only — never the raw phone_e164 (R3, T-04-24).
        sa.Column("phone_masked", sa.String(32), nullable=False),
        sa.Column("direction", sa.String(16), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("extracted", sa.JSON, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["rio_id"],
            ["rio_records.id"],
            name="fk_conversation_message_rio_id",
        ),
        # CR-03: append-only idempotency — a replayed turn cannot duplicate.
        sa.UniqueConstraint(
            "rio_id", "turn_seq", name="uq_conversation_message_rio_turn"
        ),
    )
    # Standard B-tree index — not CONCURRENTLY (inside Alembic transaction, new table).
    op.create_index(
        "ix_conversation_message_rio_id", "conversation_message", ["rio_id"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_conversation_message_rio_id", table_name="conversation_message"
    )
    op.drop_table("conversation_message")
