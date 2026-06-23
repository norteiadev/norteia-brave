"""Tests for mar_ready flag in route_by_score (TA-05, T-11-02-02).

The mar_ready flag is set in route_by_score after the dlq_reason block.
It is True ONLY when:
  - entity_type == "attraction"
  - canonical_key starts with "tripadvisor:"
  - atualidade_value >= config.mar_ready_atualidade_bar (default 70.0)
  - corroboracao_value >= config.mar_ready_corrob_bar (default 60.0)

For all other sources (mtur, places) or low scores: mar_ready == False.

Security note (T-11-02-02): explicit False for all non-qualifying paths ensures
that re-scoring a record always resets mar_ready rather than keeping a stale True.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from brave.config.settings import ScoreConfig
from brave.core.models import RioRecord
from brave.core.rio.routing import route_by_score


def _make_config(**overrides) -> ScoreConfig:
    defaults = dict(
        weight_origem=30.0,
        weight_completude=20.0,
        weight_corroboracao=20.0,
        weight_atualidade=15.0,
        weight_validacao_humana=15.0,
        threshold_mar=85.0,
        threshold_dlq=40.0,
        score_version="v1.1",
        mar_ready_atualidade_bar=70.0,
        mar_ready_corrob_bar=60.0,
    )
    defaults.update(overrides)
    return ScoreConfig(**defaults)


def _make_rio_record(
    entity_type: str = "attraction",
    canonical_key: str = "tripadvisor:attraction:99999",
    origin: float = 65.0,
    completude: float = 100.0,
    corroboracao: float = 85.0,
    atualidade: float = 70.0,
    validacao: float = 0.0,
) -> RioRecord:
    """Build a minimal in-memory RioRecord with a normalized payload."""
    rio = MagicMock(spec=RioRecord)
    rio.entity_type = entity_type
    rio.canonical_key = canonical_key
    rio.normalized = {
        "origem_value": origin,
        "completude_value": completude,
        "corroboracao_value": corroboracao,
        "atualidade_value": atualidade,
        "validacao_humana_value": validacao,
    }
    # Allow attribute assignment
    rio.score = None
    rio.routing = "in_progress"
    rio.score_version = None
    rio.score_breakdown = None
    rio.processed_at = None
    rio.dlq_reason = None
    rio.mar_ready = None
    return rio


class TestMarReadyFlag:
    """Verify route_by_score sets mar_ready correctly (T-11-02-02)."""

    def test_mar_ready_set_for_qualifying_ta_attraction(self) -> None:
        """TA attraction with atualidade≥70 and corroboracao≥60 → mar_ready=True."""
        config = _make_config()
        rio = _make_rio_record(
            entity_type="attraction",
            canonical_key="tripadvisor:attraction:99999",
            corroboracao=85.0,  # ≥60 bar
            atualidade=70.0,    # ≥70 bar
        )
        route_by_score(None, rio, config)
        assert rio.mar_ready is True, f"Expected mar_ready=True, got {rio.mar_ready}"

    def test_mar_ready_not_set_for_mtur_source(self) -> None:
        """mtur source → mar_ready=False (T-11-02-02 explicit False for non-TA sources)."""
        config = _make_config()
        rio = _make_rio_record(
            entity_type="destination",
            canonical_key="mtur:BA:2927408",
            corroboracao=0.0,
            atualidade=70.0,
        )
        route_by_score(None, rio, config)
        assert rio.mar_ready is False, f"Expected mar_ready=False for mtur, got {rio.mar_ready}"

    def test_mar_ready_not_set_for_low_corroboracao(self) -> None:
        """TA attraction with corroboracao < 60 → mar_ready=False."""
        config = _make_config()
        rio = _make_rio_record(
            entity_type="attraction",
            canonical_key="tripadvisor:attraction:99999",
            corroboracao=30.0,  # < 60 bar
            atualidade=70.0,
        )
        route_by_score(None, rio, config)
        assert rio.mar_ready is False, (
            f"Expected mar_ready=False for low corroboracao, got {rio.mar_ready}"
        )

    def test_mar_ready_not_set_for_low_atualidade(self) -> None:
        """TA attraction with atualidade < 70 → mar_ready=False."""
        config = _make_config()
        rio = _make_rio_record(
            entity_type="attraction",
            canonical_key="tripadvisor:attraction:99999",
            corroboracao=85.0,
            atualidade=40.0,  # < 70 bar
        )
        route_by_score(None, rio, config)
        assert rio.mar_ready is False, (
            f"Expected mar_ready=False for low atualidade, got {rio.mar_ready}"
        )

    def test_mar_ready_not_set_for_non_attraction_entity(self) -> None:
        """entity_type='destination' (not 'attraction') → mar_ready=False."""
        config = _make_config()
        rio = _make_rio_record(
            entity_type="destination",
            canonical_key="tripadvisor:destination:303506",
            corroboracao=85.0,
            atualidade=70.0,
        )
        route_by_score(None, rio, config)
        assert rio.mar_ready is False, (
            f"Expected mar_ready=False for destination entity_type, got {rio.mar_ready}"
        )

    def test_mar_ready_not_set_for_non_ta_canonical_key(self) -> None:
        """Attraction with non-TA canonical_key → mar_ready=False."""
        config = _make_config()
        rio = _make_rio_record(
            entity_type="attraction",
            canonical_key="places:BA:ChIJAbcDef",  # non-tripadvisor source
            corroboracao=85.0,
            atualidade=70.0,
        )
        route_by_score(None, rio, config)
        assert rio.mar_ready is False, (
            f"Expected mar_ready=False for non-TA canonical key, got {rio.mar_ready}"
        )

    def test_mar_ready_configurable_bars(self) -> None:
        """Custom bars: atualidade_bar=80, corrob_bar=90 — fails with atualidade=70."""
        config = _make_config(mar_ready_atualidade_bar=80.0, mar_ready_corrob_bar=90.0)
        rio = _make_rio_record(
            entity_type="attraction",
            canonical_key="tripadvisor:attraction:99999",
            corroboracao=85.0,  # < 90 bar
            atualidade=70.0,    # < 80 bar
        )
        route_by_score(None, rio, config)
        assert rio.mar_ready is False, (
            f"Expected mar_ready=False with custom bars, got {rio.mar_ready}"
        )

    def test_route_by_score_sets_routing_and_score(self) -> None:
        """route_by_score still sets score, routing, score_version correctly."""
        config = _make_config()
        rio = _make_rio_record(
            entity_type="attraction",
            canonical_key="tripadvisor:attraction:99999",
            corroboracao=85.0,
            atualidade=70.0,
        )
        route_by_score(None, rio, config)
        assert rio.score is not None
        assert rio.routing in ("mar", "dlq", "descarte")
        assert rio.score_version == "v1.1"
