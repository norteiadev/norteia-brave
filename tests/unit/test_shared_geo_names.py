"""brave.shared.geo / brave.shared.names — and the image lane still sees the same matcher."""

from __future__ import annotations

import pytest

from brave.shared.geo import haversine_km
from brave.shared.names import name_score, norm


def test_haversine_zero_and_a_known_distance() -> None:
    assert haversine_km(-20.8, -40.6, -20.8, -40.6) == 0.0
    # Rio de Janeiro -> São Paulo (city centres): ~358 km great-circle
    assert haversine_km(-22.9068, -43.1729, -23.5505, -46.6333) == pytest.approx(360.7, abs=2)
    # 0.001 degree of latitude is ~111 m
    assert haversine_km(-20.0, -40.0, -20.001, -40.0) == pytest.approx(0.111, abs=0.002)


def test_haversine_is_symmetric() -> None:
    a, b = (-20.1, -40.5), (-20.3, -40.2)
    assert haversine_km(*a, *b) == pytest.approx(haversine_km(*b, *a))


def test_norm_folds_accents_case_and_stop_words() -> None:
    assert norm("Santuário de São José") == ["santuario", "sao", "jose"]


def test_the_image_lane_re_exports_the_shared_matcher() -> None:
    from brave.domains.images import matching as images_matching

    assert images_matching.name_score is name_score
    assert images_matching.norm is norm
