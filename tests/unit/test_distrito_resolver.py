"""Tests for the IBGE distrito resolver (DTB name-only match).

Covers the golden Arraial d'Ajuda case, an accent/variant hit, the wrong-município
guard (same name under a different parent → None), and the no-hint None returns.

Loads the real data/ibge/ibge_distritos.csv via load_distritos_csv.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from brave.shared.ibge_distritos import (
    IbgeDistrito,
    load_distritos_csv,
    resolve_distrito,
)

# Repo-root-relative path to the shipped DTB distrito reference CSV.
_CSV_PATH = Path(__file__).resolve().parents[2] / "data" / "ibge" / "ibge_distritos.csv"


@pytest.fixture(scope="module")
def distritos() -> list[IbgeDistrito]:
    """Load the real IBGE distrito CSV once per module."""
    records = load_distritos_csv(_CSV_PATH)
    assert records, "ibge_distritos.csv loaded empty"
    return records


def test_golden_arraial_dajuda(distritos: list[IbgeDistrito]) -> None:
    """Arraial d'Ajuda under Porto Seguro (2925303) → distrito 292530307."""
    match = resolve_distrito("Arraial d'Ajuda", "2925303", distritos)
    assert match is not None
    assert match.distrito_code == "292530307"
    assert match.ibge_code == "2925303"


def test_accent_and_apostrophe_variant(distritos: list[IbgeDistrito]) -> None:
    """Accent-folded / punctuation-stripped variant still resolves to 292530307."""
    match = resolve_distrito("arraial dajuda", "2925303", distritos)
    assert match is not None
    assert match.distrito_code == "292530307"


def test_wrong_municipio_guard(distritos: list[IbgeDistrito]) -> None:
    """Same distrito name but scoped to São Paulo (3550308) → None (no candidate)."""
    match = resolve_distrito("Arraial d'Ajuda", "3550308", distritos)
    assert match is None


def test_no_hint_returns_none(distritos: list[IbgeDistrito]) -> None:
    """Falsy / non-str name hints yield None (never crash)."""
    assert resolve_distrito(None, "2925303", distritos) is None  # type: ignore[arg-type]
    assert resolve_distrito("", "2925303", distritos) is None
    assert resolve_distrito("   ", "2925303", distritos) is None
