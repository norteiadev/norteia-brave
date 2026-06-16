"""SQLAlchemy 2.0 declarative models for the three medallion layers and observability tables.

Tables (D-01):
  - nascente_records  — immutable raw payload store
  - rio_records       — mutable working area (dedup/normalize/score/route)
  - mar_records       — canonical published store
  - llm_generations   — LLM call observability (D-20)
  - audit_log         — steward + pipeline audit trail (D-21, OBS-04)
  - poison_quarantine — Celery poison messages (separate from §7.6 DLQ)
  - consent_log       — LGPD consent and opt-out log per contact (COMP-01, D-11)

Key design decisions implemented here:
  D-01: Table-per-layer (not a mega-table with a state column)
  D-02: DLQ and descarte are routing values (routing/sub_state) within rio_records
  D-03: Versioning by supersession (superseded_by FK); never mutate-in-place
  D-04: Nascente columns: source, source_ref, entity_type, uf, payload, content_hash, version
  D-13: score_version column on RioRecord and MarRecord
  D-19: psycopg 3 driver (connection string postgresql+psycopg://...)
"""

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Shared declarative base for all Brave models.

    Referenced by alembic/env.py as target_metadata = Base.metadata.
    """

    pass


# ---------------------------------------------------------------------------
# NascenteRecord — immutable raw payload store (D-01, D-04)
# ---------------------------------------------------------------------------


class NascenteRecord(Base):
    """Nascente layer: immutable raw source payload.

    Append-only. Once ingested, fields are NEVER updated in-place.
    Supersession by appending a new row + pointing superseded_by_id (D-03).
    """

    __tablename__ = "nascente_records"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_ref: Mapped[str] = mapped_column(
        String(256), nullable=False, index=True
    )
    entity_type: Mapped[str] = mapped_column(
        String(64), nullable=False
    )  # "destination" | "attraction"
    uf: Mapped[str] = mapped_column(String(2), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # Self-referential FK: points to the row that supersedes this one (D-03)
    superseded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("nascente_records.id"),
        nullable=True,
    )

    # Relationship helpers (lazy-loaded; do not use in hot paths)
    superseded_by: Mapped["NascenteRecord | None"] = relationship(
        "NascenteRecord", foreign_keys=[superseded_by_id], remote_side="NascenteRecord.id"
    )

    def __repr__(self) -> str:
        return (
            f"<NascenteRecord id={self.id} source={self.source!r} "
            f"source_ref={self.source_ref!r} uf={self.uf!r}>"
        )


# ---------------------------------------------------------------------------
# RioRecord — mutable working area (D-01, D-02)
# ---------------------------------------------------------------------------


class RioRecord(Base):
    """Rio layer: deduplication, normalization, scoring, and routing.

    routing values (D-02):
      "in_progress" — being processed
      "mar"         — promoted to MarRecord (≥85%)
      "dlq"         — §7.6 review DLQ (51–84.9%)
      "descarte"    — rejected (≤50%)

    sub_state is used by the Atrativos lane Phase 3 state machine; null for Destinos.
    """

    __tablename__ = "rio_records"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    nascente_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("nascente_records.id"), nullable=False
    )
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    uf: Mapped[str] = mapped_column(String(2), nullable=False)
    municipio_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    # Routing column (D-02): DLQ and descarte are values here, not separate tables
    routing: Mapped[str] = mapped_column(
        String(32), nullable=False, default="in_progress", index=True
    )
    dlq_reason: Mapped[str | None] = mapped_column(String(256), nullable=True)
    # Sub-state for Atrativos (Phase 3); null for Destinos
    sub_state: Mapped[str | None] = mapped_column(String(64), nullable=True)
    normalized: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # pgvector column for HNSW fuzzy dedup (D-07, D-08)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536), nullable=True)
    score: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    score_breakdown: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # Weight-set identity stamp (D-13)
    score_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    canonical_key: Mapped[str | None] = mapped_column(
        String(256), nullable=True, unique=True
    )

    # Relationship helpers
    nascente: Mapped["NascenteRecord"] = relationship("NascenteRecord", foreign_keys=[nascente_id])

    def __repr__(self) -> str:
        return (
            f"<RioRecord id={self.id} entity_type={self.entity_type!r} "
            f"uf={self.uf!r} routing={self.routing!r}>"
        )


# ---------------------------------------------------------------------------
# MarRecord — canonical published store (D-01, D-03, D-15)
# ---------------------------------------------------------------------------


class MarRecord(Base):
    """Mar layer: canonical records published to norteia-api.

    source_ref is unique among ACTIVE rows only — idempotent push keyed by
    source_ref (D-15). Supersession appends a new active row and points the old
    row's superseded_by_id at it (D-03), so uniqueness must exclude superseded
    rows; enforced by the partial unique index uq_mar_active_source_ref.
    """

    __tablename__ = "mar_records"
    __table_args__ = (
        Index(
            "uq_mar_active_source_ref",
            "source_ref",
            unique=True,
            postgresql_where=text("superseded_by_id IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    rio_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rio_records.id"), nullable=False
    )
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    # Idempotent push keyed by source_ref (D-15); uniqueness enforced only on
    # active rows via partial index uq_mar_active_source_ref (see __table_args__)
    source_ref: Mapped[str] = mapped_column(
        String(256), nullable=False, index=True
    )
    canonical: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    reliability_score: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
    # Weight-set identity stamp (D-13)
    score_version: Mapped[str] = mapped_column(String(64), nullable=False)
    # Self-referential FKs for supersession (D-03)
    parent_mar_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("mar_records.id"), nullable=True
    )
    # DEFERRABLE INITIALLY DEFERRED: supersession writes the new active row and
    # repoints the old row's superseded_by_id within one transaction; the self-FK
    # is validated at COMMIT so the in-flight circular reference is legal.
    superseded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mar_records.id", deferrable=True, initially="DEFERRED"),
        nullable=True,
    )
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationship helpers
    rio: Mapped["RioRecord"] = relationship("RioRecord", foreign_keys=[rio_id])

    def __repr__(self) -> str:
        return (
            f"<MarRecord id={self.id} entity_type={self.entity_type!r} "
            f"source_ref={self.source_ref!r} score={self.reliability_score}>"
        )


# ---------------------------------------------------------------------------
# LLMGeneration — observability: every LLM call logged (D-20, OBS-01)
# ---------------------------------------------------------------------------


class LLMGeneration(Base):
    """Records every LLM call for observability and cost tracking.

    The USD cost guard (OBS-02) uses Redis counters for the enforcing check;
    this table provides the historical audit trail.
    """

    __tablename__ = "llm_generations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    lane: Mapped[str] = mapped_column(String(64), nullable=False)
    model_slug: Mapped[str] = mapped_column(String(128), nullable=False)
    resolved_provider: Mapped[str | None] = mapped_column(String(128), nullable=True)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    usd_cost: Mapped[float] = mapped_column(Numeric(10, 6), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<LLMGeneration id={self.id} lane={self.lane!r} "
            f"model={self.model_slug!r} cost={self.usd_cost}>"
        )


# ---------------------------------------------------------------------------
# AuditLog — steward + pipeline action audit trail (D-21, OBS-04)
# ---------------------------------------------------------------------------


class AuditLog(Base):
    """Records steward decisions (approve/reject/reprocess) and pipeline actions.

    Written by the observability.audit module; surfaced via FastAPI /api/v1/audit.
    """

    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    record_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    before_state: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    after_state: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    actor: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<AuditLog id={self.id} action={self.action!r} "
            f"entity_type={self.entity_type!r}>"
        )


# ---------------------------------------------------------------------------
# PoisonQuarantine — Celery poison messages (distinct from §7.6 DLQ)
# ---------------------------------------------------------------------------


class PoisonQuarantine(Base):
    """Celery tasks that failed permanently after max_retries are quarantined here.

    This is DISTINCT from the §7.6 review DLQ (routing='dlq' on RioRecord):
      - §7.6 DLQ = score gate routing; human reviews the territorial record
      - PoisonQuarantine = Celery operational; engineering investigates the failure

    Both must be named and documented clearly to avoid confusion (see PITFALLS §6).
    """

    __tablename__ = "poison_quarantine"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    nascente_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    task_name: Mapped[str] = mapped_column(String(256), nullable=False)
    error_message: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    quarantined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<PoisonQuarantine id={self.id} task_name={self.task_name!r} "
            f"nascente_id={self.nascente_id}>"
        )


# ---------------------------------------------------------------------------
# Composite index for territorial-key dedup blocking (D-07)
# ---------------------------------------------------------------------------
Index(
    "ix_rio_territorial_key",
    RioRecord.uf,
    RioRecord.municipio_id,
    RioRecord.entity_type,
)


# ---------------------------------------------------------------------------
# ConsentLog — LGPD consent and opt-out log per contact (COMP-01, D-11)
# ---------------------------------------------------------------------------


class ConsentLog(Base):
    """LGPD consent and opt-out log per contact (COMP-01, D-11).

    Separate from audit_log because it serves a different query pattern:
      audit_log   = historical trail (append-only reads)
      consent_log = real-time suppression lookup (is_opted_out check before every send)

    Indexed on phone_e164 for fast suppression lookups.
    FK to rio_records.id enforces that every consent row belongs to a valid atrativo (T-03-01-01).
    """

    __tablename__ = "consent_log"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    phone_e164: Mapped[str] = mapped_column(
        String(32), nullable=False, index=True
    )
    rio_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rio_records.id"), nullable=False
    )
    legal_basis: Mapped[str] = mapped_column(String(128), nullable=False)
    norteia_identified: Mapped[bool] = mapped_column(Boolean, nullable=False)
    opted_out: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    opted_out_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    opted_out_keyword: Mapped[str | None] = mapped_column(String(32), nullable=True)
    first_contact_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_contact_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    purpose: Mapped[str] = mapped_column(
        String(128), nullable=False, default="business_validation"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationship to RioRecord (FK: rio_id)
    rio: Mapped["RioRecord"] = relationship("RioRecord", foreign_keys=[rio_id])

    def __repr__(self) -> str:
        return (
            f"<ConsentLog id={self.id} phone_prefix={self.phone_e164[:5]!r} "
            f"opted_out={self.opted_out}>"
        )


# ---------------------------------------------------------------------------
# ConversationMessage — append-only WhatsApp transcript log (DASH-05, R2 Option B)
# ---------------------------------------------------------------------------


def mask_phone(phone: str | None) -> str:
    """Return an LGPD-minimized form of a phone number (R3, T-04-24).

    The conversation transcript MUST NOT persist or surface the raw E.164 number.
    We keep only enough to disambiguate a conversation in the ops UI: the country/
    area prefix and the last two digits, with the middle masked. Empty/None →
    a stable "***" sentinel so the column is never null.

    Examples:
      "+5571999998888" -> "+5571*****88"
      ""               -> "***"
    """
    if not phone:
        return "***"
    digits = phone.strip()
    if len(digits) <= 6:
        # Too short to safely reveal a prefix + suffix — fully mask.
        return "***"
    prefix = digits[:5]
    suffix = digits[-2:]
    return f"{prefix}*****{suffix}"


class ConversationMessage(Base):
    """Append-only log of every WhatsApp conversation message boundary (R2 Option B).

    Written by the outreach/resume Celery tasks at every message boundary so the
    dashboard transcript read endpoint is a trivial `SELECT ... ORDER BY created_at`,
    decoupled from LangGraph's internal checkpoint serialization (RESEARCH §4).

    Posture mirrors ConsentLog: our-own-table, FK rio_id → rio_records, LGPD-minimized.
    UNLIKE ConsentLog (which indexes the raw phone_e164 for suppression lookups), this
    table stores ONLY a masked phone (R3) — the raw E.164 number is NEVER persisted here,
    so transcripts cannot leak PII (T-04-24).

    Append-only by contract (T-04-26): there is no update or delete path. Rows are
    written on the task's own committed session, never orphaned/uncommitted.
    """

    __tablename__ = "conversation_message"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    rio_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rio_records.id"), nullable=False, index=True
    )
    # LGPD-minimized phone only — NEVER the raw phone_e164 (R3, T-04-24).
    phone_masked: Mapped[str] = mapped_column(String(32), nullable=False)
    # "outbound" (Norteia → owner) | "inbound" (owner → Norteia)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    # LangGraph turn role: "assistant" | "user" (mirrors messages[].role)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # DeepSeek extraction snapshot at this boundary (nullable; resume follow-ups only)
    extracted: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationship to RioRecord (FK: rio_id)
    rio: Mapped["RioRecord"] = relationship("RioRecord", foreign_keys=[rio_id])

    def __repr__(self) -> str:
        return (
            f"<ConversationMessage id={self.id} rio_id={self.rio_id} "
            f"direction={self.direction!r} role={self.role!r}>"
        )
