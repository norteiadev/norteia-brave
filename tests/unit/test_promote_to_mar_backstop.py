"""Unit tests for the Phase F attraction recency BACKSTOP in promote_to_mar.

100% offline: the backstop-fires path short-circuits before any repo/DB access; the
promote path patches the module-level _mar_repo so no Postgres is required.

Rule (DECIDED DEFAULT): an ATTRACTION whose most-recent review is missing or older
than 90 days must NOT reach Mar — promote_to_mar routes it to DLQ
(dlq_reason='no_recent_reviews') and returns None. Destinos (no reviews) are UNAFFECTED.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import brave.core.mar.service as ms
from brave.core.mar.service import _attraction_review_recent, promote_to_mar

# ---------------------------------------------------------------------------
# Pure helper: _attraction_review_recent (injectable 'now' → deterministic)
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 7, 2, tzinfo=UTC)


def test_recent_review_within_90d_is_recent() -> None:
    norm = {"most_recent_review_at": (_NOW - timedelta(days=30)).isoformat()}
    assert _attraction_review_recent(norm, now=_NOW) is True


def test_review_exactly_90d_is_recent_inclusive() -> None:
    """90 days is the inclusive boundary — still recent (only > 90 fails)."""
    norm = {"most_recent_review_at": (_NOW - timedelta(days=90)).isoformat()}
    assert _attraction_review_recent(norm, now=_NOW) is True


def test_review_over_90d_is_stale() -> None:
    norm = {"most_recent_review_at": (_NOW - timedelta(days=91)).isoformat()}
    assert _attraction_review_recent(norm, now=_NOW) is False


def test_missing_review_date_is_not_recent() -> None:
    assert _attraction_review_recent({}, now=_NOW) is False


def test_none_review_date_is_not_recent() -> None:
    assert _attraction_review_recent({"most_recent_review_at": None}, now=_NOW) is False


def test_unparseable_review_date_is_not_recent() -> None:
    assert _attraction_review_recent({"most_recent_review_at": "not-a-date"}, now=_NOW) is False


def test_zulu_suffix_review_date_parses() -> None:
    norm = {"most_recent_review_at": "2026-06-20T12:00:00Z"}
    assert _attraction_review_recent(norm, now=_NOW) is True


# ---------------------------------------------------------------------------
# promote_to_mar: backstop FIRES (attraction, missing / stale review)
# ---------------------------------------------------------------------------


def _rio(entity_type: str, normalized: dict, *, score: float = 90.0) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        nascente_id=uuid.uuid4(),
        canonical_key="places:BA:ChIJtest",
        entity_type=entity_type,
        normalized=normalized,
        score=score,
        score_version="v1.0",
        score_breakdown={
            "origem": 30.0, "completude": 20.0, "corroboracao": 0.0,
            "atualidade": 15.0, "validacao_humana": 15.0,
        },
        routing="mar",
        dlq_reason=None,
    )


def test_promote_attraction_missing_review_routes_to_dlq() -> None:
    """Attraction with NO most_recent_review_at → DLQ, returns None (no Mar write)."""
    session = MagicMock()
    rio = _rio("attraction", {"name": "Praia X"})  # no most_recent_review_at

    result = promote_to_mar(session, rio)

    assert result is None
    assert rio.routing == "dlq"
    assert rio.dlq_reason == "no_recent_reviews"


def test_promote_attraction_stale_review_routes_to_dlq() -> None:
    """Attraction whose newest review is > 90 days old → DLQ, returns None."""
    session = MagicMock()
    stale = (datetime.now(UTC) - timedelta(days=200)).isoformat()
    rio = _rio("attraction", {"name": "Praia X", "most_recent_review_at": stale})

    result = promote_to_mar(session, rio)

    assert result is None
    assert rio.routing == "dlq"
    assert rio.dlq_reason == "no_recent_reviews"


# ---------------------------------------------------------------------------
# promote_to_mar: backstop does NOT fire (recent attraction / any destino)
# ---------------------------------------------------------------------------


def test_promote_attraction_recent_review_is_promoted() -> None:
    """Attraction with a review within 90 days promotes normally (returns MarRecord)."""
    session = MagicMock()
    recent = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    rio = _rio("attraction", {"name": "Praia X", "most_recent_review_at": recent})

    with patch.object(ms, "_mar_repo") as repo:
        repo.get_active_by_source_ref.return_value = None  # first-time creation
        result = promote_to_mar(session, rio)

    assert result is not None
    assert result.entity_type == "attraction"
    assert rio.routing == "mar"  # NOT flipped to dlq
    repo.add.assert_called_once()


def test_promote_destination_without_reviews_is_unaffected() -> None:
    """Destinos have no reviews — the backstop must NEVER touch them (entity_type guard)."""
    session = MagicMock()
    rio = _rio("destination", {"name": "Trancoso"})  # no most_recent_review_at

    with patch.object(ms, "_mar_repo") as repo:
        repo.get_active_by_source_ref.return_value = None
        result = promote_to_mar(session, rio)

    assert result is not None
    assert result.entity_type == "destination"
    assert rio.routing == "mar"
    assert rio.dlq_reason is None


def test_promote_excludes_internal_keys_from_canonical() -> None:
    """most_recent_review_at + contact + the Places-enrichment bookkeeping keys are
    internal/board-only → excluded from the Mar canonical push shape (Pact
    byte-identical guardrail). descricao_attempts especially: it is a retry counter
    that ticks 0→3 on copywriter failures, and leaking it into the canonical would
    supersede an active Mar row (and re-push it) for a purely internal change."""
    session = MagicMock()
    recent = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    rio = _rio(
        "attraction",
        {
            "name": "Praia X",
            "most_recent_review_at": recent,
            "contact": {"whatsapp_candidate": "+5573*****01"},
            "google_enriched": True,
            "descricao_attempts": 1,
            "atualidade_value": 100.0,
        },
    )

    with patch.object(ms, "_mar_repo") as repo:
        repo.get_active_by_source_ref.return_value = None
        result = promote_to_mar(session, rio)

    assert result is not None
    assert "most_recent_review_at" not in result.canonical
    assert "contact" not in result.canonical
    assert "google_enriched" not in result.canonical
    assert "descricao_attempts" not in result.canonical
    assert "atualidade_value" not in result.canonical  # reliability criterion still excluded
    assert result.canonical["name"] == "Praia X"


# ---------------------------------------------------------------------------
# End-to-end: TA-shaped Nascente payload → Rio → steward promote → Mar
#
# The gap this closes: the TA lane fetched a real review date, scored atualidade
# from it, then dropped it — so a steward-promoted atrativo bounced right back to
# DLQ with no_recent_reviews. Exercises the whole loop, not just the helper.
# ---------------------------------------------------------------------------


def _ta_payload(**extra) -> dict:
    """TA atrativo scoring ~70 (< threshold_mar 80) — steward promote is the only path."""
    return {
        "name": "Praia do Espelho",
        "uf": "BA",
        "lat": -16.4,
        "lng": -39.0,
        "origem_value": 70.0,
        "completude_value": 90.0,
        "corroboracao_value": 80.0,
        "atualidade_value": 100.0,
        "validacao_humana_value": 0.0,
        **extra,
    }


def _rio_from_nascente(payload: dict):
    import brave.core.rio.routing as routing
    from brave.config.settings import ScoreConfig
    from brave.core.models import NascenteRecord
    from brave.core.rio.routing import process_nascente_record

    nascente = NascenteRecord(
        id=uuid.uuid4(),
        source="tripadvisor",
        source_ref="tripadvisor:attraction:12345",
        entity_type="attraction",
        uf="BA",
        payload=payload,
        content_hash="deadbeef",
    )
    repo = MagicMock()
    repo.get_by_canonical_key.return_value = None
    with patch.object(routing, "_rio_repo", repo), patch.object(
        routing, "find_duplicate", return_value=None
    ), patch.object(routing, "record_event"):
        return process_nascente_record(MagicMock(), nascente, ScoreConfig())


def _steward_promote(rio):
    """validate_and_promote_rio offline: repo.get returns our transient Rio."""
    import brave.core.rio.routing as routing
    from brave.config.settings import ScoreConfig
    from brave.core.dlq.service import validate_and_promote_rio

    repo = MagicMock()
    repo.get.return_value = rio
    with patch.object(routing, "_rio_repo", repo), patch.object(
        routing, "record_event"
    ), patch.object(ms, "_mar_repo") as mar_repo:
        mar_repo.get_active_by_source_ref.return_value = None
        return validate_and_promote_rio(MagicMock(), rio, ScoreConfig())


def test_ta_attraction_with_fresh_review_date_reaches_mar() -> None:
    recent = (datetime.now(UTC) - timedelta(days=10)).isoformat()
    rio = _rio_from_nascente(_ta_payload(most_recent_review_at=recent))

    # The date the lane fetched survived the Rio cherry-pick (the fix).
    assert rio.normalized["most_recent_review_at"] == recent

    mar = _steward_promote(rio)

    assert mar is not None
    assert rio.routing == "mar"
    assert rio.dlq_reason != "no_recent_reviews"
    # Internal recency evidence never crosses the wire (Pact-frozen push shape).
    assert "most_recent_review_at" not in mar.canonical


def test_ta_attraction_without_review_date_still_dlqs() -> None:
    """Fail-closed guarantee: no timestamp → DLQ even after a steward promote."""
    rio = _rio_from_nascente(_ta_payload())

    mar = _steward_promote(rio)

    assert mar is None
    assert rio.routing == "dlq"
    assert rio.dlq_reason == "no_recent_reviews"
