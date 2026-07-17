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
from brave.lanes.atrativos.signal_agent import (
    CLOSED_STATUSES,
    _compute_atualidade,
    _newest_review_dt,
)
from brave.observability.audit import write_audit
from brave.observability.record_events import record_event

if TYPE_CHECKING:
    from brave.clients.base import PlacesClientProtocol
    from brave.core.models import RioRecord

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
    """Enriches a TA atrativo with Google Places opening hours + review liveness.

    Advances sub_state from "description_enriched" to "places_enriched".
    Idempotency guard: returns immediately if sub_state != "description_enriched".
    Cross-lane guard: a record that already carries place_id_cache AND weekday_text
    was enriched by the Places-FSM SignalAgent — leave it untouched (no-op).

    Args:
        places_client:   PlacesClientProtocol implementation (real/null/fake).
        session:         SQLAlchemy synchronous Session.
        config:          ScoreConfig with reliability weights (for the re-score).
        now:             Injectable reference clock (atualidade / recency). None → now.
        max_distance_km: Text-Search match radius in km (AppConfig.places_match_max_distance_km).
    """

    def __init__(
        self,
        places_client: PlacesClientProtocol,
        session: Session,
        config: ScoreConfig | None = None,
        now: datetime | None = None,
        max_distance_km: float = 20.0,
    ) -> None:
        self._places_client = places_client
        self._session = session
        self._config = config or ScoreConfig()
        self._now = now
        self._max_distance_km = max_distance_km

    async def run(self, rio: RioRecord) -> None:
        """Enrich one atrativo with Google Places signals and advance to places_enriched.

        Pipeline:
          1. Idempotency guard (sub_state == "description_enriched") + Places-FSM skip.
          2. Resolve place_id: use place_id_cache if present, else Text Search + match.
             No confident match → keep the TA floor (no write), advance + re-score.
          3. place_details → business_status CLOSED_* (confident match) → descarte + return.
          4. weekday_text (hours) + atualidade = max(TA, Google) + most_recent_review_at.
          5. flag_modified, advance sub_state, re-score (route_by_score), dlq bounce.
        """
        # Step 1: idempotency + cross-lane guard.
        if rio.sub_state != "description_enriched":
            return
        normalized = rio.normalized or {}
        if normalized.get("place_id_cache") and normalized.get("weekday_text"):
            # Already enriched by the Places-FSM SignalAgent — not a TA-lane record.
            return

        nome: str = normalized.get("name") or ""
        uf: str = rio.uf or normalized.get("uf") or ""
        lat = normalized.get("lat")
        lng = normalized.get("lon")  # routing.normalize stores longitude under "lon"

        new_normalized = dict(normalized)
        hours_written = False
        ref_date = self._now or datetime.now(UTC)

        # Step 2+3: resolve place_id, fetch details. ANY external failure degrades to the
        # TA floor — a Places defect can never strand the record short of places_enriched.
        details: dict[str, Any] = {}
        place_id: str = normalized.get("place_id_cache") or ""
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
                rio.routing = "descarte"
                rio.dlq_reason = "closed_place"
                rio.sub_state = None
                write_audit(
                    session=self._session,
                    action="hard_descarte",
                    entity_type="attraction",
                    record_id=rio.id if isinstance(rio.id, uuid.UUID) else None,
                    before_state={"sub_state": "description_enriched"},
                    after_state={"routing": "descarte", "sub_state": None, "reason": "closed_place"},
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
        else:
            logger.info("places_enrich_kept_floor", rio_id=str(rio.id), uf=uf)

        # Step 5: mutate normalized + advance sub_state.
        rio.normalized = new_normalized
        flag_modified(rio, "normalized")
        rio.sub_state = "places_enriched"

        write_audit(
            session=self._session,
            action="sub_state_advanced",
            entity_type="attraction",
            record_id=rio.id if isinstance(rio.id, uuid.UUID) else None,
            before_state={"sub_state": "description_enriched"},
            after_state={
                "sub_state": "places_enriched",
                "weekday_text_set": hours_written,
                "atualidade_value": new_normalized.get("atualidade_value"),
            },
            actor="places_enrichment_agent",
        )
        self._session.flush()

        # Re-score: atualidade may have changed → borderline record can move mar↔dlq.
        route_by_score(self._session, rio, self._config)
        self._session.flush()

        # dlq bounce — mirror the SignalAgent/Description post-score convention.
        if rio.routing == "dlq":
            rio.sub_state = None
            write_audit(
                session=self._session,
                action="sub_state_advanced",
                entity_type="attraction",
                record_id=rio.id if isinstance(rio.id, uuid.UUID) else None,
                before_state={"sub_state": "places_enriched"},
                after_state={"sub_state": None, "routing": "dlq"},
                actor="places_enrichment_agent",
            )
            self._session.flush()

        # Append-only Log-tab timeline event (keyed by canonical_key — the drawer key).
        # LGPD: public-geo / engineering fields only — never review text.
        canonical_key = rio.canonical_key or ""
        record_event(
            session=self._session,
            source=canonical_key.split(":", 1)[0] if canonical_key else "unknown",
            source_ref=canonical_key,
            stage="places_enriched",
            status="ok" if hours_written else "skip",
            entity_type="attraction",
            uf=rio.uf,
            rio_id=rio.id if isinstance(rio.id, uuid.UUID) else None,
            data={
                "hours_written": hours_written,
                "atualidade_value": new_normalized.get("atualidade_value"),
                "routing": rio.routing,
            },
        )
        self._session.flush()

        logger.info(
            "places_enriched",
            rio_id=str(rio.id),
            routing=rio.routing,
            sub_state=rio.sub_state,
            hours_written=hours_written,
        )
