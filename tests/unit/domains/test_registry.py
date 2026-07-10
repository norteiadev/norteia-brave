"""Unit tests for the Brave source-domain registry (Phase G)."""

import pytest

from brave.config.settings import AppConfig
from brave.domains import (
    enabled_sources,
    get_domain,
    registered_source_names,
)
from brave.domains.base import SourceDomain


def test_all_builtin_sources_resolve():
    for name in ("default", "tripadvisor", "manual"):
        domain = get_domain(name)
        assert isinstance(domain, SourceDomain)


def test_default_is_the_places_domain():
    from brave.domains.places.controllers import PLACES_DOMAIN

    assert get_domain("default") is PLACES_DOMAIN
    assert get_domain("default").name == "default"


def test_each_domain_reports_its_own_name():
    assert get_domain("default").name == "default"
    assert get_domain("tripadvisor").name == "tripadvisor"
    assert get_domain("manual").name == "manual"


def test_unknown_source_raises_keyerror():
    with pytest.raises(KeyError):
        get_domain("does-not-exist")


def test_registered_source_names_includes_builtins():
    names = set(registered_source_names())
    assert {"default", "tripadvisor", "manual"} <= names


def test_enabled_sources_are_sweep_lanes_only():
    """TripAdvisor is the live sweep lane; the 'default' (Places) lane ships dormant
    (source.default.enabled=false) and manual is never a sweep source."""
    enabled = enabled_sources(AppConfig())
    assert "tripadvisor" in enabled
    assert "default" not in enabled  # dormant by default (re-enablable via config)
    assert "manual" not in enabled


def test_score_input_helper_is_shared_across_domains():
    payload = {"origem_value": 60.0, "completude_value": 50.0}
    si = get_domain("tripadvisor").score_input(payload)
    assert si.origem_value == 60.0
    assert si.completude_value == 50.0
    # Missing criteria default to 0.0 (mirrors routing.route_by_score).
    assert si.corroboracao_value == 0.0
