"""Mar service — promote_to_mar, reopen_from_error_report (D-03, D-15).

promote_to_mar:
  Idempotent Mar push keyed by source_ref (D-15).
  If the MarRecord already exists, update reliability_score and supersede old row (D-03).
  Carries full per-criterion provenance/lineage (D-06).

reopen_from_error_report (CNTR-02):
  Community error reports reopen a published record back into the review DLQ.
  Locates active MarRecord by source_ref, resets its linked RioRecord to routing='dlq'.
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from brave.core.models import MarRecord, RioRecord
from brave.core.repositories import SqlAlchemyMarRepository, SqlAlchemyRioRepository

# Stateless data-access seam (Phase A). The Session is passed per call and the
# caller still owns the transaction — these repos flush but never commit.
_mar_repo = SqlAlchemyMarRepository()
_rio_repo = SqlAlchemyRioRepository()

# Attraction recency backstop window (Phase F). Mirrors the SignalAgent
# no-recent-reviews rule: a review older than this (or missing) blocks promotion.
_REVIEW_MAX_AGE_DAYS = 90


def _attraction_review_recent(
    normalized: dict[str, Any],
    *,
    now: datetime | None = None,
    max_age_days: int = _REVIEW_MAX_AGE_DAYS,
) -> bool:
    """Return True iff normalized carries a most-recent review within max_age_days.

    Reads normalized["most_recent_review_at"] (ISO-8601 str, written by SignalAgent).
    Missing / None / unparseable → False (route to DLQ). Deterministic + offline:
    'now' is injectable so the 90-day boundary is pinnable in tests.
    """
    raw = normalized.get("most_recent_review_at")
    if not raw or not isinstance(raw, str):
        return False
    try:
        review_dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return False
    if review_dt.tzinfo is None:
        review_dt = review_dt.replace(tzinfo=timezone.utc)
    reference = now or datetime.now(timezone.utc)
    return (reference - review_dt) <= timedelta(days=max_age_days)


def promote_to_mar(
    session: Session,
    rio_record: RioRecord,
) -> MarRecord | None:
    """Create or update a MarRecord for a scored RioRecord.

    Idempotent: If a MarRecord with the same source_ref already exists,
    update reliability_score and supersede the old row (D-03, D-15).

    Provenance carries full per-criterion reliability breakdown (D-06).

    Attraction recency BACKSTOP (Phase F): a belt-and-suspenders guard behind the
    SignalAgent no-recent-reviews rule. If an ATTRACTION reaches promotion but its
    most-recent review is missing or older than 90 days (e.g. via a steward DLQ
    validate, or the phoneless TripAdvisor lane whose date is always None), it is
    NOT promoted — it is routed to DLQ (dlq_reason="no_recent_reviews") and this
    returns None. Destinos have no reviews and are UNAFFECTED (entity_type guard).
    Deterministic + offline: recency is read from normalized["most_recent_review_at"]
    against an injectable 'now'.

    Args:
        session:    SQLAlchemy synchronous Session.
        rio_record: Scored RioRecord (must have routing='mar').

    Returns:
        The MarRecord (existing updated, or newly created), or None when the
        attraction recency backstop routes the record to DLQ instead of promoting.
    """
    source_ref = rio_record.canonical_key or str(rio_record.id)
    normalized = rio_record.normalized or {}
    entity_type = rio_record.entity_type

    # Attraction recency BACKSTOP (Phase F) — attraction only, before any Mar write.
    # Missing or >90-day-old most-recent review → route to DLQ instead of promoting.
    if entity_type == "attraction" and not _attraction_review_recent(normalized):
        rio_record.routing = "dlq"
        rio_record.dlq_reason = "no_recent_reviews"
        session.flush()
        return None

    # Build canonical payload from normalized record. most_recent_review_at, contact
    # (Phase F), and google_enriched (the Places-enrichment idempotency marker) are
    # internal/board-only — exclude them alongside the five reliability *_value criteria
    # so the norteia-api Mar push shape stays byte-identical.
    canonical: dict[str, Any] = {
        k: v for k, v in normalized.items()
        if k not in ("origem_value", "completude_value", "corroboracao_value",
                     "atualidade_value", "validacao_humana_value",
                     "most_recent_review_at", "contact", "google_enriched")
    }

    # Build provenance (D-06) — full per-criterion breakdown + score_version
    provenance: dict[str, Any] = {
        "score_breakdown": rio_record.score_breakdown or {},
        "score_version": rio_record.score_version or "v1.0",
        "nascente_id": str(rio_record.nascente_id),
        "rio_id": str(rio_record.id),
    }

    reliability_score = float(rio_record.score or 0.0)
    score_version = rio_record.score_version or "v1.0"

    # Check for existing active MarRecord (D-15)
    existing = _mar_repo.get_active_by_source_ref(session, source_ref)

    if existing is not None:
        # Idempotent no-op (D-15): re-promoting unchanged data (e.g. a CLI
        # re-run or a reprocess that did not change the score) returns the
        # existing active row without writing — "re-push is a no-op upsert".
        if (
            float(existing.reliability_score) == reliability_score
            and existing.canonical == canonical
            and existing.provenance == provenance
            and existing.score_version == score_version
        ):
            return existing

        # Supersession pattern (D-03): the data changed (e.g. re-score after
        # human validation). Append a NEW active row and mark the old one
        # superseded BEFORE the single flush — at flush only the new row is
        # active, so the partial unique index uq_mar_active_source_ref holds.
        new_mar = MarRecord(
            id=uuid.uuid4(),
            rio_id=rio_record.id,
            entity_type=entity_type,
            source_ref=source_ref,
            canonical=canonical,
            provenance=provenance,
            reliability_score=reliability_score,
            score_version=score_version,
            parent_mar_id=existing.id,
        )
        # Assign superseded_by_id + add + single flush as one atomic step: at
        # flush only the new row is active, so the partial unique index
        # uq_mar_active_source_ref holds. Ordering preserved exactly.
        _mar_repo.supersede(session, existing, new_mar)
        return new_mar

    # First-time creation
    mar = MarRecord(
        id=uuid.uuid4(),
        rio_id=rio_record.id,
        entity_type=entity_type,
        source_ref=source_ref,
        canonical=canonical,
        provenance=provenance,
        reliability_score=reliability_score,
        score_version=score_version,
    )
    _mar_repo.add(session, mar)
    return mar


def reopen_from_error_report(
    session: Session,
    source_ref: str,
) -> RioRecord | None:
    """Reopen a published MarRecord back into the review DLQ (CNTR-02).

    Finds the active MarRecord by source_ref, then resets its linked
    RioRecord to routing='dlq' with dlq_reason='community_error_report'.

    Args:
        session:    SQLAlchemy synchronous Session.
        source_ref: Source reference identifying the published Mar record.

    Returns:
        The DLQ'd RioRecord, or None if source_ref not found in Mar.
    """
    mar = _mar_repo.get_active_by_source_ref(session, source_ref)
    if mar is None:
        return None

    rio = _rio_repo.get(session, mar.rio_id)
    if rio is None:
        return None

    # Reset RioRecord to DLQ for community review
    rio.routing = "dlq"
    rio.dlq_reason = "community_error_report"
    session.flush()

    return rio


def build_push_payload(
    mar_record: MarRecord,
    rio_record: RioRecord,
) -> dict[str, Any]:
    """Build the flat push payload norteia-api's ingestion contract expects.

    The Laravel API is the source of truth for shape: flat top-level fields (no
    ``canonical`` envelope). Territory is resolved by ``municipio_ibge``; the
    parent destino rides in ``destino`` for resolve-or-create; Google Places
    enrichment rides in ``place`` (lands in the separate attraction_place_details
    table). Provenance is flattened per-criterion (was D-16).

    Args:
        mar_record: MarRecord returned by promote_to_mar.
        rio_record: Source RioRecord (kept for signature compatibility).

    Returns:
        Dict matching the norteia-api ingestion contract (attraction or
        destination depending on ``mar_record.entity_type``).
    """
    provenance_raw = mar_record.provenance or {}
    score_breakdown = provenance_raw.get("score_breakdown", {})
    flat_provenance = {
        "origem": float(score_breakdown.get("origem", 0.0)),
        "completude": float(score_breakdown.get("completude", 0.0)),
        "corroboracao": float(score_breakdown.get("corroboracao", 0.0)),
        "atualidade": float(score_breakdown.get("atualidade", 0.0)),
        "validacao_humana": float(score_breakdown.get("validacao_humana", 0.0)),
    }

    # source_ref format "source:UF:id" → "mtur:BA:123"
    source_ref = mar_record.source_ref or ""
    parts = source_ref.split(":")
    source = parts[0] if parts and parts[0] else "unknown"
    uf = parts[1] if len(parts) >= 2 else ""

    canonical = mar_record.canonical or {}
    reliability = float(mar_record.reliability_score)

    if mar_record.entity_type == "destination":
        return {
            "source_ref": source_ref,
            "source": source,
            "tourist_name": canonical.get("name") or canonical.get("municipio") or "",
            "municipio_ibge": canonical.get("ibge_code") or canonical.get("municipio_id"),
            "reliability_score": reliability,
            "provenance": flat_provenance,
        }

    # attraction
    municipio_ibge = canonical.get("municipio_id") or canonical.get("ibge_code")
    contacts = canonical.get("contacts") or {}
    signal = canonical.get("signal") or {}
    # Parent destino link (destino-first). Fall back to the canonical IBGE ref so
    # the API can resolve-or-create even when a lane didn't stamp parent_source_ref.
    parent_ref = canonical.get("parent_source_ref") or f"ibge:{uf}:{municipio_ibge}"

    return {
        "source_ref": source_ref,
        "source": source,
        "name": canonical.get("name") or canonical.get("nome") or "",
        # ponytail: label_entity is a Phase-1 stub, so `tipo` (carried from the
        # lane in routing) is the only type signal; "outros" until NLP labeling lands.
        "type": canonical.get("tipo") or canonical.get("type") or "outros",
        "municipio_ibge": municipio_ibge,
        "description": canonical.get("descricao_editorial"),
        "latitude": canonical.get("lat"),
        "longitude": canonical.get("lon"),
        "address": canonical.get("address"),
        "instagram": contacts.get("ig_handle"),
        "whatsapp": contacts.get("phone_e164"),
        "website": contacts.get("website"),
        "reliability_score": reliability,
        "provenance": flat_provenance,
        "destino": {
            "source_ref": parent_ref,
            "source": parent_ref.split(":")[0] if parent_ref else source,
            "tourist_name": canonical.get("municipio") or canonical.get("municipio_nome") or "",
            "municipio_ibge": municipio_ibge,
        },
        "place": {
            "place_id": canonical.get("google_place_id")
            or canonical.get("place_id_cache")
            or canonical.get("place_id"),
            "business_status": signal.get("business_status"),
            "opening_hours": canonical.get("weekday_text") or signal.get("weekday_text"),
            "price_level": canonical.get("price_level"),
            "reviews_recent_count": signal.get("reviews_recent_count"),
            "distrito_code": canonical.get("distrito_code"),
            "distrito_name": canonical.get("distrito_name"),
            "distrito_municipio_ibge": canonical.get("distrito_municipio_ibge"),
            "subdistrito_name": canonical.get("subdistrito_name"),
            "subdistrito_code": canonical.get("subdistrito_code"),
            "distrito_source": canonical.get("distrito_source"),
        },
    }
