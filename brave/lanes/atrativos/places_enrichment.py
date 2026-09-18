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

from brave.clients.places import _normalize_name
from brave.config.settings import ScoreConfig
from brave.core.models import AtrativoBusca, Municipio
from brave.core.rio.persist import persist_normalized
from brave.core.rio.routing import route_by_score
from brave.lanes.atrativos.copywriter import CASCADE_MODEL, CascadeResult, TourismCopywriter
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

# Google's own marker for an administrative / geographic entity (município, bairro,
# sublocalidade). Such a place carries NONE of the fields this agent came for — no hours,
# no reviews, no business_status — yet it clears both the name and the distance guard with
# room to spare, because it sits at the atrativo's own coordinates and shares its name.
#
# Measured (docs/poc/places-extra-fields-spike.auto.raw.json, 15 atrativos): the 3 broken
# matches — Centro Histórico de Paraty (sublocality_level_1), Convento da Penha
# (neighborhood), Praia dos Carneiros (locality) — ALL carried "political"; the 12 good ones
# carried none. Separation 15/15, so a single marker does the whole job.
#
# WHY "political" and not a list of the three types seen: "political" is the taxonomy's own
# class for these, so it also covers geographic types we have not sampled yet.
#
# WHY not the inverse rule ("require establishment"), which separates the same 15/15: it
# fails CLOSED. If Google ever stops emitting the legacy "establishment" type, EVERY atrativo
# silently stops being enriched. "political" fails OPEN — a taxonomy change degrades to
# today's behaviour instead of to zero.
#
# NOT rejected: "beach" / "natural_feature". Praia de Camburi resolved to
# ["beach","natural_feature","establishment"] and DID return an editorialSummary — a natural
# feature is a legitimate atrativo, only the administrative entity is not.
_GEOGRAPHIC_TYPE_MARKER: str = "political"

# Match radius around the município SEAT for a record with no coords of its own. Wide
# because big rural municípios put real atrativos far from the seat (Chapada falls sit
# 60+ km from São João d'Aliança's) and border parks sit in the next município (Terra
# Ronca, ~40 km); the same-name mismatches it must reject were all 190+ km away.
_SEAT_RADIUS_KM: float = 80.0

# PT-BR wording of Places' business_status for the atrativo's Log tab.
_CLOSED_LABELS: dict[str, str] = {
    "CLOSED_PERMANENTLY": "fechado permanentemente",
    "CLOSED_TEMPORARILY": "fechado temporariamente",
}


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


def _best_match(
    results: list[dict[str, Any]],
    target_name: str,
    target_lat: float | None,
    target_lng: float | None,
    max_distance_km: float,
) -> dict[str, Any] | None:
    """Pick the Text Search result that confidently IS the target atrativo.

    Three guards, all of which a candidate must clear:
      - it must not be a geographic entity (``_GEOGRAPHIC_TYPE_MARKER`` in ``types``);
      - name token_set_ratio ≥ _NAME_MATCH_THRESHOLD;
      - when both target and candidate carry coordinates, within max_distance_km
        (rejects same-name places in other cities).

    Among passers the highest name score wins. Returns None when nothing passes → caller
    keeps the TA floor.

    The geographic guard runs FIRST and per-candidate (not as a post-hoc rejection of the
    winner) so a lower-scoring real POI in the same result set can still win — the
    município usually scores 100 on the name.
    """
    folded_target = _normalize_name(target_name)
    best: dict[str, Any] | None = None
    best_score = -1.0
    have_target_coords = target_lat is not None and target_lng is not None
    for r in results:
        if _GEOGRAPHIC_TYPE_MARKER in (r.get("types") or []):
            continue  # município/bairro/sublocalidade — carries none of the fields we want
        name = r.get("name") or ""
        score = fuzz.token_set_ratio(folded_target, _normalize_name(name))
        if score < _NAME_MATCH_THRESHOLD:
            continue
        # No target coords → no distance guard, and a name alone matched a church in Vitória
        # for one in Pirenópolis, and the river "Rio Preto" for "Cachoeira Saltos do Rio
        # Preto" (token_set_ratio scores a contained name 100). Then only a candidate Places
        # puts in a município of the target's UF qualifies (the client resolves
        # municipio_ibge within the UF, "" otherwise).
        if not have_target_coords and r.get("municipio_ibge") == "":
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
        search_client:      Parallel client → the copywriter runs in CASCADE mode (cascade_model,
                            mention + groundedness gates) instead of Sonnet + web_search.
                            atrativo_description_cascade_enabled. None → web_search mode.
        cascade_model:      Writer slug in cascade mode (atrativo_cascade_model).
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
        search_client: Any = None,
        cascade_model: str = CASCADE_MODEL,
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
                llm_client,
                model=cascade_model if search_client is not None else voice_model_slug,
                enable_web_search=enable_web_search,
                search_client=search_client,
            )
            if llm_client is not None
            else None
        )

    async def locate(self, nome: str, uf: str) -> dict[str, Any] | None:
        """Find WHERE an atrativo is when no other source could place it in a município.

        One Text Search, the same confident-match guards as run() (no coords to compare,
        so name + not-a-geographic-entity), and only results Places placed in a município
        of THIS UF (``municipio_ibge`` resolves within the UF only). Returns the matched
        result — place_id, location, municipio_ibge — or None. Never raises: a Places
        failure just means the card stays unmatched.

        No confident match, but every in-UF result sits in the SAME município → returns
        only ``{"municipio_ibge", "consensus": True}``: the card is placed in the município
        the search clusters in, WITHOUT a place_id (none of the results is provably this
        atrativo, so run() keeps its own strict match). Measured on the 7 Chapada cards
        the name guard rejected: all 7 clustered correctly (e.g. "Jardim de Maytreia" vs
        "Mirante Jardim de Maytrea", score 83.7). Disagreeing results → None.
        """
        try:
            results = await self._places_client.text_search(nome, uf)
        except Exception:  # noqa: BLE001 — a Places defect never breaks the ingest
            logger.warning("places_locate_failed", uf=uf)
            return None
        in_uf = [
            r
            for r in results
            if r.get("municipio_ibge") and _GEOGRAPHIC_TYPE_MARKER not in (r.get("types") or [])
        ]
        match = _best_match(in_uf, nome, None, None, self._max_distance_km)
        if match is not None:
            return match
        municipios = {r["municipio_ibge"] for r in in_uf}
        if len(municipios) == 1:
            return {"municipio_ibge": municipios.pop(), "consensus": True}
        return None

    def wants_description(self, rio: RioRecord) -> bool:
        """The description sub-step's gate. Reads ``rio`` only — no I/O, no writes."""
        normalized = rio.normalized or {}
        return bool(
            self._description_enabled
            and self._copywriter is not None
            and not normalized.get("descricao_editorial")
            and bool(normalized.get("name"))
            and rio.routing != "descarte"
            and int(normalized.get("descricao_attempts") or 0) < _MAX_DESCRIPTION_ATTEMPTS
            # A live batch already holds a PAID request for this record's description.
            # Turning atrativo_description_batch_enabled OFF (the operator action that flag
            # exists to support) re-enables THIS inline copywriter within one sweep, and
            # nothing else here can see the in-flight request: descricao_editorial is still
            # absent and descricao_attempts is still 0. Without this guard the flip bills a
            # second full-price Sonnet+web_search call for prose Anthropic is already
            # producing, and collect overwrites the inline one an hour later. The stamp is
            # cleared in the same transaction as the batched writes, so it un-blocks itself.
            and not rio.descricao_batch_id
        )

    async def write_description(
        self, nome: str, municipio: str, uf: str, details: dict[str, Any]
    ) -> tuple[str | None, CascadeResult | None, bool]:
        """The copywriter's network I/O: (prose, cascade, no_spend). Never touches the Session.

        Split out of run() so brave.describe_uf can gather it for a whole chunk and hand
        each result back through ``run(rio, description=...)``.
        """
        assert self._copywriter is not None
        try:
            if self._copywriter.cascade:
                cascade = await self._copywriter.write_cascade(
                    nome, municipio, uf, places_context=details
                )
                return cascade.prose, cascade, False
            prose = await self._copywriter.write(nome, municipio, uf, places_context=details)
            return prose, None, False
        except CostGuardError:
            # The daily budget tripped BEFORE dispatch: no token spent, so no attempt
            # happened. Burning the budget here would let one budget trip per sweep
            # exclude the WHOLE backlog from descriptions after 3 sweeps.
            return None, None, True

    async def run(
        self,
        rio: RioRecord,
        description: tuple[str | None, CascadeResult | None, bool] | None = None,
    ) -> None:
        """Enrich one atrativo with Google Places signals (hours + review liveness).

        ``description`` is an already-fetched write_description() result; None → fetch here.

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
        wants_description = self.wants_description(rio)
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
                    ref_lat, ref_lng, radius = lat, lng, self._max_distance_km
                    if (lat is None or lng is None) and municipio_ibge:
                        # No coords of its own: measure from the município seat instead,
                        # wider (see _SEAT_RADIUS_KM) — otherwise a same-name place anywhere
                        # in the UF wins (Luziânia's Igreja N. S. do Rosário took the one
                        # in Flores de Goiás, ~190 km away).
                        seat = self._session.get(Municipio, municipio_ibge)
                        if isinstance(seat, Municipio):
                            ref_lat, ref_lng = seat.lat, seat.lng
                            radius = max(radius, _SEAT_RADIUS_KM)
                    match = _best_match(results, nome, ref_lat, ref_lng, radius)
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
                persist_normalized(self._session, rio, normalized, new_normalized)
                routing_before = rio.routing
                rio.routing = "descarte"
                rio.dlq_reason = "closed_place"
                cause = {
                    "reason": "closed_place",
                    "business_status": business_status,
                    "place_id": place_id,
                    "place_name": details.get("name") or None,
                    "place_address": details.get("formatted_address") or None,
                }
                write_audit(
                    session=self._session,
                    action="places_hard_descarte",
                    entity_type="attraction",
                    record_id=rio.id if isinstance(rio.id, uuid.UUID) else None,
                    before_state={"routing": routing_before},
                    after_state={"routing": "descarte", **cause},
                    actor="places_enrichment_agent",
                )
                # The atrativo's Log tab must say WHY it left the pipeline — the audit row
                # alone is invisible there. Public business data only (Places listing).
                canonical_key = rio.canonical_key or ""
                record_event(
                    session=self._session,
                    source=canonical_key.split(":", 1)[0] if canonical_key else "unknown",
                    source_ref=canonical_key,
                    stage="places_descarte",
                    status="fail",
                    message=(
                        f"Google Places marca como {_CLOSED_LABELS.get(business_status, business_status)}"
                        + (f" ({cause['place_name']})" if cause["place_name"] else "")
                    ),
                    entity_type="attraction",
                    uf=rio.uf,
                    rio_id=rio.id if isinstance(rio.id, uuid.UUID) else None,
                    data={**cause, "routing_before": routing_before},
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
        cascade: CascadeResult | None = None
        if wants_description and self._copywriter is not None:
            if description is None:
                description = await self.write_description(nome, municipio, uf, details)
            prose, cascade, no_spend = description
            if no_spend:
                logger.warning(
                    "copywriter_cost_guard_no_attempt", rio_id=str(rio.id), attempts=attempts
                )
            if cascade is not None and cascade.busca is not None:
                # Every paid search is kept whole, whatever the verdict — descriptions
                # get regenerated later with another model from these rows (§29).
                b = cascade.busca
                self._session.add(
                    AtrativoBusca(
                        canonical_key=rio.canonical_key or "",
                        nome=nome,
                        municipio=municipio or None,
                        uf=uf or None,
                        provider="parallel",
                        mode=b.mode,
                        objective=b.objective,
                        queries=b.queries,
                        search_id=b.search_id,
                        results=b.results,
                        usage=b.usage,
                        warnings=b.warnings,
                        usd_cost=b.usd,
                        latency_ms=b.latency_ms,
                    )
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
            if cascade is not None:
                # Gate verdicts are board-only (excluded from the Mar canonical). A gated pass
                # burns an attempt like any spend without prose — the search was paid — so a
                # record the index does not cover stops re-searching after
                # _MAX_DESCRIPTION_ATTEMPTS, and a later pass can still succeed if coverage
                # improves. Success clears a stale verdict from an earlier attempt.
                new_normalized["descricao_gate"] = cascade.motivo
                new_normalized["descricao_rascunho"] = cascade.rascunho
                new_normalized["descricao_groundedness"] = cascade.groundedness

        # Step 5: mark enriched (idempotency), mutate normalized, re-score. sub_state is
        # left untouched — a dlq record stays in the plain DLQ (sub_state=None) queue.
        new_normalized["google_enriched"] = True
        # Merge, never overwrite — see persist_normalized. From here on the merged value is
        # what the audit row, the timeline event and the re-score must read.
        new_normalized = persist_normalized(self._session, rio, normalized, new_normalized)

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
        # Ungrounded prose goes to the human queue, not to Mar: the draft sits in
        # descricao_rascunho for a steward, and the reason overrides the score one so the DLQ
        # says what to look at. The invariant does not depend on this routing — the draft is
        # never written to descricao_editorial, so no re-score can carry it to Mar.
        if cascade is not None and cascade.motivo == "nao_fundamentada":
            rio.routing = "dlq"
            rio.dlq_reason = "descricao_nao_fundamentada"
        # The search never names the record's município: a steward must fix the record (a
        # Nascente homonym geocode), not the description — so it leaves Mar's path too.
        if cascade is not None and cascade.motivo == "municipio_nao_confirmado":
            rio.routing = "dlq"
            rio.dlq_reason = "municipio_nao_confirmado"
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
                "descricao_gate": cascade.motivo if cascade is not None else None,
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
