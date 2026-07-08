"""SignalAgent — gathers operating signals and triggers reliability scoring.

Sub-state transition: contacts_found → signals_gathered.

D-05: Hard descarte path fires FIRST, before any reliability scoring:
  - business_status CLOSED_PERMANENTLY or CLOSED_TEMPORARILY:
    → rio.routing = "descarte", rio.sub_state = None, rio.dlq_reason = "closed_place"
    → write audit row, flush, return (no scoring)

Score inputs set on normalized:
  - atualidade_value: 100 if review ≤ 30 days, 50 if 1–6 months, 0 if no recent reviews
  - weekday_text: stored for completude scoring
  - corroboracao_value: fixed at 0.0. The social-signal (Apify IG) corroboration
    source was retired (Phase E); no Places field feeds corroboração today, so the
    lane writes a deterministic 0.0. This matches the prior offline (Null) behaviour
    exactly and keeps the reliability score input present (no default drift in routing).

No-recent-reviews rule (Phase F), attraction only, BEFORE any reliability scoring:
  - NO reviews OR most-recent review older than 90 days:
    → rio.routing = "dlq", rio.dlq_reason = "no_recent_reviews", rio.sub_state = None
    → terminal DLQ; does NOT enter the WhatsApp gate (the gate is manual now).
  The 90-day check uses an injectable reference clock (SignalAgent(now=...)) so it is
  fully deterministic offline.

After gathering signals, calls route_by_score to apply reliability scoring and route to mar/dlq/descarte.
For a NON-stale borderline record (routing == "dlq" via score), sets
sub_state = "aguardando_consulta_whatsapp" (human WhatsApp gate, D-06).

D-18 boundary: no imports from brave.lanes.destinos or brave.tasks.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from brave.config.settings import ScoreConfig
from brave.core.rio.routing import route_by_score
from brave.domains.mtur.dtos import SignalResult
from brave.observability.audit import write_audit

if TYPE_CHECKING:
    from brave.clients.base import PlacesClientProtocol
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
        reference_date = datetime.now(UTC)

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
                publish_dt = publish_dt.replace(tzinfo=UTC)

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
    """SignalAgent — gathers Places signals and triggers reliability scoring.

    Advances sub_state from "contacts_found" to "signals_gathered".
    Idempotency guard: returns immediately if sub_state != "contacts_found".

    Hard descarte check (D-05): fires BEFORE any scoring.
    business_status CLOSED_PERMANENTLY or CLOSED_TEMPORARILY → descarte + return.

    Corroboração (Phase E): the social-signal (Apify IG) source was retired, so
    corroboracao_value is written as a deterministic 0.0 (no Places field feeds it
    today). This preserves the prior offline behaviour and keeps the score input
    present so reliability routing does not silently drift.

    After signals: calls route_by_score. If routing == "dlq" (borderline), sets
    sub_state = "aguardando_consulta_whatsapp" (human gate, D-06).

    D-18 boundary: no imports from brave.lanes.destinos.

    Args:
        places_client: PlacesClientProtocol implementation (real or fake).
        session:       SQLAlchemy synchronous Session.
        config:        ScoreConfig with reliability weights (optional; defaults to ScoreConfig()).
    """

    def __init__(
        self,
        places_client: PlacesClientProtocol,
        session: Session,
        config: ScoreConfig | None = None,
        now: datetime | None = None,
    ) -> None:
        self._places_client = places_client
        self._session = session
        self._config = config or ScoreConfig()
        # Injectable reference clock (Phase F). None → resolved to
        # datetime.now(timezone.utc) at run() time. Pinning it makes the atualidade
        # buckets AND the 90-day no-recent-reviews rule fully deterministic offline.
        self._now = now

    async def run(self, rio: RioRecord) -> None:
        """Gather signals for an atrativo and advance to signals_gathered.

        Idempotency guard: returns immediately if sub_state != "contacts_found".

        Pipeline:
          1. Fetch place_details by place_id_cache
          2. HARD DESCARTE CHECK: CLOSED_* → descarte + return
          2.5 NO-RECENT-REVIEWS RULE (attraction): no reviews OR newest > 90 days →
              terminal DLQ (dlq_reason="no_recent_reviews", sub_state=None) + return.
              Does NOT enter the WhatsApp gate.
          3. Compute atualidade_value from reviews
          4. Set corroboracao_value to the deterministic 0.0 constant (Apify retired)
          5. Mutate normalized (incl. most_recent_review_at) with flag_modified, advance sub_state
          6. Call route_by_score for reliability scoring
          7. If routing == "dlq" → set sub_state = "aguardando_consulta_whatsapp"

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

        # Step 2: HARD DESCARTE CHECK — before any reliability scoring (D-05)
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

        # Reference clock (Phase F): resolve ONCE so the atualidade buckets, the
        # 90-day no-recent-reviews rule, and reviews_recent_count all share one
        # deterministic 'now' (injectable via SignalAgent(now=...) for offline tests).
        ref_date = self._now or datetime.now(UTC)

        reviews: list[dict[str, Any]] = details.get("reviews", [])

        # Step 2.5: NO-RECENT-REVIEWS RULE (Phase F) — attraction only, BEFORE scoring.
        # No reviews at all, OR the most-recent review older than 90 days → terminal
        # DLQ (dlq_reason="no_recent_reviews"). This deliberately does NOT enter the
        # WhatsApp gate: the record lands in the plain DLQ (sub_state=None) for a
        # manual, operator-driven move (Phase F makes the gate manual). The CLOSED
        # hard-descarte path above still fires first; corroboração stays the Phase E
        # 0.0 constant on the scored (non-stale) path below.
        if rio.entity_type == "attraction" and _reviews_stale_90d(reviews, ref_date):
            rio.routing = "dlq"
            rio.dlq_reason = "no_recent_reviews"
            rio.sub_state = None

            write_audit(
                session=self._session,
                action="no_recent_reviews",
                entity_type="attraction",
                record_id=rio.id if isinstance(rio.id, uuid.UUID) else None,
                before_state={"sub_state": "contacts_found"},
                after_state={"routing": "dlq", "sub_state": None, "reason": "no_recent_reviews"},
                actor="signal_agent",
            )
            self._session.flush()

            logger.info(
                "atrativo_no_recent_reviews",
                rio_id=str(rio.id),
                review_count=len(reviews),
            )
            return

        # Step 3: Compute atualidade_value from reviews (D-05)
        atualidade_value = _compute_atualidade(reviews, ref_date)

        # Compute reviews_recent_count (for SignalResult)
        recent_count = sum(
            1 for r in reviews
            if _is_recent_review(r, ref_date)
        )

        # Step 4: Corroboração — deterministic 0.0 constant (Apify IG source retired,
        # Phase E). No Places field feeds corroboração today; writing an explicit 0.0
        # keeps the reliability score input present and matches the prior offline behaviour
        # (routing does not silently drift on a missing key).
        corroboracao_value = 0.0

        # Step 5: Mutate normalized with flag_modified (T-02-06-04 lesson)
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
        # Phase F: persist the most-recent review timestamp (ISO-8601, UTC) so the
        # promote_to_mar recency BACKSTOP can re-check the 90-day rule at promotion
        # time. Excluded from the Mar `canonical` payload in promote_to_mar so the
        # norteia-api push shape stays byte-identical (Pact).
        newest_dt = _newest_review_dt(reviews)
        new_normalized["most_recent_review_at"] = (
            newest_dt.isoformat() if newest_dt is not None else None
        )

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

        # Step 6: Apply reliability scoring (route_by_score mutates routing in-place)
        route_by_score(self._session, rio, self._config)
        self._session.flush()

        # Step 7 (Phase F, spec 2026-07-02): NÃO auto-gate. A borderline score→dlq
        # attraction now STAYS in DLQ with sub_state=None. The operator moves it to
        # the WhatsApp column manually via the DLQ→WhatsApp batch endpoint
        # (POST /api/v1/dlq/whatsapp-batch), which drives the None→aguardando_consulta_whatsapp
        # FSM edge. Auto-enrollment into aguardando_consulta_whatsapp was removed here.
        if rio.routing == "dlq":
            rio.sub_state = None
            write_audit(
                session=self._session,
                action="sub_state_advanced",
                entity_type="attraction",
                record_id=rio.id if isinstance(rio.id, uuid.UUID) else None,
                before_state={"sub_state": "signals_gathered"},
                after_state={"sub_state": None, "routing": "dlq"},
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


def _parse_publish_time(review: dict[str, Any]) -> datetime | None:
    """Parse a review's publishTime into a UTC-aware datetime, or None.

    Reuses the codebase's deterministic parse: read publishTime/publish_time,
    normalize a trailing 'Z' to '+00:00', datetime.fromisoformat, force UTC when
    naive. Unparseable / missing → None.
    """
    publish_time_raw = review.get("publishTime") or review.get("publish_time")
    if not publish_time_raw or not isinstance(publish_time_raw, str):
        return None
    try:
        publish_dt = datetime.fromisoformat(publish_time_raw.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if publish_dt.tzinfo is None:
        publish_dt = publish_dt.replace(tzinfo=UTC)
    return publish_dt


def _newest_review_dt(reviews: list[dict[str, Any]]) -> datetime | None:
    """Return the most-recent parseable review publishTime, or None."""
    newest: datetime | None = None
    for review in reviews:
        publish_dt = _parse_publish_time(review)
        if publish_dt is None:
            continue
        if newest is None or publish_dt > newest:
            newest = publish_dt
    return newest


def _reviews_stale_90d(reviews: list[dict[str, Any]], reference_date: datetime) -> bool:
    """True when the no-recent-reviews rule fires (Phase F).

    Fires when there are NO reviews, OR none carry a parseable publishTime, OR the
    most-recent review is older than 90 days relative to reference_date. Pure +
    deterministic (reference_date injected) → fully offline-testable.
    """
    if not reviews:
        return True
    newest = _newest_review_dt(reviews)
    if newest is None:
        return True  # reviews present but no usable recency signal → treat as stale
    return (reference_date - newest) > timedelta(days=90)


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
            publish_dt = publish_dt.replace(tzinfo=UTC)

        return (reference_date - publish_dt) <= timedelta(days=30)

    except (ValueError, TypeError):
        return False
