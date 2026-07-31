"""PlacesEnrichmentAgent — enriches a TA atrativo with Google Places signals.

Sub-state transition: description_enriched → places_enriched.

The TripAdvisor lane never enters the Places FSM chain (discovery → contacts →
signals), so its atrativos never get Google ``weekday_text`` (opening hours) nor a
Google ``business_status`` / review-recency liveness signal. This agent — the TA-lane
counterpart of ``DescriptionEnrichmentAgent`` — resolves the atrativo to a Google
place_id (Places Text Search), fetches Place Details, and persists:

  - ``weekday_text``       : opening hours → flows Rio→Mar→push (norteia-api gains hours).
  - ``atualidade_value``   : max(existing TA recency, Google review recency) — a recent
                             Google review BOOSTS the score, never lowers it.
  - ``most_recent_review_at``: the most-recent review date across TA + Google — lets the
                             promote_to_mar 90-day recency backstop pass (a recent Google
                             review confirms the place is operating → eligible for Mar).
  - ``place_id_cache``     : the resolved place_id, so a later 90-day refresh sweep skips
                             Text Search (only the Place Details SKU is re-spent).

Liveness posture (operator decision): a recent Google review is a POSITIVE boost; its
ABSENCE does NOT route to DLQ (the TA signals are kept). The one hard rule is
``business_status`` CLOSED_* → descarte, and only on a confident match (we only fetch
Place Details for a place we matched by name + proximity).

Graceful degradation (mirrors DescriptionEnrichmentAgent): no confident match, empty
Text Search, or ANY external failure keeps the TA floor (no Google keys written), still
advances sub_state + re-scores — a scraper/API defect can never strand the record.

D-18 boundary: no imports from brave.lanes.destinos or brave.tasks.
"""

from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog
from rapidfuzz import fuzz
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from brave.clients.places import _normalize_name
from brave.config.settings import ScoreConfig
from brave.core.rio.routing import route_by_score
from brave.lanes.atrativos.copywriter import TourismCopywriter
from brave.lanes.atrativos.schemas import SignalResult
from brave.lanes.atrativos.signal_agent import (
    CLOSED_STATUSES,
    _compute_atualidade,
    _is_recent_review,
    _newest_review_dt,
)
from brave.observability.audit import write_audit
from brave.observability.record_events import record_event
from brave.shared.exceptions import CostGuardError
from brave.shared.ibge_distritos import resolve_distrito

if TYPE_CHECKING:
    from brave.clients.base import LLMClientProtocol, PlacesClientProtocol
    from brave.core.models import RioRecord
    from brave.shared.ibge_distritos import IbgeDistrito

# completude ceiling once a descricao_editorial is written (mirrors the old
# DescriptionEnrichmentAgent degrau: 75 floor → 90 with description).
_COMPLETUDE_WITH_DESCRIPTION: float = 90.0

# How many times the copywriter may be re-attempted on a record that never got prose.
# WHY a bounded count and not a boolean: TourismCopywriter.write swallows every failure and
# returns None, so "description absent" alone re-arms the backfill pass forever (a provider
# outage or cost-guard trip would re-spend an LLM call + re-score on EVERY sweep, for every
# already-enriched atrativo). A permanent "failed once, never again" marker re-creates the
# opposite bug — a transient outage would block backfill for good. A small bounded count is
# the only shape that is both. Operators clear ``descricao_attempts`` to re-arm.
# NOT counted: a CostGuardError (raised before dispatch, zero spend) — see run().
_MAX_DESCRIPTION_ATTEMPTS: int = 3

logger = structlog.get_logger(__name__)

# rapidfuzz token_set_ratio cutoff for a Text Search result name vs the atrativo name.
# Below this the candidate is rejected — never write a wrong-place's hours/reviews onto a
# canonical record (mirrors DescriptionEnrichmentAgent's município guard posture).
_NAME_MATCH_THRESHOLD: int = 85


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km (pure math).

    Inlined (not imported from brave.domains.tripadvisor.ibge) to keep this
    brave.lanes.atrativos agent free of a cross-package import for ~8 lines of math.
    """
    r = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _parse_iso(raw: Any) -> datetime | None:
    """Parse an ISO-8601 string into a UTC-aware datetime, or None."""
    if not raw or not isinstance(raw, str):
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def _persist_normalized(
    session: Session, rio: RioRecord, before: dict[str, Any], after: dict[str, Any]
) -> dict[str, Any]:
    """Merge ONLY the keys this run changed onto the row's CURRENT ``normalized``. Returns it.

    Lost update, and it predates the batch lane: ``run`` reads ``normalized``, snapshots it,
    then spends seconds in Places network I/O before writing the whole JSONB column back from
    that pre-I/O snapshot — silently erasing whatever ANY other writer committed in that
    window (workers run in parallel; prefetch 1 is not concurrency 1).

    The concrete case is the batched-description lane: collect commits ``descricao_editorial``
    + the lifted ``completude_value`` mid-flight, this agent's stale dict wipes both, and since
    apply_result already cleared ``descricao_batch_id`` the record is instantly eligible again
    and the next tick PAYS for the same description a second time.

    Re-reading under FOR UPDATE and writing a delta (not a snapshot) fixes it for every
    concurrent writer, not just that one.
    """
    delta = {k: v for k, v in after.items() if k not in before or before[k] != v}
    session.refresh(rio, ["normalized"], with_for_update=True)
    merged = {**(rio.normalized or {}), **delta}
    rio.normalized = merged
    flag_modified(rio, "normalized")
    return merged


def _best_match(
    results: list[dict[str, Any]],
    target_name: str,
    target_lat: float | None,
    target_lng: float | None,
    max_distance_km: float,
) -> dict[str, Any] | None:
    """Pick the Text Search result that confidently IS the target atrativo.

    Requires a name token_set_ratio ≥ _NAME_MATCH_THRESHOLD. When both the target and
    the candidate carry coordinates, the candidate must also be within max_distance_km
    (rejects same-name places in other cities). Among passers, the highest name score
    wins. Returns None when nothing passes → caller keeps the TA floor.
    """
    folded_target = _normalize_name(target_name)
    best: dict[str, Any] | None = None
    best_score = -1.0
    have_target_coords = target_lat is not None and target_lng is not None
    for r in results:
        name = r.get("name") or ""
        score = fuzz.token_set_ratio(folded_target, _normalize_name(name))
        if score < _NAME_MATCH_THRESHOLD:
            continue
        loc = r.get("location") or {}
        rlat, rlng = loc.get("lat"), loc.get("lng")
        if (
            have_target_coords
            and rlat is not None
            and rlng is not None
            and _haversine_km(target_lat, target_lng, rlat, rlng) > max_distance_km
        ):
            continue  # right name, wrong place (different city)
        if score > best_score:
            best_score = score
            best = r
    return best


class PlacesEnrichmentAgent:
    """The single atrativo enrichment agent: description + distrito + hours/contact/price +
    review liveness, all off one Google Places ``place_details`` call.

    Advances sub_state (None | "signals_gathered") → "places_enriched". Serves both the TA
    inline path (sub_state None, dispatched by sweep_tripadvisor) and the Places-FSM discovery
    path (sub_state "signals_gathered"). Cross-lane guard: a record that already carries
    place_id_cache AND weekday_text was enriched by the Places-FSM SignalAgent — its PAID
    Places sub-step is skipped, but the description sub-step still runs (this agent is the
    ONLY writer of descricao_editorial, so that lane would otherwise never get one).

    Description: written by TourismCopywriter (Places editorialSummary + web_search, Norteia
    voice) when ``description_enabled`` and the record has no descricao_editorial yet. Gated
    separately from the Places call so an operator can disable the LLM/web-search spend while
    still getting hours/distrito/liveness. Distrito comes from Places addressComponents
    (distrito_hint → resolve_distrito), replacing the old MD-breadcrumb resolver.

    Args:
        places_client:      PlacesClientProtocol implementation (real/null/fake).
        session:            SQLAlchemy synchronous Session.
        config:             ScoreConfig with reliability weights (for the re-score).
        llm_client:         LLMClientProtocol for the copywriter. None → no description.
        distritos:          IBGE DTB distrito reference (resolve_distrito). None → distrito no-op.
        voice_model_slug:   Anthropic slug for the copywriter (a Sonnet slug).
        description_enabled: Gate the copywriter sub-step (description_enrichment_enabled).
        enable_web_search:   Offer the web_search tool to the copywriter (real sweeps only).
        now:                Injectable reference clock (atualidade / recency). None → now.
        max_distance_km:    Text-Search match radius in km (places_match_max_distance_km).
    """

    def __init__(
        self,
        places_client: PlacesClientProtocol,
        session: Session,
        config: ScoreConfig | None = None,
        llm_client: LLMClientProtocol | None = None,
        distritos: list[IbgeDistrito] | None = None,
        voice_model_slug: str = "claude-sonnet-4-5",
        description_enabled: bool = True,
        enable_web_search: bool = True,
        now: datetime | None = None,
        max_distance_km: float = 20.0,
    ) -> None:
        self._places_client = places_client
        self._session = session
        self._config = config or ScoreConfig()
        self._llm_client = llm_client
        self._distritos = distritos or []
        self._description_enabled = description_enabled
        self._now = now
        self._max_distance_km = max_distance_km
        self._copywriter = (
            TourismCopywriter(
                llm_client, model=voice_model_slug, enable_web_search=enable_web_search
            )
            if llm_client is not None
            else None
        )

    async def run(self, rio: RioRecord) -> None:
        """Enrich one atrativo with Google Places signals (hours + review liveness).

        Runs for a TA atrativo REGARDLESS of routing — a dlq'd record (TA scores
        ~55 < 80 and only reaches Mar via steward validation) still gets Google hours,
        coords, and google_place_id, all valuable the moment a steward validates it to
        Mar. Idempotency is keyed on the ``google_enriched`` normalized marker, NOT
        sub_state (the description step dlq-bounces sub_state to None, so a sub_state
        gate would never fire). This step does NOT participate in the sub_state FSM.
        ``google_enriched`` means "the PAID Places sub-step has run for this record" —
        NOT "everything is done": the description sub-step is re-attempted on later
        passes while descricao_editorial is missing, for at most
        _MAX_DESCRIPTION_ATTEMPTS tries (``descricao_attempts``).

        Pipeline:
          1. Skip the PAID Places sub-step when already done (marker / cross-lane);
             return only when there is also no description left to backfill.
          2. Resolve place_id: use place_id_cache if present, else Text Search + match.
          3. place_details → business_status CLOSED_* (confident match) → descarte.
          4. weekday_text (hours) + Google coords + atualidade=max(TA,Google) +
             most_recent_review_at + place_id_cache/google_place_id.
          5. Mark google_enriched, flag_modified, re-score (route_by_score).
        """
        # Step 1: the PAID Places sub-step is one-shot — either this agent already ran
        # (google_enriched marker) or the Places-FSM SignalAgent already spent the
        # Details SKU (cross-lane: place_id_cache + weekday_text). The DESCRIPTION
        # sub-step is NOT one-shot: a record whose copywriter pass failed or was
        # disabled is backfilled on a later pass at ZERO Places spend (details stays
        # empty → the copywriter grounds on web_search alone). wants_description
        # mirrors the description sub-step's own condition below.
        normalized = rio.normalized or {}
        places_done = bool(
            normalized.get("google_enriched")
            or (normalized.get("place_id_cache") and normalized.get("weekday_text"))
        )
        attempts = int(normalized.get("descricao_attempts") or 0)
        wants_description = (
            self._description_enabled
            and self._copywriter is not None
            and not normalized.get("descricao_editorial")
            and bool(normalized.get("name"))
            and rio.routing != "descarte"
            and attempts < _MAX_DESCRIPTION_ATTEMPTS
        )
        # Exhausted budget is SILENT otherwise (the record just stops getting descriptions,
        # forever, until an operator clears the counter in JSONB). Log it on every pass so
        # "descriptions stopped" is diagnosable from the logs alone.
        if (
            self._description_enabled
            and self._copywriter is not None
            and not normalized.get("descricao_editorial")
            and attempts >= _MAX_DESCRIPTION_ATTEMPTS
        ):
            logger.warning(
                "description_attempts_exhausted",
                rio_id=str(rio.id),
                uf=rio.uf,
                attempts=attempts,
                max_attempts=_MAX_DESCRIPTION_ATTEMPTS,
            )
        if places_done and not wants_description:
            return

        nome: str = normalized.get("name") or ""
        uf: str = rio.uf or normalized.get("uf") or ""
        municipio: str = normalized.get("municipio") or ""
        municipio_ibge: str = normalized.get("municipio_id") or ""
        lat = normalized.get("lat")
        lng = normalized.get("lon")  # routing.normalize stores longitude under "lon"

        new_normalized = dict(normalized)
        hours_written = False
        ref_date = self._now or datetime.now(UTC)

        # Step 2+3: resolve place_id, fetch details. Skipped entirely once places_done —
        # a description-only backfill pass spends NO Places SKU. ANY external failure
        # degrades to the TA floor — a Places defect can never strand the record.
        details: dict[str, Any] = {}
        place_id: str = normalized.get("place_id_cache") or ""
        if not places_done:
            try:
                if not place_id and nome:
                    results = await self._places_client.text_search(nome, uf)
                    match = _best_match(results, nome, lat, lng, self._max_distance_km)
                    if match is not None:
                        place_id = match.get("place_id") or ""
                if place_id:
                    details = await self._places_client.place_details(place_id)
            except Exception:  # noqa: BLE001 — Places failure keeps the TA floor
                logger.warning("places_enrich_failed_kept_floor", rio_id=str(rio.id))
                details = {}

        if details:
            business_status: str = details.get("business_status", "UNKNOWN")

            # Step 3: CLOSED_* on a confident match → hard descarte (mirror SignalAgent).
            if business_status in CLOSED_STATUSES:
                new_normalized["google_enriched"] = True
                _persist_normalized(self._session, rio, normalized, new_normalized)
                rio.routing = "descarte"
                rio.dlq_reason = "closed_place"
                write_audit(
                    session=self._session,
                    action="places_hard_descarte",
                    entity_type="attraction",
                    record_id=rio.id if isinstance(rio.id, uuid.UUID) else None,
                    before_state={"routing": rio.routing},
                    after_state={"routing": "descarte", "reason": "closed_place"},
                    actor="places_enrichment_agent",
                )
                self._session.flush()
                logger.info("places_enrich_hard_descarte", rio_id=str(rio.id))
                return

            # Step 4: opening hours + liveness boost (never lowers the score).
            weekday_text: list[str] = details.get("weekday_text", [])
            reviews: list[dict[str, Any]] = details.get("reviews", [])

            if weekday_text:
                new_normalized["weekday_text"] = weekday_text
                hours_written = True

            # Adopt Google's precise coordinates (more accurate than TA's). normalize
            # stores longitude under "lon"; these flow to canonical → norteia-api push.
            gloc = details.get("location") or {}
            g_lat, g_lng = gloc.get("lat"), gloc.get("lng")
            if g_lat is not None and g_lng is not None:
                new_normalized["lat"] = g_lat
                new_normalized["lon"] = g_lng

            google_atualidade = _compute_atualidade(reviews, ref_date)
            existing_atualidade = float(new_normalized.get("atualidade_value", 0.0))
            new_normalized["atualidade_value"] = max(existing_atualidade, google_atualidade)

            # most_recent_review_at = the later of (existing TA date, newest Google date).
            google_newest = _newest_review_dt(reviews)
            existing_newest = _parse_iso(new_normalized.get("most_recent_review_at"))
            newest = max(
                (d for d in (google_newest, existing_newest) if d is not None),
                default=None,
            )
            if newest is not None:
                new_normalized["most_recent_review_at"] = newest.isoformat()

            # place_id_cache: internal FSM lookup key (refresh finds by id, not text).
            # google_place_id: the same id exposed as a clean platform-facing canonical
            # field → flows to norteia-api (both lanes write it — see routing.normalize).
            new_normalized["place_id_cache"] = place_id
            new_normalized["google_place_id"] = place_id

            # Structured operational fields (discrete keys, NEVER in the description prose).
            phone = details.get("international_phone_number")
            website = details.get("website")
            price_level = details.get("price_level")
            address = details.get("formatted_address")
            if phone:
                new_normalized["phone"] = phone
            if website:
                new_normalized["website"] = website
            if price_level:
                new_normalized["price_level"] = price_level
            if address:
                new_normalized["address"] = address

            # Places-FSM parity: build_push_payload reads business_status and
            # reviews_recent_count off normalized["signal"] (the SignalResult block the
            # SignalAgent writes) — without it the TA lane pushes both as null.
            new_normalized["signal"] = SignalResult(
                business_status=business_status,
                weekday_text=weekday_text,
                atualidade_value=new_normalized["atualidade_value"],
                reviews_recent_count=sum(
                    1 for r in reviews if _is_recent_review(r, ref_date)
                ),
            ).model_dump()

            # Distrito from Places addressComponents (admin_area_level_3 → resolve_distrito),
            # replacing the old MD-breadcrumb resolver. Same six canonical keys the discovery
            # lane writes; all None when there's no hint / no reference table / no match.
            distrito_hint = details.get("distrito_hint")
            match = (
                resolve_distrito(distrito_hint, municipio_ibge, self._distritos)
                if (self._distritos and distrito_hint and municipio_ibge)
                else None
            )
            if match is not None:
                new_normalized["distrito_name"] = match.nome
                new_normalized["distrito_code"] = match.distrito_code
                new_normalized["distrito_municipio_ibge"] = match.ibge_code
                new_normalized["subdistrito_name"] = None
                new_normalized["subdistrito_code"] = None
                new_normalized["distrito_source"] = "places_admin_area_level_3"
        else:
            logger.info("places_enrich_kept_floor", rio_id=str(rio.id), uf=uf)

        # Description (TourismCopywriter): grounded in the Places context + web search, in the
        # Norteia voice. Gated by description_enabled; skipped if a description already exists
        # (idempotent refresh). Runs even without a Places match — web_search can still ground
        # it from name+município+UF. Never raises (copywriter returns None on any failure).
        description_written = bool(new_normalized.get("descricao_editorial"))
        # Same predicate as the Step-1 gate (routing + attempt budget included) — the two
        # conditions MUST agree, so this branch reuses it instead of restating it.
        if wants_description and self._copywriter is not None:
            prose: str | None = None
            no_spend = False
            try:
                prose = await self._copywriter.write(
                    nome, municipio, uf, places_context=details
                )
            except CostGuardError:
                # The daily budget tripped BEFORE dispatch: no token spent, so no attempt
                # happened. Burning the budget here would let one budget trip per sweep
                # exclude the WHOLE backlog from descriptions after 3 sweeps.
                no_spend = True
                logger.warning(
                    "copywriter_cost_guard_no_attempt", rio_id=str(rio.id), attempts=attempts
                )
            if prose:
                new_normalized["descricao_editorial"] = prose
                new_normalized["completude_value"] = max(
                    float(new_normalized.get("completude_value", 0.0)),
                    _COMPLETUDE_WITH_DESCRIPTION,
                )
                description_written = True
            elif not no_spend:
                # Only a real invocation that produced no prose burns budget; success and a
                # pre-dispatch cost-guard block never increment. After
                # _MAX_DESCRIPTION_ATTEMPTS the record stops re-entering the pass (see the
                # constant for why this is a count, not a flag).
                new_normalized["descricao_attempts"] = attempts + 1

        # Step 5: mark enriched (idempotency), mutate normalized, re-score. sub_state is
        # left untouched — a dlq record stays in the plain DLQ (sub_state=None) queue.
        new_normalized["google_enriched"] = True
        # Merge, never overwrite — see _persist_normalized. From here on the merged value is
        # what the audit row, the timeline event and the re-score must read.
        new_normalized = _persist_normalized(self._session, rio, normalized, new_normalized)

        write_audit(
            session=self._session,
            action="places_enriched",
            entity_type="attraction",
            record_id=rio.id if isinstance(rio.id, uuid.UUID) else None,
            before_state={"routing": rio.routing},
            after_state={
                "weekday_text_set": hours_written,
                "descricao_editorial_set": description_written,
                "atualidade_value": new_normalized.get("atualidade_value"),
                "completude_value": new_normalized.get("completude_value"),
            },
            actor="places_enrichment_agent",
        )
        self._session.flush()

        # Re-score: atualidade/completude may have changed → borderline record can move mar↔dlq.
        route_by_score(self._session, rio, self._config)
        self._session.flush()

        # Append-only Log-tab timeline event (keyed by canonical_key — the drawer key).
        # LGPD: public-geo / engineering fields only — never review text.
        canonical_key = rio.canonical_key or ""
        record_event(
            session=self._session,
            source=canonical_key.split(":", 1)[0] if canonical_key else "unknown",
            source_ref=canonical_key,
            stage="places_enriched",
            status="ok" if (hours_written or description_written) else "skip",
            entity_type="attraction",
            uf=rio.uf,
            rio_id=rio.id if isinstance(rio.id, uuid.UUID) else None,
            data={
                "hours_written": hours_written,
                "description_written": description_written,
                "atualidade_value": new_normalized.get("atualidade_value"),
                "routing": rio.routing,
            },
        )
        self._session.flush()

        logger.info(
            "places_enriched",
            rio_id=str(rio.id),
            routing=rio.routing,
            hours_written=hours_written,
            description_written=description_written,
        )
