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
    for name in ("mtur", "default", "tripadvisor", "manual"):
        domain = get_domain(name)
        assert isinstance(domain, SourceDomain)


def test_default_alias_is_the_mtur_domain():
    assert get_domain("default") is get_domain("mtur")
    assert get_domain("default").name == "mtur"


def test_each_domain_reports_its_own_name():
    assert get_domain("tripadvisor").name == "tripadvisor"
    assert get_domain("manual").name == "manual"


def test_unknown_source_raises_keyerror():
    with pytest.raises(KeyError):
        get_domain("does-not-exist")


def test_registered_source_names_includes_builtins():
    names = set(registered_source_names())
    assert {"mtur", "default", "tripadvisor", "manual"} <= names


def test_enabled_sources_are_sweep_lanes_only():
    """Default config enables both sweep lanes; manual is never a sweep source."""
    enabled = enabled_sources(AppConfig())
    assert "default" in enabled
    assert "tripadvisor" in enabled
    assert "manual" not in enabled


def test_score_input_helper_is_shared_across_domains():
    payload = {"origem_value": 60.0, "completude_value": 50.0}
    si = get_domain("tripadvisor").score_input(payload)
    assert si.origem_value == 60.0
    assert si.completude_value == 50.0
    # Missing criteria default to 0.0 (mirrors routing.route_by_score).
    assert si.corroboracao_value == 0.0
