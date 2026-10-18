"""Mar service — promote_to_mar, reopen_from_error_report (D-03, D-15).

promote_to_mar:
  Idempotent Mar push keyed by source_ref (D-15).
  If the MarRecord already exists, update reliability_score and supersede old row (D-03).
  Carries full per-criterion provenance/lineage (D-06).

reopen_from_error_report (CNTR-02):
  Community error reports reopen a published record back into the review DLQ.
  Locates active MarRecord by source_ref, resets its linked RioRecord to routing='dlq'.
"""

import unicodedata
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from brave.core.models import MarRecord, RioRecord
from brave.core.repositories import SqlAlchemyMarRepository, SqlAlchemyRioRepository
from brave.shared.opening_hours import to_hours_map

# Stateless data-access seam (Phase A). The Session is passed per call and the
# caller still owns the transaction — these repos flush but never commit.
_mar_repo = SqlAlchemyMarRepository()
_rio_repo = SqlAlchemyRioRepository()

# Attraction recency backstop window (Phase F). The SignalAgent no-recent-reviews rule
# calls this same helper (over its merged TA+Google view), so the two cannot drift:
# a review older than this (or missing) blocks promotion.
_REVIEW_MAX_AGE_DAYS = 90
# ...unless the attraction is ESTABLISHED: the 90-day rule was written for business
# liveness (a restaurant that shut), and measured on the TA base it held back 189 of 275
# blocked atrativos that have 20+ reviews — a beach or a church whose last review is four
# months old is not a dead place. Enough review volume buys a one-year window instead.
# A closed place is still dropped upstream by Google's CLOSED_* business_status.
_ESTABLISHED_MIN_REVIEWS = 20
_ESTABLISHED_MAX_AGE_DAYS = 365


def _attraction_review_recent(
    normalized: dict[str, Any],
    *,
    now: datetime | None = None,
    max_age_days: int = _REVIEW_MAX_AGE_DAYS,
) -> bool:
    """Return True iff normalized carries a most-recent review within max_age_days —
    or within _ESTABLISHED_MAX_AGE_DAYS when normalized["review_count"] reaches
    _ESTABLISHED_MIN_REVIEWS.

    Reads normalized["most_recent_review_at"] (ISO-8601 str, written by the TA ingest,
    SignalAgent and PlacesEnrichmentAgent). Also the SignalAgent's no-recent-reviews gate.
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
    age = (now or datetime.now(timezone.utc)) - review_dt
    if age <= timedelta(days=max_age_days):
        return True
    try:
        established = int(normalized.get("review_count") or 0) >= _ESTABLISHED_MIN_REVIEWS
    except (TypeError, ValueError):
        established = False
    return established and age <= timedelta(days=_ESTABLISHED_MAX_AGE_DAYS)


def _split_phone_for_push(phone_raw: str | None) -> tuple[str | None, str | None]:
    """Classify a raw phone into ``(whatsapp_e164, telefone_e164)`` for the push.

    Google Places returns a single ``internationalPhoneNumber`` with no mobile/landline
    flag. Product rule: a BR **celular** (11 national digits, subscriber part starting
    ``9``) is a plausible WhatsApp number → the ``whatsapp`` field; anything else (fixo/
    other) → the ``telefone`` field. Exactly one slot is filled; the other is None.

    Returns E.164 (``+55…``) for a recognised BR number. A number that isn't a
    recognisable BR shape (e.g. a foreign line) is surfaced verbatim as ``telefone``
    (never guessed as WhatsApp). Empty/None → ``(None, None)``.
    """
    if not phone_raw:
        return None, None
    digits = "".join(c for c in phone_raw if c.isdigit())
    if not digits:
        return None, None
    # Foreign line (explicit non-BR country code, e.g. "+1 415…"): never guess
    # WhatsApp — pass through verbatim as telefone.
    if phone_raw.strip().startswith("+") and not digits.startswith("55"):
        return None, phone_raw
    if digits.startswith("55") and len(digits) in (12, 13):
        national = digits[2:]
    elif len(digits) in (10, 11):
        national = digits  # bare BR national (no country code)
    else:
        # Unrecognisable shape — never guess WhatsApp; pass through as telefone.
        return None, phone_raw
    e164 = f"+55{national}"
    if len(national) == 11 and national[2] == "9":
        return e164, None  # celular → whatsapp
    return None, e164  # fixo / other → telefone


# Places API (New) returns priceLevel as an ENUM NAME ("PRICE_LEVEL_MODERATE");
# norteia-api's attraction_place_details.price_level is an INTEGER column and its
# request validator rejects anything else — a single bad key 422s the whole
# attraction push. Map to Google's legacy 0–4 price_level scale (the same scale the
# Pact freezes as 2). NOT the proto ordinals (FREE=1…VERY_EXPENSIVE=5), which are
# off by one. Unknown/UNSPECIFIED → None; the column is nullable.
_PRICE_LEVEL_TO_INT = {
    "PRICE_LEVEL_FREE": 0,
    "PRICE_LEVEL_INEXPENSIVE": 1,
    "PRICE_LEVEL_MODERATE": 2,
    "PRICE_LEVEL_EXPENSIVE": 3,
    "PRICE_LEVEL_VERY_EXPENSIVE": 4,
}


def _price_level_for_push(value: Any) -> int | None:
    """Coerce a stored price level to the API's integer contract.

    Only the Google enum name maps (the sole producer, brave/clients/places.py,
    persists that string). Anything else — including a bare number, which is
    ambiguous between the legacy 0–4 scale and the off-by-one proto ordinals —
    is dropped to None rather than 422-ing or pushing a wrong tier.
    """
    if value is None:
        return None
    return _PRICE_LEVEL_TO_INT.get(str(value).strip().upper())


# TripAdvisor card category (primaryInfo, PT-BR — "Cachoeiras", "Igrejas e catedrais",
# "Trilhas para caminhada • Cachoeiras"; EN fixtures say "Waterfalls"/"Parks") → the
# AtrativoTipo vocabulary the push `type` already speaks (C6). Keyword match on the
# accent-folded text, first "•" segment that maps wins; a None tipo marks a segment that
# matches but is deliberately not a tipo (a water park is not a parque). Order matters:
# the more specific keyword first.
_CATEGORY_TIPO: tuple[tuple[str, str | None], ...] = (
    ("praia", "praia"), ("beach", "praia"),
    ("cachoeira", "cachoeira"), ("waterfall", "cachoeira"),
    ("museu", "museu"), ("museum", "museu"), ("galeria", "museu"), ("galler", "museu"),
    ("trilha", "trilha"), ("hiking", "trilha"), ("trail", "trilha"),
    ("mirante", "mirante"), ("observat", "mirante"), ("panoramic", "mirante"),
    ("lookout", "mirante"),
    ("aquatic", None), ("diversoes", None), ("water park", None), ("amusement", None),
    ("parque", "parque"), ("jardi", "parque"), ("natureza", "parque"), ("reserva", "parque"),
    ("park", "parque"), ("garden", "parque"), ("nature", "parque"),
    ("igreja", "centro_historico"), ("catedra", "centro_historico"),
    ("religios", "centro_historico"), ("historic", "centro_historico"),
    ("monumento", "centro_historico"), ("arquitet", "centro_historico"),
    ("church", "centro_historico"), ("monument", "centro_historico"),
    ("teatro", "show_cultural"), ("theat", "show_cultural"), ("festiva", "show_cultural"),
    ("centro cultural", "show_cultural"),
    ("pulgas", None),
    ("vinicola", "experiencia_gastronomica"), ("adega", "experiencia_gastronomica"),
    ("destilaria", "experiencia_gastronomica"), ("mercado", "experiencia_gastronomica"),
    ("feira", "experiencia_gastronomica"), ("gastron", "experiencia_gastronomica"),
    ("winer", "experiencia_gastronomica"),
    ("caverna", "esporte_aventura"), ("gruta", "esporte_aventura"),
    ("aventura", "esporte_aventura"), ("mergulho", "esporte_aventura"),
    ("rafting", "esporte_aventura"), ("cave", "esporte_aventura"),
)


def tipo_from_category(category: Any) -> str:
    """Map a raw TripAdvisor card category onto AtrativoTipo; unknown/empty → "outros"."""
    for raw in str(category or "").split("•"):  # split BEFORE folding: "•" is not ASCII
        segment = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode().casefold()
        for keyword, tipo in _CATEGORY_TIPO:
            if keyword in segment:
                if tipo is not None:
                    return tipo
                break  # known non-tipo segment: try the next one
    return "outros"


def promote_to_mar(
    session: Session,
    rio_record: RioRecord,
    *,
    now: datetime | None = None,
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
    against ``now`` (default: the real clock), so the 90-day boundary is pinnable.

    Args:
        session:    SQLAlchemy synchronous Session.
        rio_record: Scored RioRecord (must have routing='mar').

    Returns:
        The MarRecord (existing updated, or newly created), or None when the
        attraction recency backstop routes the record to DLQ instead of promoting.
    """
    # Attraction recency BACKSTOP (Phase F) — attraction only, before any Mar write.
    # Missing or >90-day-old most-recent review → route to DLQ instead of promoting.
    if rio_record.entity_type == "attraction" and not _attraction_review_recent(
        rio_record.normalized or {}, now=now
    ):
        rio_record.routing = "dlq"
        rio_record.dlq_reason = "no_recent_reviews"
        session.flush()
        return None
    return upsert_mar_snapshot(session, rio_record)


# Internal/board-only normalized keys, never part of the Mar canonical: the five reliability
# *_value criteria, recency evidence, the masked WhatsApp contact (Phase F) and its window
# state, the Places/TA backfill markers (C1 google_enriched / ta_backfilled_at / ta_uf,
# google_fetched_at), the copywriter retry counters and the cascade gate verdicts. Leaking
# a counter or a timestamp would supersede (and re-push) an active Mar row for a purely
# internal change.
_NON_CANONICAL_KEYS = frozenset(
    {
        "origem_value", "completude_value", "corroboracao_value", "atualidade_value",
        "validacao_humana_value", "most_recent_review_at", "review_count", "contact",
        "window_open", "last_inbound_at", "google_enriched", "google_fetched_at",
        "ta_backfilled_at", "ta_uf", "descricao_attempts", "descricao_batch_expiries",
        "descricao_gate", "descricao_rascunho", "descricao_groundedness",
        "places_refreshed_at",
    }
)


def upsert_mar_snapshot(session: Session, rio_record: RioRecord) -> MarRecord:
    """Write the Mar snapshot of ``rio_record`` — no gate, no recency backstop.

    promote_to_mar is the gated entry; this is the snapshot half it shares with the outbox
    (publication.publish), which re-derives the canonical of an ALREADY-promoted record so
    a later description / Places / TA-backfill write reaches norteia-api (F48). No-op when
    nothing changed; supersedes (D-03) when it did.
    """
    source_ref = rio_record.canonical_key or str(rio_record.id)
    normalized = rio_record.normalized or {}
    entity_type = rio_record.entity_type

    canonical: dict[str, Any] = {
        k: v for k, v in normalized.items() if k not in _NON_CANONICAL_KEYS
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
            # push_hash describes what norteia-api holds for this source_ref, not the row:
            # carried over, a supersession whose payload did not move is not re-POSTed.
            push_hash=existing.push_hash,
            # Same for a Brave retraction: the API copy is still hidden until a push says
            # otherwise, so publish() must still send visibility=published (F47).
            retracted_at=existing.retracted_at,
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
    The Mar row stays active: the outbox (publication.publish) sees a Mar row whose
    Rio left 'mar' and retracts it from norteia-api (visibility=hidden) — F47.

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

    # source_ref format "source:UF:id" → "mtur:BA:123" — but NOT every lane: TripAdvisor
    # refs are "tripadvisor:attraction:<id>", whose [1] read as a UF made the destino
    # fallback "ibge:attraction:<ibge>", a ghost Destination per município on the API
    # (F43). The Rio row's own UF wins; the ref's [1] only counts when it IS a UF.
    source_ref = mar_record.source_ref or ""
    parts = source_ref.split(":")
    source = parts[0] if parts and parts[0] else "unknown"
    ref_uf = parts[1] if len(parts) >= 3 and len(parts[1]) == 2 and parts[1].isalpha() else ""
    uf = (getattr(rio_record, "uf", None) or ref_uf).upper()

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
            # Mapa do Turismo Brasileiro — stamped by ensure_destino from the seeded
            # `municipios` table. Sent as a hard bool (never null): the API column is
            # `boolean default false`, so a null would be a lie about a município we
            # have data for, and `False` is exactly what "not in the Mapa" means.
            "participates_mtur": bool(canonical.get("participates_mtur")),
        }

    # attraction
    municipio_ibge = canonical.get("municipio_id") or canonical.get("ibge_code")
    contacts = canonical.get("contacts") or {}
    signal = canonical.get("signal") or {}
    # Split the Places phone: a BR celular → whatsapp, anything else → telefone.
    wa_from_places, telefone_from_places = _split_phone_for_push(canonical.get("phone"))
    # Parent destino link (destino-first). Fall back to the canonical IBGE ref so
    # the API can resolve-or-create even when a lane didn't stamp parent_source_ref
    # (the TA bulk lane). No UF or no município → no fallback at all: a half-built ref
    # is exactly the ghost destino of F43.
    parent_ref = canonical.get("parent_source_ref") or (
        f"ibge:{uf}:{municipio_ibge}" if uf and municipio_ibge else None
    )
    destino_name = canonical.get("municipio") or canonical.get("municipio_nome") or ""
    weekday_text = canonical.get("weekday_text") or signal.get("weekday_text")

    payload: dict[str, Any] = {
        "source_ref": source_ref,
        "source": source,
        "name": canonical.get("name") or canonical.get("nome") or "",
        # Lane taxonomy `tipo` first (Places/discovery), else the TripAdvisor card
        # category (C6, raw in canonical["category"]) mapped onto the same vocabulary.
        "type": canonical.get("tipo") or canonical.get("type")
        or tipo_from_category(canonical.get("category")),
        "municipio_ibge": municipio_ibge,
        "description": canonical.get("descricao_editorial"),
        "latitude": canonical.get("lat"),
        "longitude": canonical.get("lon"),
        "address": canonical.get("address"),
        # D3: the Places-enrichment lane writes website/instagram at the TOP LEVEL of
        # normalized (no "contacts" sub-dict); only the retiring contact_finder lane
        # nests them under "contacts". Read top-level first, fall back to contacts, so
        # Places-enriched atrativos (the standard path) don't silently drop their site.
        "instagram": canonical.get("instagram") or contacts.get("ig_handle"),
        # Phone split (product rule): a contact_finder-supplied phone_e164 (WhatsApp
        # lane) wins for whatsapp; otherwise a Places celular fills whatsapp and any
        # non-celular Places number fills telefone.
        "whatsapp": contacts.get("phone_e164") or wa_from_places,
        "telefone": telefone_from_places,
        "website": canonical.get("website") or contacts.get("website"),
        "reliability_score": reliability,
        "provenance": flat_provenance,
        # No `visibility`: the API keeps its own moderation state (a community hide) on a
        # re-push. publish() adds hidden/published only when Brave retracts / re-shows (F47).
    }

    # Omitted rather than sent half-built: the API requires destino.source_ref and a
    # non-empty destino.tourist_name, so an empty one 422s the whole push. Without the
    # block the API resolves the existing "ibge:<UF>:<ibge>" destination itself.
    if parent_ref and destino_name:
        payload["destino"] = {
            "source_ref": parent_ref,
            "source": parent_ref.split(":")[0],
            "tourist_name": destino_name,
            "municipio_ibge": municipio_ibge,
        }

    # Google Places block, None keys dropped and the block omitted when empty (F93): the
    # API upserts attraction_place_details with enriched_at=now() for ANY non-empty
    # `place`, so all-null keys stamped never-enriched atrativos as enriched — and a
    # re-push carrying null wiped a place_id the API already had.
    place = {
        "place_id": canonical.get("google_place_id")
        or canonical.get("place_id_cache")
        or canonical.get("place_id"),
        "business_status": signal.get("business_status"),
        "opening_hours": weekday_text,
        "price_level": _price_level_for_push(canonical.get("price_level")),
        "reviews_recent_count": signal.get("reviews_recent_count"),
        "distrito_code": canonical.get("distrito_code"),
        "distrito_name": canonical.get("distrito_name"),
        "distrito_municipio_ibge": canonical.get("distrito_municipio_ibge"),
        "subdistrito_name": canonical.get("subdistrito_name"),
        "subdistrito_code": canonical.get("subdistrito_code"),
        "distrito_source": canonical.get("distrito_source"),
    }
    # Exception: once the API holds a copy (push_hash), a key Brave deliberately CLEARED —
    # present in the canonical as None (a TA-geo move off a homonym, a NotFound place_id) —
    # is sent as null: omitted, the API kept the wrong place's id/hours/status/distrito.
    cleared: set[str] = set()
    if getattr(mar_record, "push_hash", None):
        gone = {k for k, v in canonical.items() if v is None}
        sources = {
            "place_id": {"google_place_id", "place_id_cache", "place_id"},
            "business_status": {"signal"},
            "reviews_recent_count": {"signal"},
            "opening_hours": {"weekday_text", "signal"},
        }
        cleared = {k for k in place if sources.get(k, {k}) & gone}
    place = {k: v for k, v in place.items() if v is not None or k in cleared}
    if place:
        payload["place"] = place

    # Language-neutral day→hours map for attractions.opening_hours (the raw Google
    # lines keep riding untouched in place.opening_hours, which the Pact freezes).
    # OMITTED rather than sent as null when unparseable: the API rule is `sometimes`,
    # so an absent key leaves an existing/curated value alone, whereas an explicit
    # null would wipe it on every re-push.
    hours_map = to_hours_map(weekday_text)
    if hours_map:
        payload["opening_hours"] = hours_map
    elif "opening_hours" in cleared:
        payload["opening_hours"] = {}  # the cleared hours overwrite the API's map

    return payload
