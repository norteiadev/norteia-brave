"""Tests for Rio routing logic (route_by_score, reprocess_record).

All tests run fully offline — no DB, no I/O.
"""

import uuid
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

import brave.core.rio.routing as routing
from brave.config.settings import ScoreConfig
from brave.core.models import NascenteRecord, RioRecord


@pytest.fixture
def score_config():
    return ScoreConfig()


@pytest.fixture
def rio_record():
    """Minimal RioRecord fixture for routing tests."""
    return RioRecord(
        id=uuid.uuid4(),
        nascente_id=uuid.uuid4(),
        entity_type="destination",
        uf="BA",
        normalized={
            "origem_value": 90.0,
            "completude_value": 90.0,
            "corroboracao_value": 90.0,
            "atualidade_value": 90.0,
            "validacao_humana_value": 90.0,
        },
        routing="in_progress",
    )


def test_route_by_score_mar(score_config, rio_record):
    """route_by_score with score=87.0 sets routing='mar'."""
    from brave.core.rio.routing import route_by_score

    rio_record.normalized = {
        "origem_value": 100.0,
        "completude_value": 100.0,
        "corroboracao_value": 100.0,
        "atualidade_value": 100.0,
        "validacao_humana_value": 0.0,
    }
    # 30+20+20+15+0 = 85 exactly → mar
    route_by_score(None, rio_record, score_config)
    assert rio_record.routing == "mar"
    assert rio_record.score == 85.0
    assert rio_record.score_version == score_config.score_version


def test_route_by_score_dlq(score_config, rio_record):
    """route_by_score with score=65.0 sets routing='dlq' and dlq_reason non-empty."""
    from brave.core.rio.routing import route_by_score

    rio_record.normalized = {
        "origem_value": 100.0,
        "completude_value": 80.0,
        "corroboracao_value": 60.0,
        "atualidade_value": 40.0,
        "validacao_humana_value": 0.0,
    }
    # 30+16+12+6+0 = 64.0 → dlq
    route_by_score(None, rio_record, score_config)
    assert rio_record.routing == "dlq"
    assert rio_record.dlq_reason is not None
    assert len(rio_record.dlq_reason) > 0


def test_route_by_score_low_dlq(score_config, rio_record):
    """route_by_score with a low score sets routing='dlq' (binary gate, < 80)."""
    from brave.core.rio.routing import route_by_score

    rio_record.normalized = {
        "origem_value": 40.0,
        "completude_value": 20.0,
        "corroboracao_value": 0.0,
        "atualidade_value": 0.0,
        "validacao_humana_value": 0.0,
    }
    # 12+4+0+0+0 = 16.0 → dlq
    route_by_score(None, rio_record, score_config)
    assert rio_record.routing == "dlq"
    assert rio_record.dlq_reason is not None
    assert len(rio_record.dlq_reason) > 0


def test_route_by_score_boundary_80_is_mar(score_config, rio_record):
    """route_by_score at exactly 80.0 (default threshold_mar) sets routing='mar'."""
    from brave.core.rio.routing import route_by_score

    rio_record.normalized = {
        "origem_value": 100.0,
        "completude_value": 100.0,
        "corroboracao_value": 75.0,
        "atualidade_value": 100.0,
        "validacao_humana_value": 0.0,
    }
    # 30+20+15+15+0 = 80.0 → mar
    route_by_score(None, rio_record, score_config)
    assert rio_record.score == 80.0
    assert rio_record.routing == "mar"


def test_route_by_score_sets_score_version(score_config, rio_record):
    """route_by_score stamps score_version on RioRecord (D-13)."""
    from brave.core.rio.routing import route_by_score

    route_by_score(None, rio_record, score_config)
    assert rio_record.score_version == score_config.score_version


def test_route_by_score_sets_breakdown(score_config, rio_record):
    """route_by_score writes score_breakdown dict to RioRecord."""
    from brave.core.rio.routing import route_by_score

    route_by_score(None, rio_record, score_config)
    assert rio_record.score_breakdown is not None
    assert "origem" in rio_record.score_breakdown


def _reprocess(rio_record, score_config, *, active_mar=True, **kwargs):
    """Run reprocess_record against an in-memory RioRecord (session mocked).

    active_mar: whether the record already has an active MarRecord.
    """
    session = MagicMock()
    session.get.return_value = rio_record
    session.scalar.return_value = uuid.uuid4() if active_mar else None
    routing.reprocess_record(session, rio_record.id, score_config, **kwargs)
    return session


def test_reprocess_record_resets_routing(score_config):
    """reprocess_record re-scores; a published record can stay in 'mar'."""
    rio_record = RioRecord(
        id=uuid.uuid4(),
        nascente_id=uuid.uuid4(),
        entity_type="destination",
        uf="BA",
        routing="dlq",
        normalized={
            "origem_value": 100.0,
            "completude_value": 100.0,
            "corroboracao_value": 100.0,
            "atualidade_value": 100.0,
            "validacao_humana_value": 0.0,
        },
    )
    _reprocess(rio_record, score_config)
    # After reprocess, routing is determined by score (85.0 → mar)
    assert rio_record.routing == "mar"


def test_reprocess_record_idempotent(score_config):
    """Calling reprocess_record twice produces the same result."""
    rio_record = RioRecord(
        id=uuid.uuid4(),
        nascente_id=uuid.uuid4(),
        entity_type="destination",
        uf="BA",
        routing="in_progress",
        normalized={
            "origem_value": 100.0,
            "completude_value": 80.0,
            "corroboracao_value": 60.0,
            "atualidade_value": 40.0,
            "validacao_humana_value": 0.0,
        },
    )
    _reprocess(rio_record, score_config)
    score_after_first = rio_record.score
    routing_after_first = rio_record.routing

    _reprocess(rio_record, score_config)
    assert rio_record.score == score_after_first
    assert rio_record.routing == routing_after_first


# ---------------------------------------------------------------------------
# process_nascente_record: most_recent_review_at passthrough (attraction-only)
#
# The promote_to_mar 90-day backstop reads normalized["most_recent_review_at"],
# so a lane that fetches a real review date must have it survive the Rio
# cherry-pick — otherwise every scored-fresh attraction still lands in DLQ.
# ---------------------------------------------------------------------------


@contextmanager
def _offline_rio():
    """Patch the Rio I/O seams so process_nascente_record runs pure-in-memory."""
    repo = MagicMock()
    repo.get_by_canonical_key.return_value = None
    with patch.object(routing, "_rio_repo", repo), patch.object(
        routing, "find_duplicate", return_value=None
    ), patch.object(routing, "record_event"):
        yield


def _run_rio(entity_type: str, payload: dict) -> RioRecord:
    from brave.core.rio.routing import process_nascente_record

    nascente = NascenteRecord(
        id=uuid.uuid4(),
        source="tripadvisor",
        source_ref=f"tripadvisor:{entity_type}:1",
        entity_type=entity_type,
        uf="BA",
        payload=payload,
        content_hash="deadbeef",
    )
    with _offline_rio():
        return process_nascente_record(MagicMock(), nascente, ScoreConfig())


def _payload(**extra) -> dict:
    return {"name": "Praia X", "uf": "BA", "lat": -16.4, "lng": -39.0, **extra}


def test_attraction_carries_most_recent_review_at_into_normalized():
    rio = _run_rio("attraction", _payload(most_recent_review_at="2026-07-20T12:00:00+00:00"))
    assert rio.normalized["most_recent_review_at"] == "2026-07-20T12:00:00+00:00"


def test_attraction_carries_review_count_into_normalized():
    """The backstop's "established attraction" window reads normalized["review_count"]."""
    assert _run_rio("attraction", _payload(review_count=42)).normalized["review_count"] == 42
    assert "review_count" not in _run_rio("attraction", _payload(review_count=0)).normalized
    assert "review_count" not in _run_rio("destination", _payload(review_count=42)).normalized


@pytest.mark.parametrize("payload", [_payload(), _payload(most_recent_review_at=None)])
def test_attraction_without_review_date_leaves_key_unset(payload):
    """Absent or None → key not set, so the backstop stays fail-closed."""
    rio = _run_rio("attraction", payload)
    assert "most_recent_review_at" not in rio.normalized


def test_destination_does_not_get_most_recent_review_at():
    """The backstop is attraction-only — destinos must stay untouched."""
    rio = _run_rio("destination", _payload(most_recent_review_at="2026-07-20T12:00:00+00:00"))
    assert "most_recent_review_at" not in rio.normalized


# ---------------------------------------------------------------------------
# route_by_score verdict stickiness (C5) — a re-score that does not re-judge the record
# ---------------------------------------------------------------------------

_MAR_SCORE = {
    "origem_value": 100.0, "completude_value": 100.0, "corroboracao_value": 100.0,
    "atualidade_value": 100.0, "validacao_humana_value": 100.0,
}


def test_preserving_never_resurrects_descarte(score_config, rio_record):
    rio_record.normalized = dict(_MAR_SCORE)
    rio_record.routing, rio_record.dlq_reason = "descarte", "closed_place"

    routing.route_by_score(None, rio_record, score_config)

    assert (rio_record.routing, rio_record.dlq_reason) == ("descarte", "closed_place")
    assert rio_record.score is not None  # still re-scored


@pytest.mark.parametrize("reason", sorted(routing.STICKY_DLQ_REASONS - {"no_recent_reviews"}))
def test_preserving_keeps_deliberate_verdicts_out_of_mar(score_config, rio_record, reason):
    rio_record.normalized = dict(_MAR_SCORE)
    rio_record.routing, rio_record.dlq_reason = "dlq", reason

    routing.route_by_score(None, rio_record, score_config)

    assert (rio_record.routing, rio_record.dlq_reason) == ("dlq", reason)


def test_preserving_lets_a_plain_score_reason_move(score_config, rio_record):
    rio_record.normalized = dict(_MAR_SCORE)
    rio_record.routing, rio_record.dlq_reason = "dlq", "score=50.00 below threshold_mar=85.0"

    routing.route_by_score(None, rio_record, score_config)

    assert rio_record.routing == "mar" and rio_record.dlq_reason is None


def test_preserving_releases_no_recent_reviews_once_recency_passes(score_config, rio_record):
    from datetime import UTC, datetime, timedelta

    rio_record.routing, rio_record.dlq_reason = "dlq", "no_recent_reviews"
    rio_record.normalized = dict(_MAR_SCORE)
    routing.route_by_score(None, rio_record, score_config)
    assert rio_record.dlq_reason == "no_recent_reviews"  # recency still fails

    rio_record.normalized["most_recent_review_at"] = (
        datetime.now(UTC) - timedelta(days=3)
    ).isoformat()
    routing.route_by_score(None, rio_record, score_config)
    assert rio_record.routing == "mar" and rio_record.dlq_reason is None


def test_redecided_verdict_may_leave_and_override_drops_all(score_config, rio_record):
    rio_record.normalized = dict(_MAR_SCORE)
    rio_record.routing, rio_record.dlq_reason = "dlq", "closed_temporarily"
    routing.route_by_score(None, rio_record, score_config, redecided={"closed_temporarily"})
    assert rio_record.routing == "mar" and rio_record.dlq_reason is None

    rio_record.routing, rio_record.dlq_reason = "descarte", "steward_rejected"
    routing.route_by_score(None, rio_record, score_config, override_verdicts=True)
    assert rio_record.routing == "mar"


# ---------------------------------------------------------------------------
# reprocess_record — row lock, sticky verdicts, no 'mar' without a MarRecord (F17/F24/F72)
# ---------------------------------------------------------------------------


def _dest(routing_value, reason=None):
    return RioRecord(
        id=uuid.uuid4(), nascente_id=uuid.uuid4(), entity_type="destination", uf="BA",
        routing=routing_value, dlq_reason=reason, normalized=dict(_MAR_SCORE),
    )


def test_reprocess_locks_and_refreshes_the_row(score_config):
    rio = _dest("dlq")
    session = _reprocess(rio, score_config)
    session.get.assert_called_once_with(
        RioRecord, rio.id, with_for_update=True, populate_existing=True
    )


@pytest.mark.parametrize(
    ("routing_value", "reason"),
    [("descarte", "closed_place"), ("dlq", "community_error_report"), ("dlq", "no_recent_reviews")],
)
def test_reprocess_keeps_verdicts(score_config, routing_value, reason):
    rio = _dest(routing_value, reason)
    _reprocess(rio, score_config)
    assert (rio.routing, rio.dlq_reason) == (routing_value, reason)


def test_reprocess_without_mar_record_parks_in_dlq_not_ghost_mar(score_config):
    rio = _dest("dlq", "score=50.00 below threshold_mar=80.0")
    _reprocess(rio, score_config, active_mar=False)
    assert (rio.routing, rio.dlq_reason) == ("dlq", "awaiting_promotion")


def test_reprocess_override_reaches_mar_for_the_promote(score_config):
    rio = _dest("dlq", "community_error_report")
    _reprocess(rio, score_config, active_mar=False, override_verdicts=True)
    assert rio.routing == "mar" and rio.dlq_reason is None


# ---------------------------------------------------------------------------
# process_nascente_record — category (C6), MTur flags (F44), supersede (F16), race (F22)
# ---------------------------------------------------------------------------


def test_attraction_keeps_raw_ta_category():
    assert _run_rio("attraction", _payload(category="Praias")).normalized["category"] == "Praias"
    assert "category" not in _run_rio("attraction", _payload()).normalized


def test_destination_carries_mtur_flags_even_when_false():
    canonical = {"participates_mtur": False, "mtur_categoria": "A", "regiao_turistica": "Costa"}
    rio = _run_rio("destination", _payload(canonical=canonical))
    assert rio.normalized["participates_mtur"] is False
    assert rio.normalized["mtur_categoria"] == "A"
    assert rio.normalized["regiao_turistica"] == "Costa"
    assert "participates_mtur" not in _run_rio("attraction", _payload(canonical=canonical)).normalized


def _nascente(payload, *, superseded_by=None):
    return NascenteRecord(
        id=uuid.uuid4(), source="ibge", source_ref="ibge:2925303", entity_type="destination",
        uf="BA", payload=payload, content_hash=uuid.uuid4().hex, version=1,
        superseded_by_id=superseded_by,
    )


def _supersede(existing, old, new):
    session = MagicMock()
    session.get.return_value = old
    repo = MagicMock()
    repo.get_by_canonical_key.return_value = existing
    with patch.object(routing, "_rio_repo", repo), patch.object(routing, "record_event"):
        return routing.process_nascente_record(session, new, ScoreConfig())


def test_new_nascente_version_reaches_the_rio_without_erasing_enrichment():
    v1_payload = {"name": "Porto Seguro", "canonical": {"participates_mtur": False}}
    v1 = _nascente(v1_payload)
    existing = RioRecord(
        id=uuid.uuid4(), nascente_id=v1.id, entity_type="destination", uf="BA",
        routing="dlq", dlq_reason="score=38.00 below threshold_mar=80.0",
        canonical_key="ibge:2925303",
        normalized={**routing._build_normalized(v1), "name": "Porto Seguro (curado)"},
    )
    v2 = _nascente({"name": "Porto Seguro BA", "canonical": {"participates_mtur": True}})

    rio = _supersede(existing, v1, v2)

    assert rio is existing
    assert rio.nascente_id == v2.id  # no longer 'unrouted' on the Painel
    assert rio.normalized["participates_mtur"] is True  # untouched lane fact follows the source
    assert rio.normalized["name"] == "Porto Seguro (curado)"  # changed since v1 → kept


def test_superseded_nascente_version_never_applies():
    v1 = _nascente({"name": "A"}, superseded_by=uuid.uuid4())
    current_id = uuid.uuid4()
    existing = RioRecord(
        id=uuid.uuid4(), nascente_id=current_id, entity_type="destination", uf="BA",
        routing="dlq", canonical_key="ibge:2925303", normalized={"name": "B"},
    )
    rio = _supersede(existing, None, v1)
    assert rio.nascente_id == current_id and rio.normalized == {"name": "B"}


def test_concurrent_insert_of_same_canonical_key_returns_the_winner():
    from sqlalchemy.exc import IntegrityError

    winner = RioRecord(id=uuid.uuid4(), canonical_key="tripadvisor:attraction:1")
    repo = MagicMock()
    repo.get_by_canonical_key.side_effect = [None, winner]
    repo.add.side_effect = IntegrityError("INSERT", {}, Exception("uq_rio_canonical_key"))
    nascente = NascenteRecord(
        id=uuid.uuid4(), source="tripadvisor", source_ref="tripadvisor:attraction:1",
        entity_type="attraction", uf="BA", payload=_payload(), content_hash="x",
    )
    with patch.object(routing, "_rio_repo", repo), patch.object(
        routing, "find_duplicate", return_value=None
    ), patch.object(routing, "record_event"):
        assert routing.process_nascente_record(MagicMock(), nascente, ScoreConfig()) is winner
