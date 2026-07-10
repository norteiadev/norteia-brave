"""SQLAlchemy 2.0 declarative models for the three medallion layers and observability tables.

Tables (D-01):
  - nascente_records  — immutable raw payload store
  - rio_records       — mutable working area (dedup/normalize/score/route)
  - mar_records       — canonical published store
  - llm_generations   — LLM call observability (D-20)
  - audit_log         — steward + pipeline audit trail (D-21, OBS-04)
  - poison_quarantine — Celery poison messages (separate from reliability DLQ)
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
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
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
      "mar"         — promoted to MarRecord (score ≥ threshold_mar)
      "dlq"         — reliability review DLQ (score < threshold_mar; binary score gate)
      "descarte"    — rejected by a non-score path (hard-descarte for CLOSED places,
                      steward reject, dedup discard). The score engine never emits it.

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
# RunHistory — durable engine-sweep run trail (UI-PAINEL-2, Varreduras view)
# ---------------------------------------------------------------------------


class RunHistory(Base):
    """A persisted engine-sweep "run" envelope (the Varreduras DB trail).

    Engine runs lived only in Redis until now (no DB audit trail). This table is
    written at engine-start (status="running") and finalized when the orchestrator
    returns to idle (ended_at, ufs_dispatched, status). synced/failed are NOT
    threaded through the async producer tasks; they are computed ON-READ in the
    list endpoint over the run's [started_at, ended_at] window (RESEARCH #2, A4).

    Mirrors the LLMGeneration table style (mapped_column + server_default).
    """

    __tablename__ = "runs_history"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ufs: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    depth: Mapped[str] = mapped_column(String(32), nullable=False)
    lane: Mapped[str] = mapped_column(String(32), nullable=False, server_default="both")
    ufs_total: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    ufs_dispatched: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    # Nullable aggregate snapshot — the list endpoint recomputes synced/failed on-read.
    total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    synced: Mapped[int | None] = mapped_column(Integer, nullable=True)
    failed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="running"
    )

    def __repr__(self) -> str:
        return (
            f"<RunHistory id={self.id} source={self.source!r} "
            f"depth={self.depth!r} status={self.status!r}>"
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
# PoisonQuarantine — Celery poison messages (distinct from reliability DLQ)
# ---------------------------------------------------------------------------


class PoisonQuarantine(Base):
    """Celery tasks that failed permanently after max_retries are quarantined here.

    This is DISTINCT from the reliability review DLQ (routing='dlq' on RioRecord):
      - reliability DLQ = score gate routing; human reviews the territorial record
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
# RecordEvent — append-only per-record Brave timeline (Log tab)
# ---------------------------------------------------------------------------


class RecordEvent(Base):
    """Append-only per-record event log powering the drawer "Log" tab timeline.

    One row per pipeline stage a record passes through (TripAdvisor synced →
    município resolved → validated → ingested → deduped → scored → routed, or a
    terminal ``quarantined`` on failure). Written by
    ``brave.observability.record_events.record_event`` alongside the existing
    emission points, ALWAYS behind the idempotency early-returns (``store_raw``
    content_hash / ``process_nascente_record`` canonical_key) so a re-sweep of an
    already-ingested record does not re-emit DB-stage events.

    ``source_ref`` is the universal drawer key and exists from the first stage,
    before any Nascente/Rio row (for a TA attraction:
    ``tripadvisor:attraction:{locationId}`` == ``RioRecord.canonical_key``), so a
    ``ibge_unmatched`` failure that returns before ``store_raw`` still has a stable
    identity to group its terminal event under.

    LGPD (T): stores ONLY public-geo + engineering fields — score, routing,
    dlq_reason, IBGE reason, name/uf (public-geo), locationId. NEVER a phone, PII,
    review text, or a username. The ``data`` JSON column carries only those fields.
    """

    __tablename__ = "record_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(256), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    uf: Mapped[str | None] = mapped_column(String(2), nullable=True)
    nascente_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    rio_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    stage: Mapped[str] = mapped_column(String(48), nullable=False)
    # 'ok' | 'fail' | 'skip'
    status: Mapped[str] = mapped_column(String(8), nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    data: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # clock_timestamp() (not now()): now() returns the TRANSACTION start time, so the
    # ~7 events of one atrativo (all in one per-atrativo commit) would share an identical
    # created_at and the ASC-ordered Log timeline would be ambiguous. clock_timestamp()
    # advances within a txn (real wall-clock) so intra-transaction order is preserved.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )

    def __repr__(self) -> str:
        return (
            f"<RecordEvent id={self.id} stage={self.stage!r} "
            f"status={self.status!r} source_ref={self.source_ref!r}>"
        )


# ---------------------------------------------------------------------------
# RecordEvent indexes — drawer lookup by source_ref + Rio-card lookup by rio_id
# ---------------------------------------------------------------------------
Index(
    "ix_record_events_source_ref",
    RecordEvent.source,
    RecordEvent.source_ref,
)
Index(
    "ix_record_events_rio_id",
    RecordEvent.rio_id,
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


def whatsapp_candidate_from_phone(phone_raw: str | None) -> str | None:
    """Return a MASKED Brazilian celular (WhatsApp candidate) or None (LGPD, Phase F).

    Phase F enrichment captures a `whatsapp_candidate` (celular + DDD) from the
    phone surfaced by an enrichment source (Google Places `internationalPhoneNumber`).
    Only a MOBILE line (celular) is a plausible WhatsApp number, so landlines are
    rejected. The returned value is ALWAYS the ``mask_phone()`` form — the raw celular
    is NEVER returned, so callers can store it in ``normalized["contact"]
    ["whatsapp_candidate"]`` and surface it on the board without leaking PII (R3).

    Celular shape (Brazil): DDD(2) + leading 9 + 8 subscriber digits = 11 national
    digits with the subscriber part starting in ``9`` (Google exposes no mobile/landline
    flag nor a DDD field — the DDD is embedded and parsed from the number).

    Args:
        phone_raw: Raw phone string from an enrichment source (E.164, ``55``-prefixed,
            or bare national). None/empty/landline/non-celular → None.

    Returns:
        Masked celular (e.g. ``"+5573*****01"``) or None when not a BR celular.
    """
    if not phone_raw:
        return None

    digits = "".join(c for c in phone_raw if c.isdigit())
    if not digits:
        return None

    # Derive the national number (DDD + subscriber), stripping a +55 country code.
    if len(digits) in (12, 13) and digits.startswith("55"):
        national = digits[2:]
    elif len(digits) in (10, 11):
        national = digits
    else:
        return None

    # Celular = 11 national digits with the subscriber part starting in 9
    # (DDD(2) + 9 + 8). A 10-digit national number is a landline → rejected.
    if len(national) != 11 or national[2] != "9":
        return None

    return mask_phone(f"+55{national}")


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

    Idempotency by identity (CR-03): each row carries a deterministic `turn_seq`
    (0-based chronological position in the thread). A UNIQUE (rio_id, turn_seq)
    constraint backstops the append-only "no duplicate" contract — a retried/
    replayed task inserts the same (rio_id, turn_seq) and the writer's existence
    check (or ON CONFLICT DO NOTHING) makes the re-run a true no-op, regardless of
    any drift between the persisted-row count and the graph's `messages` length.
    """

    __tablename__ = "conversation_message"
    __table_args__ = (
        # CR-03: idempotency by identity — a replayed turn cannot duplicate.
        UniqueConstraint("rio_id", "turn_seq", name="uq_conversation_message_rio_turn"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    rio_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rio_records.id"), nullable=False, index=True
    )
    # CR-03: 0-based chronological position of this message in the rio's thread.
    # Unique per (rio_id, turn_seq) — the append-only idempotency key.
    turn_seq: Mapped[int] = mapped_column(Integer, nullable=False)
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


# ---------------------------------------------------------------------------
# ConfigSetting — operator-tunable runtime config overlay (Phase D)
# ---------------------------------------------------------------------------


class ConfigSetting(Base):
    """Sparse key→value overlay for operator-tunable runtime config (Phase D).

    Each row is one dotted config key (e.g. "score.threshold_mar",
    "source.tripadvisor.enabled", "engine.mode") whose value is wrapped as
    ``{"v": <any>}`` so any JSON scalar/list/dict — including ``None``, ``False``,
    or ``0`` — round-trips through the JSON column unambiguously (row presence,
    not a NULL, marks "set"). brave.config.runtime.load_effective_config reads
    every row and overlays it onto the env-bootstrapped AppConfig.

    ABSENT rows → the effective config equals the env/AppConfig defaults
    (behavior-neutral). The idempotent seed (runtime.seed_default_config) inserts
    default rows equal to the current env-effective values, so a freshly seeded
    base behaves identically to one with no rows at all.

    This is the FIRST table in the repo to carry an ``updated_at`` column with an
    ``onupdate`` bump. NB: ``onupdate=func.now()`` is an ORM-flush-level hook
    (fires on SQLAlchemy ORM UPDATE), NOT a DB trigger — a raw SQL UPDATE will not
    bump it. The Alembic DDL therefore only emits the INSERT-time ``server_default``.
    """

    __tablename__ = "config_settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    # Always the {"v": <any>} wrapper — never NULL (see class docstring).
    value: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    updated_by: Mapped[str | None] = mapped_column(String, nullable=True)

    def __repr__(self) -> str:
        return f"<ConfigSetting key={self.key!r}>"


# ---------------------------------------------------------------------------
# Reference tables — static carga-inicial territorial base (seeded, not pipeline)
# ---------------------------------------------------------------------------
#
# These three tables replace the mtur destino-seed lane: the collection lanes read
# their parent destinos / geo resolution from here instead of running static CSV
# data through the whole Brave pipeline. They are seeded at migrate time by
# scripts/seed_reference_data.py and are PRESERVED across a reset-brave-db wipe
# (they carry no pipeline state and nothing FK-references them).
#
# Column types are chosen so the resolver dataclasses IbgeMunicipio
# (brave/domains/tripadvisor/ibge.py) and IbgeDistrito (brave/shared/ibge_distritos.py)
# round-trip unchanged.


class Municipio(Base):
    """IBGE municipality reference row + folded-in mtur turistic categorization.

    Loaded from data/ibge/ibge_municipios.csv (5571 rows). The nullable
    ``categoria`` / ``regiao_turistica`` columns carry the mtur turistic signal
    folded in from data/mtur/municipios_mtur_2025.csv — only ~2922/5571 rows have
    them, so both are nullable.
    """

    __tablename__ = "municipios"

    ibge_code: Mapped[str] = mapped_column(String(7), primary_key=True)
    nome: Mapped[str] = mapped_column(String(128), nullable=False)
    uf: Mapped[str] = mapped_column(String(2), nullable=False, index=True)
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lng: Mapped[float] = mapped_column(Float, nullable=False)
    # mtur fold-in — nullable (only ~2922/5571 rows carry a turistic categorization).
    categoria: Mapped[str | None] = mapped_column(String(32), nullable=True)
    regiao_turistica: Mapped[str | None] = mapped_column(String(128), nullable=True)

    def __repr__(self) -> str:
        return (
            f"<Municipio ibge_code={self.ibge_code!r} nome={self.nome!r} "
            f"uf={self.uf!r}>"
        )


class Distrito(Base):
    """IBGE distrito reference row (DTB — Divisão Territorial Brasileira).

    Loaded from data/ibge/ibge_distritos.csv (10751 rows). No GPS — distritos are
    resolved by name only, scoped to the parent município via ``ibge_code``.
    """

    __tablename__ = "distritos"

    distrito_code: Mapped[str] = mapped_column(String(9), primary_key=True)
    nome: Mapped[str] = mapped_column(String(128), nullable=False)
    ibge_code: Mapped[str] = mapped_column(String(7), nullable=False, index=True)
    municipio_nome: Mapped[str] = mapped_column(String(128), nullable=False)
    uf: Mapped[str] = mapped_column(String(2), nullable=False)

    def __repr__(self) -> str:
        return (
            f"<Distrito distrito_code={self.distrito_code!r} nome={self.nome!r} "
            f"ibge_code={self.ibge_code!r}>"
        )


class UfGeoid(Base):
    """TripAdvisor UF → geoId reference row.

    Loaded from data/tripadvisor/uf_geoids.json (27 rows). The DB fallback for
    resolve_geo_id reads this table on a Redis miss.
    """

    __tablename__ = "uf_geoids"

    uf: Mapped[str] = mapped_column(String(2), primary_key=True)
    geo_id: Mapped[int] = mapped_column(Integer, nullable=False)

    def __repr__(self) -> str:
        return f"<UfGeoid uf={self.uf!r} geo_id={self.geo_id}>"
