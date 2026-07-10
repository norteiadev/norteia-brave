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
    resolve_distrito_place,
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


def test_place_golden_arraial_dajuda(distritos: list[IbgeDistrito]) -> None:
    """MD breadcrumb <Place> 'Arraial d'Ajuda' under Porto Seguro (2925303) →
    genuine sub-município distrito 292530307; ibge_code is the parent município."""
    match = resolve_distrito_place("Arraial d'Ajuda", "2925303", distritos)
    assert match is not None
    assert match.distrito_code == "292530307"
    assert match.ibge_code == "2925303"


def test_place_seat_guard_porto_seguro(distritos: list[IbgeDistrito]) -> None:
    """<Place> equal to the parent município name is the seat distrito → None."""
    assert resolve_distrito_place("Porto Seguro", "2925303", distritos) is None


def test_place_seat_guard_belo_horizonte(distritos: list[IbgeDistrito]) -> None:
    """Belo Horizonte seat under BH (3106200) → None (seat, not finer-than-município)."""
    assert resolve_distrito_place("Belo Horizonte", "3106200", distritos) is None


def test_place_empty_returns_none(distritos: list[IbgeDistrito]) -> None:
    """Empty / None <Place> yields None (never crash)."""
    assert resolve_distrito_place("", "2925303", distritos) is None
    assert resolve_distrito_place(None, "2925303", distritos) is None  # type: ignore[arg-type]
