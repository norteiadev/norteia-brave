"""Duplicados endpoints — dedup candidate↔Mar pairs + resolve (UI-PAINEL-2).

Two surfaces for the Painel "Duplicados" view:

  - GET  /api/v1/dedup/pairs                              (require_bearer)
        Compute-on-read list of candidate↔Mar pairs. Each pending candidate
        Rio (routing in_progress|dlq) is paired with the ACTIVE Mar row that
        shares its territorial key (uf + municipio_id + entity_type). Similarity
        and matched/diverged field labels are computed in PYTHON from
        normalized vs canonical — NO pgvector operator is touched here.

        RESEARCH A1: RioRecord.embedding is a zero stub (compute_embedding →
        [0.0]*1536, brave/core/rio/dedup.py), so the pgvector cosine operator is
        degenerate. We therefore never invoke the pgvector distance operator in
        this offline read path (Pitfall 2 — that would force a silently-skipped
        integration test).

  - PATCH /api/v1/dedup/pairs/{candidate_rio_id}/resolve  (require_steward_or_bearer)
        merge | keep | discard, audited on every action.

Territorial-key blocking (CR-02): a candidate is NEVER paired with a Mar from a
different UF — São Domingos/BA ≠ São Domingos/SE (mirrors the dedup.py block).

Security: reads → require_bearer; mutation → require_steward_or_bearer. LGPD: the
pair surface reads canonical/normalized over the canonical allow-list only — no
phone fields (T-17.1-01-04).
"""

import uuid
from typing import Any, Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from brave.api.deps import get_db, require_bearer, require_steward_or_bearer
from brave.core.models import MarRecord, RioRecord
from brave.observability.audit import write_audit

logger = structlog.get_logger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# reliability scoring keys excluded from the canonical allow-list (mar/service.py:46-50)
# ---------------------------------------------------------------------------

_SCORING_KEYS = frozenset(
    {
        "origem_value",
        "completude_value",
        "corroboracao_value",
        "atualidade_value",
        "validacao_humana_value",
    }
)


# ---------------------------------------------------------------------------
# Response models (A5 typed contract — extra="forbid", mirrored by the MSW handler)
# ---------------------------------------------------------------------------


class DedupPairItem(BaseModel):
    """A single candidate↔Mar dedup pair (compute-on-read)."""

    model_config = {"extra": "forbid"}

    candidate_id: str
    mar_id: str
    candidate_rio_id: str
    mar_rio_id: str
    uf: str
    municipio: str | None
    entity_type: str
    similarity: float
    similarity_source: str
    matched_fields: list[str]
    diverged_fields: list[dict[str, Any]]


class DedupPairsResponse(BaseModel):
    """Paginated envelope for GET /api/v1/dedup/pairs."""

    model_config = {"extra": "forbid"}

    items: list[DedupPairItem]
    total: int
    offset: int
    limit: int


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class ResolveBody(BaseModel):
    """Body for PATCH /api/v1/dedup/pairs/{candidate_rio_id}/resolve."""

    model_config = {"extra": "forbid"}

    action: Literal["merge", "keep", "discard"]
    mar_id: uuid.UUID


# ---------------------------------------------------------------------------
# Compute-on-read helpers (pure — offline-unit-testable)
# ---------------------------------------------------------------------------


def _compute_field_diff(
    normalized: dict[str, Any], canonical: dict[str, Any]
) -> tuple[list[str], list[dict[str, Any]]]:
    """Diff a candidate's normalized payload vs the Mar's canonical payload.

    Compares only the canonical allow-list keys (the reliability scoring *_value keys
    are excluded, matching mar/service.py:46-50). For each comparable key:
      - present in BOTH and equal           → matched
      - present in BOTH and unequal          → diverged ({field, candidate, mar})
      - present in only one                  → ignored (nothing to compare)
    """
    keys = (set(normalized) - _SCORING_KEYS) | set(canonical)
    matched: list[str] = []
    diverged: list[dict[str, Any]] = []
    for key in sorted(keys):
        if key in _SCORING_KEYS:
            continue
        if key in normalized and key in canonical:
            rv = normalized[key]
            mv = canonical[key]
            if rv == mv:
                matched.append(key)
            else:
                diverged.append({"field": key, "candidate": rv, "mar": mv})
    return matched, diverged


def _token_similarity(
    normalized: dict[str, Any], canonical: dict[str, Any]
) -> float:
    """Labeled placeholder similarity from name/município/UF token overlap.

    Jaccard over the lowercased tokens of the name/municipio/uf fields. This is a
    deliberate stand-in for real embedding similarity (deferred — A1); it never
    invokes the pgvector distance operator, so the read path stays fully offline.
    """

    def _tokens(d: dict[str, Any]) -> set[str]:
        out: set[str] = set()
        for field in ("name", "municipio", "uf"):
            value = d.get(field)
            if value:
                out.update(str(value).lower().split())
        return out

    a = _tokens(normalized)
    b = _tokens(canonical)
    union = a | b
    if not union:
        return 0.0
    return round(len(a & b) / len(union), 4)


def _find_active_mar_for(db: Session, candidate: RioRecord) -> MarRecord | None:
    """Return the ACTIVE Mar row on the candidate's territorial key, or None.

    Territorial-key block (CR-02): join MarRecord → its rio → match
    uf + municipio_id + entity_type against the candidate. NEVER widen across UF.
    Active-only via superseded_by_id IS NULL (models.py:191).
    """
    stmt = (
        select(MarRecord)
        .join(RioRecord, MarRecord.rio_id == RioRecord.id)
        .where(
            RioRecord.uf == candidate.uf,
            RioRecord.municipio_id == candidate.municipio_id,
            RioRecord.entity_type == candidate.entity_type,
            MarRecord.superseded_by_id.is_(None),
        )
        .limit(1)
    )
    return db.scalars(stmt).first()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/api/v1/dedup/pairs", dependencies=[Depends(require_bearer)])
def list_dedup_pairs(
    uf: str | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> dict:
    """List dedup candidate↔Mar pairs (compute-on-read, territorial-key blocked).

    Candidates are pending Rio rows (routing in_progress|dlq) with a municipio_id
    (no block → no pairing). Each is paired with the active Mar row on its
    territorial key; similarity + matched/diverged labels are computed in Python.
    """
    stmt = select(RioRecord).where(
        RioRecord.routing.in_(("in_progress", "dlq")),
        RioRecord.municipio_id.isnot(None),
    )
    if uf:
        stmt = stmt.where(RioRecord.uf == uf)

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    candidates = list(db.scalars(stmt.offset(offset).limit(limit)).all())

    items: list[DedupPairItem] = []
    for cand in candidates:
        mar = _find_active_mar_for(db, cand)
        if mar is None:
            continue
        normalized = cand.normalized or {}
        canonical = mar.canonical or {}
        matched, diverged = _compute_field_diff(normalized, canonical)
        items.append(
            DedupPairItem(
                candidate_id=str(cand.id),
                mar_id=str(mar.id),
                candidate_rio_id=str(cand.id),
                mar_rio_id=str(mar.rio_id),
                uf=cand.uf,
                municipio=cand.municipio_id,
                entity_type=cand.entity_type,
                similarity=_token_similarity(normalized, canonical),
                similarity_source="embedding_stub",
                matched_fields=matched,
                diverged_fields=diverged,
            )
        )

    return DedupPairsResponse(
        items=items, total=total, offset=offset, limit=limit
    ).model_dump()


@router.patch(
    "/api/v1/dedup/pairs/{candidate_rio_id}/resolve",
    status_code=200,
    dependencies=[Depends(require_steward_or_bearer)],
)
def resolve_pair(
    candidate_rio_id: uuid.UUID,
    body: ResolveBody,
    db: Session = Depends(get_db),
) -> dict:
    """Resolve a dedup pair: merge | keep | discard (audited) (LOCKED A2).

    merge   — UNION the candidate's source_ref into the EXISTING Mar's
              provenance["merged_source_refs"] and route the candidate Rio →
              descarte. NO new MarRecord, NO supersession/Mar-promotion path, NO
              409 on differing sources (this OVERRIDES RESEARCH Pitfall 4, which
              is stale). The existing Mar's own source_ref is never mutated, so
              the partial unique index uq_mar_active_source_ref stays satisfied.
    discard — candidate Rio → descarte, dlq_reason="dedup_discarded".
    keep    — no row change; the "dedup_kept" audit row IS the suppression marker
              (A3 — no schema change).
    """
    rio = db.get(RioRecord, candidate_rio_id)
    if rio is None:
        raise HTTPException(status_code=404, detail="RioRecord not found")

    before = {"routing": rio.routing}

    if body.action == "discard":
        rio.routing = "descarte"
        rio.dlq_reason = "dedup_discarded"
        action = "dedup_discarded"
    elif body.action == "merge":
        mar = db.get(MarRecord, body.mar_id)
        if mar is None:
            raise HTTPException(status_code=404, detail="MarRecord not found")
        # UNION the candidate's source_ref into the existing Mar provenance.
        # RioRecord has no source_ref column, so derive it the same way the Mar
        # promotion service does (mar/service.py:41): canonical_key or the row id.
        candidate_source_ref = rio.canonical_key or str(rio.id)
        provenance = dict(mar.provenance or {})
        merged = list(provenance.get("merged_source_refs", []))
        merged.append(candidate_source_ref)
        # Reassign a fresh dict so SQLAlchemy detects the JSON change.
        mar.provenance = {**provenance, "merged_source_refs": merged}
        # Discard the candidate Rio from the pending dedup pool — do NOT create a
        # 2nd active Mar, do NOT touch mar.source_ref, do NOT run the Mar
        # promotion/supersession path.
        rio.routing = "descarte"
        action = "dedup_merged"
    else:  # keep
        action = "dedup_kept"

    write_audit(
        session=db,
        action=action,
        entity_type=rio.entity_type,
        record_id=rio.id,
        before_state=before,
        after_state={"routing": rio.routing},
        actor="steward",
    )
    db.commit()
    return {"status": "ok", "action": body.action}
