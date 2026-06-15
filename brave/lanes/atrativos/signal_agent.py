"""SignalAgent — gathers operating signals and triggers §7.6 scoring.

Sub-state transition: contacts_found → signals_gathered.

D-05: Hard descarte path fires FIRST, before any §7.6 scoring:
  - business_status CLOSED_PERMANENTLY or CLOSED_TEMPORARILY:
    → rio.routing = "descarte", rio.sub_state = None, rio.dlq_reason = "closed_place"
    → write audit row, flush, return (no scoring)

D-05: Apify IG scraping is best-effort and non-blocking:
  - Any ApifyClientProtocol exception → ig_data = {} (degrades signal, never fails record)

Score inputs set on normalized:
  - atualidade_value: 100 if review ≤ 30 days, 50 if 1–6 months, 0 if no recent reviews
  - weekday_text: stored for completude scoring
  - corroboracao_value: 40 if Apify returns IG data confirming activity, else 0

After gathering signals, calls route_by_score to apply §7.6 and route to mar/dlq/descarte.
If routing == "dlq" (borderline <85%), sets sub_state = "aguardando_consulta_whatsapp"
(human WhatsApp gate, D-06).

D-18 boundary: no imports from brave.lanes.destinos or brave.tasks.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from brave.config.settings import ScoreConfig
from brave.core.rio.routing import route_by_score
from brave.lanes.atrativos.schemas import SignalResult
from brave.observability.audit import write_audit

if TYPE_CHECKING:
    from brave.clients.base import ApifyClientProtocol, PlacesClientProtocol
    from brave.core.models import RioRecord

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Business status constants (D-05)
# ---------------------------------------------------------------------------

CLOSED_STATUSES = frozenset({"CLOSED_PERMANENTLY", "CLOSED_TEMPORARILY"})


# ---------------------------------------------------------------------------
# Atualidade helper (D-05)
# ---------------------------------------------------------------------------


def _compute_atualidade(reviews: list[dict[str, Any]], reference_date: datetime | None = None) -> float:
    """Compute atualidade_value from Google Places reviews[].publishTime.

    D-05 deterministic mapping:
      ≤ 30 days  → 100 (funcionando — active)
      ≤ 180 days → 50  (1–6 months ago — possibly active)
      > 180 days → 0   (stale or no recent reviews)

    Args:
        reviews:        List of review dicts from Places API.
        reference_date: UTC datetime to compare against (defaults to now; injectable for tests).

    Returns:
        Float atualidade_value (0.0, 50.0, or 100.0).
    """
    if not reviews:
        return 0.0

    if reference_date is None:
        reference_date = datetime.now(timezone.utc)

    best_value = 0.0

    for review in reviews:
        publish_time_raw = review.get("publishTime") or review.get("publish_time")
        if not publish_time_raw:
            continue

        try:
            # Parse ISO 8601 timestamp
            if isinstance(publish_time_raw, str):
                # Handle both "2026-06-01T12:00:00Z" and "2026-06-01T12:00:00+00:00"
                publish_time_raw = publish_time_raw.replace("Z", "+00:00")
                publish_dt = datetime.fromisoformat(publish_time_raw)
            else:
                continue

            # Ensure timezone-aware
            if publish_dt.tzinfo is None:
                publish_dt = publish_dt.replace(tzinfo=timezone.utc)

            age = reference_date - publish_dt

            if age <= timedelta(days=30):
                return 100.0  # Recent review — immediately return highest value
            elif age <= timedelta(days=180):
                best_value = max(best_value, 50.0)

        except (ValueError, TypeError):
            continue

    return best_value


# ---------------------------------------------------------------------------
# SignalAgent
# ---------------------------------------------------------------------------


class SignalAgent:
    """SignalAgent — gathers Places + Apify signals and triggers §7.6 scoring.

    Advances sub_state from "contacts_found" to "signals_gathered".
    Idempotency guard: returns immediately if sub_state != "contacts_found".

    Hard descarte check (D-05): fires BEFORE any scoring.
    business_status CLOSED_PERMANENTLY or CLOSED_TEMPORARILY → descarte + return.

    Apify is best-effort (D-05): any exception → ig_data = {} (degrades corroboração
    signal but never fails the record).

    After signals: calls route_by_score. If routing == "dlq" (borderline), sets
    sub_state = "aguardando_consulta_whatsapp" (human gate, D-06).

    D-18 boundary: no imports from brave.lanes.destinos.

    Args:
        places_client: PlacesClientProtocol implementation (real or fake).
        apify_client:  ApifyClientProtocol implementation (real or fake).
        session:       SQLAlchemy synchronous Session.
        config:        ScoreConfig with §7.6 weights (optional; defaults to ScoreConfig()).
    """

    def __init__(
        self,
        places_client: "PlacesClientProtocol",
        apify_client: "ApifyClientProtocol",
        session: Session,
        config: ScoreConfig | None = None,
    ) -> None:
        self._places_client = places_client
        self._apify_client = apify_client
        self._session = session
        self._config = config or ScoreConfig()

    async def run(self, rio: "RioRecord") -> None:
        """Gather signals for an atrativo and advance to signals_gathered.

        Idempotency guard: returns immediately if sub_state != "contacts_found".

        Pipeline:
          1. Fetch place_details by place_id_cache
          2. HARD DESCARTE CHECK: CLOSED_* → descarte + return
          3. Compute atualidade_value from reviews
          4. Best-effort Apify IG scrape (never raise)
          5. Compute corroboracao_value from Apify data
          6. Mutate normalized with flag_modified, advance sub_state
          7. Call route_by_score for §7.6 scoring
          8. If routing == "dlq" → set sub_state = "aguardando_consulta_whatsapp"

        Args:
            rio: RioRecord with sub_state="contacts_found".
        """
        # Idempotency guard (D-01)
        if rio.sub_state != "contacts_found":
            return

        normalized = rio.normalized or {}
        place_id: str = normalized.get("place_id_cache", "")
        if not place_id:
            logger.warning("signal_agent_no_place_id", rio_id=str(rio.id))
            return

        # Step 1: Fetch place details
        details = await self._places_client.place_details(place_id)
        business_status: str = details.get("business_status", "UNKNOWN")

        # Step 2: HARD DESCARTE CHECK — before any §7.6 scoring (D-05)
        if business_status in CLOSED_STATUSES:
            rio.routing = "descarte"
            rio.dlq_reason = "closed_place"
            rio.sub_state = None

            write_audit(
                session=self._session,
                action="hard_descarte",
                entity_type="attraction",
                record_id=rio.id if isinstance(rio.id, uuid.UUID) else None,
                before_state={"sub_state": "contacts_found"},
                after_state={"routing": "descarte", "sub_state": None, "reason": "closed_place"},
                actor="signal_agent",
            )
            self._session.flush()

            logger.info(
                "atrativo_hard_descarte",
                rio_id=str(rio.id),
                business_status=business_status,
            )
            return

        # Step 3: Compute atualidade_value from reviews (D-05)
        reviews: list[dict[str, Any]] = details.get("reviews", [])
        atualidade_value = _compute_atualidade(reviews)

        # Compute reviews_recent_count (for SignalResult)
        ref_date = datetime.now(timezone.utc)
        recent_count = sum(
            1 for r in reviews
            if _is_recent_review(r, ref_date)
        )

        # Step 4: Best-effort Apify IG scrape (never raises, D-05)
        contacts = normalized.get("contacts", {})
        ig_handle: str = contacts.get("ig_handle", "") if isinstance(contacts, dict) else ""

        ig_data: dict[str, Any] = {}
        if ig_handle:
            try:
                ig_data = await self._apify_client.scrape_ig(ig_handle)
            except Exception as exc:
                # Graceful degradation — degrade corroboração, never fail record (D-05)
                logger.warning(
                    "apify_scrape_failed",
                    rio_id=str(rio.id),
                    ig_handle=ig_handle,
                    error=str(exc),
                )
                ig_data = {}

        # Step 5: Compute corroboracao_value from Apify data
        # Apify confirms activity → 40; no Apify data → 0
        corroboracao_value = _compute_corroboracao(ig_data)

        # Step 6: Mutate normalized with flag_modified (T-02-06-04 lesson)
        weekday_text: list[str] = details.get("weekday_text", [])

        signal = SignalResult(
            business_status=business_status,
            weekday_text=weekday_text,
            atualidade_value=atualidade_value,
            reviews_recent_count=recent_count,
        )

        new_normalized = dict(normalized)
        new_normalized["signal"] = signal.model_dump()
        new_normalized["atualidade_value"] = atualidade_value
        new_normalized["corroboracao_value"] = corroboracao_value
        new_normalized["weekday_text"] = weekday_text
        if ig_data:
            new_normalized["ig_data"] = ig_data

        rio.normalized = new_normalized
        flag_modified(rio, "normalized")
        rio.sub_state = "signals_gathered"

        # Write audit before scoring
        write_audit(
            session=self._session,
            action="sub_state_advanced",
            entity_type="attraction",
            record_id=rio.id if isinstance(rio.id, uuid.UUID) else None,
            before_state={"sub_state": "contacts_found"},
            after_state={"sub_state": "signals_gathered", "atualidade_value": atualidade_value},
            actor="signal_agent",
        )
        self._session.flush()

        # Step 7: Apply §7.6 scoring (route_by_score mutates routing in-place)
        route_by_score(self._session, rio, self._config)
        self._session.flush()

        # Step 8: Borderline DLQ → await human WhatsApp gate (D-06)
        if rio.routing == "dlq":
            rio.sub_state = "aguardando_consulta_whatsapp"
            write_audit(
                session=self._session,
                action="sub_state_advanced",
                entity_type="attraction",
                record_id=rio.id if isinstance(rio.id, uuid.UUID) else None,
                before_state={"sub_state": "signals_gathered"},
                after_state={"sub_state": "aguardando_consulta_whatsapp"},
                actor="signal_agent",
            )
            self._session.flush()

        logger.info(
            "signals_gathered",
            rio_id=str(rio.id),
            routing=rio.routing,
            sub_state=rio.sub_state,
            atualidade_value=atualidade_value,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _compute_corroboracao(ig_data: dict[str, Any]) -> float:
    """Compute corroboracao_value from Apify IG data.

    A genuinely active IG presence (real followers OR a real post signal) →
    40.0 (partial corroboration). An empty dict, or a found-but-inactive/
    error-shaped dict (0 followers, no posts), scores 0.0.

    WR-08: the previous implementation returned 40.0 for ANY non-empty dict via
    an `or len(ig_data) > 0` catch-all, making the has_followers/has_posts checks
    dead — an inactive or error-shaped profile still scored full corroboração.
    It also read "post_count" while the apify client writes "posts_count", so
    that branch never fired. Both are fixed here.

    Args:
        ig_data: Dict from ApifyClientProtocol.scrape_ig(), may be empty.

    Returns:
        Float corroboracao_value (0.0 or 40.0).
    """
    if not ig_data:
        return 0.0

    # Real activity signals only — no catch-all on dict non-emptiness.
    has_followers = int(ig_data.get("followers", 0) or 0) > 0
    has_posts = bool(ig_data.get("last_post")) or int(ig_data.get("posts_count", 0) or 0) > 0

    if has_followers or has_posts:
        return 40.0

    return 0.0


def _is_recent_review(review: dict[str, Any], reference_date: datetime) -> bool:
    """Check if a review was published within the last 30 days.

    Args:
        review:         Review dict from Places API.
        reference_date: UTC datetime to compare against.

    Returns:
        True if the review is within 30 days of reference_date.
    """
    publish_time_raw = review.get("publishTime") or review.get("publish_time")
    if not publish_time_raw:
        return False

    try:
        if isinstance(publish_time_raw, str):
            publish_time_raw = publish_time_raw.replace("Z", "+00:00")
            publish_dt = datetime.fromisoformat(publish_time_raw)
        else:
            return False

        if publish_dt.tzinfo is None:
            publish_dt = publish_dt.replace(tzinfo=timezone.utc)

        return (reference_date - publish_dt) <= timedelta(days=30)

    except (ValueError, TypeError):
        return False
