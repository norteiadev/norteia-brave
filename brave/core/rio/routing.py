"""Rio routing — reliability score, routing column, and pipeline orchestration (D-02, D-12, D-13).

route_by_score:          Score a RioRecord and set its routing column.
process_nascente_record: Full Rio pipeline (dedup → normalize → label → route).
reprocess_record:        Re-score an existing RioRecord (row-locked, verdicts sticky).
"""

import uuid
from collections.abc import Collection
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from brave.config.settings import ScoreConfig
from brave.core.models import MarRecord, NascenteRecord, RioRecord
from brave.core.repositories import SqlAlchemyRioRepository
from brave.core.rio.dedup import compute_embedding, find_duplicate
from brave.core.rio.label import label_entity
from brave.core.rio.normalize import normalize_address, normalize_coordinates, normalize_name
from brave.core.rio.persist import persist_normalized
from brave.core.score.engine import compute_score
from brave.core.score.schemas import ScoreInput
from brave.observability.record_events import record_event

logger = structlog.get_logger(__name__)

# Stateless data-access seam (Phase A). The Session is passed per call and the
# caller still owns the transaction — this repo flushes but never commits.
_rio_repo = SqlAlchemyRioRepository()


# Deliberate DLQ verdicts (C5): a decision an agent or steward took about the record that a
# plain re-score must not erase. "score=..." reasons (and None) are just the score talking.
# no_recent_reviews is kept only while the recency rule still fails.
STICKY_DLQ_REASONS = frozenset(
    {
        "closed_temporarily",
        "municipio_nao_confirmado",
        "descricao_nao_fundamentada",
        "no_recent_reviews",
        "steward_sent_to_review",
        "community_error_report",
    }
)


def route_by_score(
    session: Session | None,
    rio_record: RioRecord,
    config: ScoreConfig,
    *,
    redecided: Collection[str] = (),
    override_verdicts: bool = False,
) -> RioRecord:
    """Apply reliability score to a RioRecord and set its routing column.

    Reads scoring inputs from rio_record.normalized dict.
    Sets: routing, score, score_breakdown, score_version, processed_at.

    Verdicts are sticky (C5) for EVERY caller: a re-score never resurrects ``descarte``
    nor replaces a STICKY_DLQ_REASONS verdict with a "score=..." reason (or with Mar) —
    score fields still update. ``redecided`` names the verdicts the caller has just
    re-judged itself (it re-applies the ones that still hold, e.g. SignalAgent with a fresh
    business_status); ``override_verdicts`` is the explicit human decision (validate /
    promote) and drops them all.

    Note: session is accepted for future SELECT FOR UPDATE locking on state
    transitions (production use); unit tests pass None.

    Args:
        session:    SQLAlchemy Session (used for SELECT FOR UPDATE in production).
                    None is accepted for unit tests.
        rio_record: The RioRecord to score and route.
        config:     ScoreConfig with reliability weights and thresholds.

    Returns:
        The updated RioRecord (mutated in-place, also returned for chaining).
    """
    normalized = rio_record.normalized or {}

    # Build ScoreInput from the normalized dict (with safe defaults)
    score_input = ScoreInput(
        origem_value=float(normalized.get("origem_value", 0.0)),
        completude_value=float(normalized.get("completude_value", 0.0)),
        corroboracao_value=float(normalized.get("corroboracao_value", 0.0)),
        atualidade_value=float(normalized.get("atualidade_value", 0.0)),
        validacao_humana_value=float(normalized.get("validacao_humana_value", 0.0)),
    )

    result = compute_score(score_input, config)

    prev_routing, prev_reason = rio_record.routing, rio_record.dlq_reason

    # Mutate RioRecord fields
    rio_record.score = result.score
    rio_record.routing = result.routing
    rio_record.score_version = result.score_version
    rio_record.score_breakdown = {
        "origem": result.breakdown.origem,
        "completude": result.breakdown.completude,
        "corroboracao": result.breakdown.corroboracao,
        "atualidade": result.breakdown.atualidade,
        "validacao_humana": result.breakdown.validacao_humana,
    }
    rio_record.processed_at = datetime.now(UTC)

    # Set dlq_reason when routing to DLQ
    if result.routing == "dlq":
        rio_record.dlq_reason = (
            f"score={result.score:.2f} below threshold_mar={config.threshold_mar}"
        )
    else:
        rio_record.dlq_reason = None

    if override_verdicts:
        return rio_record
    if prev_routing == "descarte":
        rio_record.routing, rio_record.dlq_reason = "descarte", prev_reason
    elif prev_reason in STICKY_DLQ_REASONS and prev_reason not in redecided:
        if prev_reason == "no_recent_reviews":
            from brave.core.mar.service import _attraction_review_recent  # noqa: PLC0415

            if _attraction_review_recent(normalized):
                return rio_record
        rio_record.routing, rio_record.dlq_reason = "dlq", prev_reason
    return rio_record


def _build_normalized(nascente: NascenteRecord) -> dict[str, Any]:
    """Cherry-pick + normalize a Nascente payload into the Rio ``normalized`` dict."""
    # Build normalized record from nascente.payload
    payload = nascente.payload or {}

    # Normalize fields
    name = payload.get("name", "")
    if name:
        name = normalize_name(name)

    address = payload.get("address")
    if address:
        address = normalize_address(address)

    lat = payload.get("lat")
    # Longitude key drift: TripAdvisor/Places payloads store longitude under "lng"
    # (see atrativos/destinos payloads + places client), while geocoder/legacy
    # payloads use "lon". Reading only "lon" left normalized["lon"] perpetually
    # null for every TA/Places record — accept either key.
    lon = payload.get("lon")
    if lon is None:
        lon = payload.get("lng")
    lat, lon = normalize_coordinates(lat, lon)

    # Build normalized dict — preserve score input fields from payload
    normalized: dict[str, Any] = {
        "name": name,
        "address": address,
        "lat": lat,
        "lon": lon,
        # Score criterion values (from payload or defaults)
        "origem_value": float(payload.get("origem_value", 0.0)),
        "completude_value": float(payload.get("completude_value", 0.0)),
        "corroboracao_value": float(payload.get("corroboracao_value", 0.0)),
        "atualidade_value": float(payload.get("atualidade_value", 0.0)),
        "validacao_humana_value": float(payload.get("validacao_humana_value", 0.0)),
    }

    # Preserve público-geo município (nome + IBGE code) into normalized so board cards
    # can show it without a nascente JOIN. Entity-agnostic (destino + atrativo payloads
    # both carry canonical.municipio + municipio_id). município nome/UF/IBGE are PUBLIC
    # geo-territorial fields — NOT PII (same class as name/uf).
    _canonical = payload.get("canonical") or {}
    _municipio = _canonical.get("municipio")
    if _municipio:
        normalized["municipio"] = _municipio
    _municipio_id = payload.get("municipio_id") or _canonical.get("ibge_code")
    if _municipio_id:
        normalized["municipio_id"] = _municipio_id

    # Carry the editorial description (descricao_editorial) from canonical into
    # normalized so it survives the Rio cherry-pick and flows Mar→push→drawer (the
    # DescriptionEnrichmentAgent later overwrites it in-place with the Norteia-voice
    # rewrite). Without this it would die in the Rio exactly like `posicionamento`
    # does today (POC §7). Schemaless passthrough — no migration, no exclusion in
    # promote_to_mar. On discovery it is absent → key simply not set (floor preserved).
    _descricao = _canonical.get("descricao_editorial")
    if _descricao:
        normalized["descricao_editorial"] = _descricao

    # Carry distrito/subdistrito localization (IBGE DTB) from canonical into normalized so
    # it survives the Rio cherry-pick and flows Mar→push→drawer. These are PUBLIC geo-
    # territorial fields (same class as município) — NOT PII; do NOT add to any exclusion/
    # blocklist. On discovery/TA lanes without a distrito signal they are absent → key
    # simply not set (floor preserved), exactly like descricao_editorial above.
    _distrito_name = _canonical.get("distrito_name")
    if _distrito_name:
        normalized["distrito_name"] = _distrito_name
    _distrito_code = _canonical.get("distrito_code")
    if _distrito_code:
        normalized["distrito_code"] = _distrito_code
    _distrito_municipio_ibge = _canonical.get("distrito_municipio_ibge")
    if _distrito_municipio_ibge:
        normalized["distrito_municipio_ibge"] = _distrito_municipio_ibge
    _subdistrito_name = _canonical.get("subdistrito_name")
    if _subdistrito_name:
        normalized["subdistrito_name"] = _subdistrito_name
    _subdistrito_code = _canonical.get("subdistrito_code")
    if _subdistrito_code:
        normalized["subdistrito_code"] = _subdistrito_code
    _distrito_source = _canonical.get("distrito_source")
    if _distrito_source:
        normalized["distrito_source"] = _distrito_source

    # Attraction-specific: preserve place_id_cache so ContactFinderAgent and SignalAgent
    # can look up Place Details without repeating text_search (D-04, COMP-03).
    # This cache key is written by DiscoveryAgent into the nascente payload; copying it
    # to normalized ensures subsequent FSM tasks have it available.
    # Attraction-specific: carry the lane taxonomy `tipo` (AtrativoResult.tipo:
    # praia/cachoeira/trilha/…) from canonical into normalized so it survives the
    # Rio cherry-pick and lands in the Mar canonical → norteia-api `type` (required).
    # label_entity is a Phase-1 stub, so this is the only type signal today.
    if nascente.entity_type == "attraction":
        _tipo = _canonical.get("tipo")
        if _tipo:
            normalized["tipo"] = _tipo
        # Recency evidence for the promote_to_mar 90-day backstop (mar/service.py reads
        # normalized["most_recent_review_at"]). Lane-agnostic: ANY lane that puts a real
        # review date in its payload gets it carried into the Rio. Absent/None → key not
        # set (the backstop stays closed, floor preserved).
        _review_at = payload.get("most_recent_review_at")
        if _review_at:
            normalized["most_recent_review_at"] = _review_at
        # Review volume for the backstop's "established attraction" window (same reader).
        if payload.get("review_count"):
            normalized["review_count"] = payload["review_count"]
        # Raw TA card category (AttractionsFusion primaryInfo.text, top-level in the
        # payload) — the Mar maps it to the API `type` (C6). Dropping it here made every
        # TA atrativo publish as type='outros'.
        if payload.get("category"):
            normalized["category"] = payload["category"]

    # Destination-specific: the MTur flags ensure_destino writes into canonical. Presence,
    # not truthiness — an explicit False must reach the push too (build_push_payload reads
    # participates_mtur; without the copy every destino pushed False).
    if nascente.entity_type == "destination":
        for _k in ("participates_mtur", "mtur_categoria", "regiao_turistica"):
            if _k in _canonical:
                normalized[_k] = _canonical[_k]

    if nascente.entity_type == "attraction" and "place_id_cache" in payload:
        normalized["place_id_cache"] = payload["place_id_cache"]
        # google_place_id: same id as a clean platform-facing canonical field (flows to
        # norteia-api). place_id_cache stays the internal FSM lookup key. The TA lane
        # writes google_place_id in PlacesEnrichmentAgent (it has no place_id at Nascente).
        normalized["google_place_id"] = payload["place_id_cache"]

    # Attraction-specific: preserve the parent-destino linkage from payload so the CMS
    # and downstream queries can surface/group atrativos by parent destino without a
    # nascente JOIN. Both lanes now write the destino-first linkage:
    #   - parent_rio_id + parent_source_ref: always present (the ensured IBGE destino
    #     is promoted to Rio unconditionally — see brave/shared/destino.ensure_destino).
    #   - parent_mar_id: present ONLY when that destino already reached Mar (cms.py's
    #     detail endpoint loads the parent MarRecord from it).
    # Copying all three keeps the CMS parent linkage working uniformly whether or not
    # the destino reached Mar. Mirrors the place_id_cache copy above (D-03 / G2 gap fix).
    if nascente.entity_type == "attraction":
        if "parent_rio_id" in payload:
            normalized["parent_rio_id"] = payload["parent_rio_id"]
        if "parent_source_ref" in payload:
            normalized["parent_source_ref"] = payload["parent_source_ref"]
        if "parent_mar_id" in payload:
            normalized["parent_mar_id"] = payload["parent_mar_id"]

    # Attraction-specific: carry a lane-supplied MASKED WhatsApp candidate (Phase F)
    # from payload["contact"] into normalized["contact"]. Value is already masked at
    # the lane boundary (whatsapp_candidate_from_phone → mask_phone); it is excluded
    # from the Mar `canonical` push in promote_to_mar so the norteia-api shape is
    # unchanged. Mirrors the place_id_cache / parent_mar_id attraction-scoped copies.
    if nascente.entity_type == "attraction" and "contact" in payload:
        normalized["contact"] = payload["contact"]

    # Add taxonomy labels (Phase 1 stub)
    normalized = label_entity(nascente.entity_type, normalized)
    return normalized


_SCORE_INPUTS = (
    "origem_value",
    "completude_value",
    "corroboracao_value",
    "atualidade_value",
    "validacao_humana_value",
)


def _apply_new_version(
    session: Session, rio: RioRecord, nascente: NascenteRecord, config: ScoreConfig
) -> None:
    """Merge a superseding Nascente version into its existing Rio row (F16).

    Three-way merge against the version the row was built from: a key takes the new
    payload's value only when no one changed it since (current == old version's value) or
    the row never had it. So enrichment (google_enriched, the rewritten description,
    Places address, signal, backfilled geo) is kept, while lane facts nobody touched
    (participates_mtur, parent linkage, review_count, category...) follow the source.
    Re-scored only when a score input moved and the record still waits on the score.
    """
    old = session.get(NascenteRecord, rio.nascente_id)
    base = _build_normalized(old) if old is not None else {}
    current = dict(rio.normalized or {})
    after = dict(current)
    for key, value in _build_normalized(nascente).items():
        if key not in current or current[key] == base.get(key):
            after[key] = value
    changed = {k for k, v in after.items() if current.get(k) != v}
    if changed:
        persist_normalized(session, rio, current, after)
    old_municipio_id = ((old.payload or {}) if old is not None else {}).get("municipio_id")
    new_municipio_id = (nascente.payload or {}).get("municipio_id")
    if new_municipio_id and rio.municipio_id == old_municipio_id:
        rio.municipio_id = new_municipio_id
    # Repoint even with no field change: the Painel's 'unrouted' count joins on nascente_id
    # over ACTIVE versions, so a stale pointer shows the record as never routed.
    rio.nascente_id = nascente.id
    if changed & set(_SCORE_INPUTS) and rio.routing in ("in_progress", "dlq") and not rio.sub_state:
        route_by_score(session, rio, config)
    session.flush()
    record_event(
        session,
        source=nascente.source,
        source_ref=nascente.source_ref,
        stage="superseded",
        status="ok",
        message=(rio.normalized or {}).get("name"),
        entity_type=rio.entity_type,
        uf=rio.uf,
        nascente_id=nascente.id,
        rio_id=rio.id,
        data={"version": nascente.version, "changed": sorted(changed)},
    )


def process_nascente_record(
    session: Session,
    nascente: NascenteRecord,
    config: ScoreConfig,
    llm_client: Any = None,  # LLMClientProtocol | None
) -> RioRecord:
    """Full Rio pipeline: dedup → normalize → label → route.

    Idempotent: If a RioRecord already exists for this canonical_key, returns it without
    re-processing — unless ``nascente`` is a newer ACTIVE version of it (store_raw
    supersession), which is merged in first (see _apply_new_version).

    Pipeline steps (D-07):
    1. Exact content_hash dedup
    2. Territorial-key-blocked pgvector fuzzy dedup
    3. Normalize names/coordinates/addresses
    4. Label with Norteia taxonomy (Phase 1 stub)
    5. Score via reliability pure function
    6. Route to mar/dlq (binary threshold_mar gate)

    Args:
        session:    SQLAlchemy synchronous Session.
        nascente:   NascenteRecord to process.
        config:     ScoreConfig with reliability weights.
        llm_client: LLMClientProtocol for future extraction (Phase 2). Unused in Phase 1.

    Returns:
        The RioRecord for this nascente (existing if already processed).
    """
    # Idempotency check: use canonical_key = source_ref from payload or nascente.source_ref
    canonical_key = nascente.source_ref

    existing = _rio_repo.get_by_canonical_key(session, canonical_key)
    if existing is not None:
        # A superseded (older) version never applies: A→B→A comes back from store_raw as
        # the superseded v1, and applying it would flip the record back and forth.
        if existing.nascente_id != nascente.id and nascente.superseded_by_id is None:
            _apply_new_version(session, existing, nascente, config)
        return existing

    payload = nascente.payload or {}
    normalized = _build_normalized(nascente)
    name = normalized.get("name")

    # Compute embedding (Phase 1 stub — zero vector)
    embed_text = name or canonical_key
    embedding = compute_embedding(embed_text)

    # Check for duplicate before creating a new RioRecord
    municipio_id = payload.get("municipio_id")
    duplicate = find_duplicate(
        session=session,
        uf=nascente.uf,
        municipio_id=municipio_id,
        entity_type=nascente.entity_type,
        content_hash=nascente.content_hash,
        embedding=embedding,
    )
    if duplicate is not None:
        # Duplicate-detected return — minimal public-geo log (LGPD-safe).
        logger.info(
            "registro_deduplicado",
            entity_type=duplicate.entity_type,
            uf=duplicate.uf,
            name=((duplicate.normalized or {}).get("name")),
        )
        # Append-only Log-tab timeline event (behind the canonical_key early-return).
        # LGPD: public-geo fields only. Keyed by this record's source_ref so it shows
        # under the same drawer even though it collapsed onto an existing Rio row.
        record_event(
            session,
            source=nascente.source,
            source_ref=canonical_key,
            stage="deduped",
            status="skip",
            message=((duplicate.normalized or {}).get("name")),
            entity_type=nascente.entity_type,
            uf=nascente.uf,
            nascente_id=nascente.id,
            rio_id=duplicate.id,
        )
        return duplicate

    # Create RioRecord
    rio = RioRecord(
        id=uuid.uuid4(),
        nascente_id=nascente.id,
        entity_type=nascente.entity_type,
        uf=nascente.uf,
        municipio_id=municipio_id,
        routing="in_progress",
        normalized=normalized,
        # ponytail: don't persist 1536 zeros per row while compute_embedding is a
        # stub (column is nullable; HNSW index skips NULLs). Real vectors persist as-is.
        embedding=embedding if any(embedding) else None,
        canonical_key=canonical_key,
    )
    # Savepoint: a concurrent writer (bulk sweep x per-UF sweep, TA x Places ensure_destino)
    # may insert the same canonical_key first. uq_rio_canonical_key then fails only this
    # INSERT — not the caller's whole transaction — and the winner's row is returned.
    try:
        with session.begin_nested():
            _rio_repo.add(session, rio)
    except IntegrityError:
        winner = _rio_repo.get_by_canonical_key(session, canonical_key)
        if winner is None:
            raise
        return winner

    # Apply reliability scoring and routing
    route_by_score(session, rio, config)
    session.flush()

    # Per-entity sync log at the PRIMARY routed return — rio.routing is now final
    # (mar/dlq/in_progress). LGPD: public-geo fields only (name, uf, routing, score).
    logger.info(
        "registro_roteado",
        entity_type=rio.entity_type,
        uf=rio.uf,
        name=((rio.normalized or {}).get("name")),
        routing=rio.routing,
        score=(float(rio.score) if rio.score is not None else None),
    )

    # Append-only Log-tab timeline events (behind the canonical_key early-return).
    # LGPD: only reliability engineering fields (score, routing, dlq_reason, version).
    _score = float(rio.score) if rio.score is not None else None
    record_event(
        session,
        source=nascente.source,
        source_ref=canonical_key,
        stage="scored",
        status="ok",
        message=((rio.normalized or {}).get("name")),
        entity_type=rio.entity_type,
        uf=rio.uf,
        nascente_id=nascente.id,
        rio_id=rio.id,
        data={"score": _score, "score_version": rio.score_version},
    )
    record_event(
        session,
        source=nascente.source,
        source_ref=canonical_key,
        stage="routed",
        status=("fail" if rio.routing == "dlq" else "ok"),
        message=rio.dlq_reason,
        entity_type=rio.entity_type,
        uf=rio.uf,
        nascente_id=nascente.id,
        rio_id=rio.id,
        data={
            "routing": rio.routing,
            "dlq_reason": rio.dlq_reason,
            "score": _score,
        },
    )

    return rio


def reprocess_record(
    session: Session,
    rio_id: uuid.UUID,
    config: ScoreConfig,
    *,
    override_verdicts: bool = False,
) -> RioRecord:
    """Re-score an existing RioRecord under a row lock.

    Idempotent: re-running with the same config produces the same result.
    Used for: config change (threshold tuning), new corroboration, human validation,
    or error report reopen.

    Verdicts are sticky (see route_by_score): a reprocess never resurrects ``descarte``
    nor erases a deliberate DLQ reason. Only the human promote path
    (validate_and_promote_rio) passes ``override_verdicts=True`` — it is also the only
    caller that writes the MarRecord, so it alone may leave the record at routing='mar'.
    Anywhere else a qualifying score with no active MarRecord parks the record in the DLQ
    as ``awaiting_promotion``: 'mar' without a MarRecord is a dead end (no board edge
    leaves the Mar column and publish() refuses it).

    Args:
        session: SQLAlchemy synchronous Session.
        rio_id:  UUID of the RioRecord to reprocess.
        config:  ScoreConfig with reliability weights (may differ from original score).
        override_verdicts: True only for an explicit human decision (validate/promote).

    Returns:
        The updated RioRecord.

    Raises:
        ValueError: If no RioRecord with rio_id exists.
    """
    # FOR UPDATE + populate_existing: request handlers already hold this instance in the
    # identity map, and without populate_existing the lock would re-score its stale copy
    # (lost update against an agent / batch collect that committed meanwhile).
    rio = session.get(RioRecord, rio_id, with_for_update=True, populate_existing=True)
    if rio is None:
        raise ValueError(f"RioRecord {rio_id} not found")

    route_by_score(session, rio, config, override_verdicts=override_verdicts)
    if rio.routing == "mar" and not override_verdicts:
        active_mar = session.scalar(
            select(MarRecord.id).where(
                MarRecord.rio_id == rio.id, MarRecord.superseded_by_id.is_(None)
            )
        )
        if active_mar is None:
            rio.routing, rio.dlq_reason = "dlq", "awaiting_promotion"
    session.flush()

    return rio
