"""Tests for Rio routing logic (route_by_score, reprocess_record).

All tests run fully offline — no DB, no I/O.
"""

import uuid

import pytest

from brave.config.settings import ScoreConfig
from brave.core.models import RioRecord


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


def test_route_by_score_descarte(score_config, rio_record):
    """route_by_score with score=30.0 sets routing='descarte'."""
    from brave.core.rio.routing import route_by_score

    rio_record.normalized = {
        "origem_value": 40.0,
        "completude_value": 20.0,
        "corroboracao_value": 0.0,
        "atualidade_value": 0.0,
        "validacao_humana_value": 0.0,
    }
    # 12+4+0+0+0 = 16.0 → descarte
    route_by_score(None, rio_record, score_config)
    assert rio_record.routing == "descarte"


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


def test_reprocess_record_resets_routing(score_config):
    """reprocess_record resets RioRecord.routing to 'in_progress' then re-scores."""
    from brave.core.rio.routing import reprocess_record_inline

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
    reprocess_record_inline(rio_record, score_config)
    # After reprocess, routing is determined by score (85.0 → mar)
    assert rio_record.routing == "mar"


def test_reprocess_record_idempotent(score_config):
    """Calling reprocess_record_inline twice produces the same result."""
    from brave.core.rio.routing import reprocess_record_inline

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
    reprocess_record_inline(rio_record, score_config)
    score_after_first = rio_record.score
    routing_after_first = rio_record.routing

    reprocess_record_inline(rio_record, score_config)
    assert rio_record.score == score_after_first
    assert rio_record.routing == routing_after_first
