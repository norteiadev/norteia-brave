"""SignalAgent — gathers operating signals and triggers reliability scoring.

Sub-state transition: contacts_found → signals_gathered.

D-05: Hard descarte path fires FIRST, before any reliability scoring:
  - business_status CLOSED_PERMANENTLY:
    → rio.routing = "descarte", rio.sub_state = None, rio.dlq_reason = "closed_place"
    → write audit row, flush, return (no scoring)
  - business_status CLOSED_TEMPORARILY (operator decision 2026-09-18: the place still
    exists, a steward decides): → rio.routing = "dlq", rio.sub_state = None,
    rio.dlq_reason = "closed_temporarily" — the Painel shows a "Fechado Temporariamente"
    badge. The signals delta (below) is persisted first; audit row, flush, return (no
    scoring).

Every non-descarte exit (temporarily closed, no recent reviews, scored) persists the same
signals delta from the already-paid place_details call: signal, atualidade_value,
corroboracao_value, weekday_text, address (when present) and most_recent_review_at.

Score inputs set on normalized:
  - atualidade_value: 100 if review ≤ 30 days, 50 if 1–6 months, 0 if no recent reviews
  - weekday_text: stored for completude scoring
  - corroboracao_value: fixed at 0.0. The social-signal (Apify IG) corroboration
    source was retired (Phase E); no Places field feeds corroboração today, so the
    lane writes a deterministic 0.0. This matches the prior offline (Null) behaviour
    exactly and keeps the reliability score input present (no default drift in routing).

No-recent-reviews rule (Phase F), attraction only, BEFORE any reliability scoring — the
SAME rule as the promote_to_mar backstop (brave.core.mar.service._attraction_review_recent),
read over the merged recency view (the later of the existing normalized date and the newest
Google review; review_count from normalized only, i.e. the TripAdvisor count):
  - newest review older than 90 days (365 when review_count >= 20), or none at all:
    → rio.routing = "dlq", rio.dlq_reason = "no_recent_reviews", rio.sub_state = None
    → terminal DLQ; does NOT enter the WhatsApp gate (the gate is manual now).
  The check uses an injectable reference clock (SignalAgent(now=...)) so it is fully
  deterministic offline.

After gathering signals, calls route_by_score to apply reliability scoring and route to mar/dlq/descarte.
For a NON-stale borderline record (routing == "dlq" via score), sets
sub_state = "aguardando_consulta_whatsapp" (human WhatsApp gate, D-06).

D-18 boundary: no imports from other domains or brave.tasks.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy.orm import Session

from brave.config.settings import ScoreConfig
from brave.core.mar.service import _attraction_review_recent
from brave.core.rio.persist import persist_normalized
from brave.core.rio.routing import route_by_score
from brave.domains.places.schemas import SignalResult
from brave.observability.audit import write_audit

if TYPE_CHECKING:
    from brave.clients.base import PlacesClientProtocol
    from brave.core.models import RioRecord

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Business status constants (D-05)
# ---------------------------------------------------------------------------

# Hard descarte. CLOSED_TEMPORARILY is NOT here: it parks in the DLQ (see below).
CLOSED_STATUSES = frozenset({"CLOSED_PERMANENTLY"})
TEMPORARILY_CLOSED = "CLOSED_TEMPORARILY"
TEMPORARILY_CLOSED_REASON = "closed_temporarily"


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
    business_status CLOSED_PERMANENTLY → descarte + return; CLOSED_TEMPORARILY → DLQ
    (dlq_reason "closed_temporarily") + return.

    Corroboração (Phase E): the social-signal (Apify IG) source was retired, so
    corroboracao_value is written as a deterministic 0.0 (no Places field feeds it
    today). This preserves the prior offline behaviour and keeps the score input
    present so reliability routing does not silently drift.

    After signals: calls route_by_score. If routing == "dlq" (borderline), sets
    sub_state = "aguardando_consulta_whatsapp" (human gate, D-06).

    D-18 boundary: no imports from other domains.

    Args:
        places_client: PlacesClientProtocol implementation (real or fake).
        session:       SQLAlchemy synchronous Session.
        config:        ScoreConfig with reliability weights — the effective one
                       (load_effective_config(session).score), never a silent default.
    """

    def __init__(
        self,
        places_client: PlacesClientProtocol,
        session: Session,
        config: ScoreConfig,
        now: datetime | None = None,
    ) -> None:
        self._places_client = places_client
        self._session = session
        self._config = config
        # Injectable reference clock (Phase F). None → resolved to
        # datetime.now(timezone.utc) at run() time. Pinning it makes the atualidade
        # buckets AND the no-recent-reviews rule fully deterministic offline.
        self._now = now

    async def run(self, rio: RioRecord) -> None:
        """Gather signals for an atrativo and advance to signals_gathered.

        Idempotency guard: returns immediately if sub_state != "contacts_found".

        Pipeline:
          1. Fetch place_details by place_id_cache
          2. HARD DESCARTE CHECK: CLOSED_PERMANENTLY → descarte + return (nothing persisted)
          3. Compute atualidade_value (max of existing and Google reviews)
          4. Set corroboracao_value to the deterministic 0.0 constant (Apify retired)
          5. Persist the signals delta (incl. most_recent_review_at) — on every path below
          2b. CLOSED_TEMPORARILY → DLQ (dlq_reason="closed_temporarily") + return
          2.5 NO-RECENT-REVIEWS RULE (attraction): the Mar backstop rule fails on the
              merged recency view → terminal DLQ (dlq_reason="no_recent_reviews",
              sub_state=None) + return. Does NOT enter the WhatsApp gate.
          6. Advance sub_state, call route_by_score for reliability scoring
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
        # no-recent-reviews rule, and reviews_recent_count all share one
        # deterministic 'now' (injectable via SignalAgent(now=...) for offline tests).
        ref_date = self._now or datetime.now(UTC)

        reviews: list[dict[str, Any]] = details.get("reviews", [])
        recency = _merged_recency(normalized, reviews)

        # Step 3: atualidade_value from Google reviews (D-05), never below the existing
        # value (e.g. TripAdvisor's) — same max-merge as PlacesEnrichmentAgent.
        atualidade_value = max(
            float(normalized.get("atualidade_value") or 0.0),
            _compute_atualidade(reviews, ref_date),
        )

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

        # Step 5: build the signals delta (derived scalars only — no review text/author).
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
        # Places formatted_address → the flat `address` push field. Guarded so a Places
        # blank never wipes the address already in normalized (e.g. from the nascente
        # payload). Brave-side only: build_push_payload always emits "address" as a literal
        # key, so a still-empty value is pushed as null and overwrites a curated API value —
        # a pre-existing trait shared with description/instagram/whatsapp/telefone/website,
        # not fixed here.
        address = details.get("formatted_address")
        if address:
            new_normalized["address"] = address
        # Phase F: persist the merged most-recent review timestamp so the promote_to_mar
        # recency BACKSTOP re-checks the same rule at promotion time. It is excluded from the
        # Mar `canonical` payload in promote_to_mar so the norteia-api push shape stays
        # byte-identical (Pact).
        new_normalized.update(recency)

        # Persist on EVERY non-descarte path (temporarily closed, no recent reviews, scored),
        # BEFORE the gates: the place_details call is already paid, and dropping its hours /
        # address / status on a DLQ exit makes the record look "sem horário" (WhatsApp
        # eligibility) and leaves the steward's card empty. Same as PlacesEnrichmentAgent,
        # which persists before parking a temporarily-closed place.
        # Merge the delta onto the CURRENT column under a row lock instead of writing this
        # pre-place_details snapshot back whole — otherwise anything committed during the
        # Places call (a paid batched descricao_editorial + its completude, whose batch stamp
        # is already cleared) is erased and gets paid for again. See persist_normalized.
        persist_normalized(self._session, rio, normalized, new_normalized)

        # Step 2b: temporarily closed → steward's call, not a descarte (no scoring).
        if business_status == TEMPORARILY_CLOSED:
            rio.routing = "dlq"
            rio.dlq_reason = TEMPORARILY_CLOSED_REASON
            rio.sub_state = None
            write_audit(
                session=self._session,
                action="temporarily_closed",
                entity_type="attraction",
                record_id=rio.id if isinstance(rio.id, uuid.UUID) else None,
                before_state={"sub_state": "contacts_found"},
                after_state={
                    "routing": "dlq",
                    "sub_state": None,
                    "reason": TEMPORARILY_CLOSED_REASON,
                },
                actor="signal_agent",
            )
            self._session.flush()
            logger.info("atrativo_temporarily_closed", rio_id=str(rio.id))
            return

        # Step 2.5: NO-RECENT-REVIEWS RULE (Phase F) — attraction only, BEFORE scoring.
        # One rule with the promote_to_mar backstop (_attraction_review_recent), read over
        # the merged view: a fresh TripAdvisor date passes even with zero Google reviews,
        # and an established place (review_count >= 20) gets the 365-day window. Failing it
        # → terminal DLQ (dlq_reason="no_recent_reviews"). This deliberately does NOT enter
        # the WhatsApp gate: the record lands in the plain DLQ (sub_state=None) for a
        # manual, operator-driven move (Phase F makes the gate manual). The CLOSED
        # hard-descarte path above still fires first; corroboração stays the Phase E
        # 0.0 constant on the scored (non-stale) path below.
        # Reads the pre-call snapshot + this run's recency (NOT the refreshed column), so a
        # concurrent writer cannot change this run's routing outcome.
        if rio.entity_type == "attraction" and not _attraction_review_recent(
            {**normalized, **recency}, now=ref_date
        ):
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

        # Step 6: Apply reliability scoring (route_by_score mutates routing in-place). The fresh
        # business_status above re-decided temporarily-closed; other verdicts stay sticky (C5).
        route_by_score(self._session, rio, self._config, redecided={TEMPORARILY_CLOSED_REASON})
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


def _merged_recency(
    normalized: dict[str, Any],
    reviews: list[dict[str, Any]],
) -> dict[str, Any]:
    """The recency keys to write onto normalized: only what changes, never a None.

    most_recent_review_at = the later of the existing value (e.g. TripAdvisor's) and the
    newest Google review — never regresses. review_count is left alone: the backstop's
    established window (review_count >= 20) is calibrated on TripAdvisor review counts,
    and Google's userRatingCount (star-only ratings included) is not comparable.
    """
    out: dict[str, Any] = {}
    existing = normalized.get("most_recent_review_at")
    existing_dt = _parse_publish_time({"publishTime": existing})
    newest = max(
        (d for d in (existing_dt, _newest_review_dt(reviews)) if d is not None),
        default=None,
    )
    if newest is not None and newest != existing_dt:
        out["most_recent_review_at"] = newest.isoformat()
    return out


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
