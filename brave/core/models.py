"""SQLAlchemy 2.0 declarative models for the three medallion layers and observability tables.

Tables (D-01):
  - nascente_records  — immutable raw payload store
  - rio_records       — mutable working area (dedup/normalize/score/route)
  - mar_records       — canonical published store
  - llm_generations   — LLM call observability (D-20)
  - audit_log         — steward + pipeline audit trail (D-21, OBS-04)
  - poison_quarantine — Celery poison messages (separate from §7.6 DLQ)

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
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    func,
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

    source_ref is UNIQUE — idempotent push keyed by source_ref (D-15).
    Supersession by appending a new row + superseded_by_id FK (D-03).
    """

    __tablename__ = "mar_records"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    rio_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rio_records.id"), nullable=False
    )
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    # Unique: idempotent push keyed by source_ref (D-15)
    source_ref: Mapped[str] = mapped_column(
        String(256), nullable=False, unique=True, index=True
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
    superseded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("mar_records.id"), nullable=True
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
