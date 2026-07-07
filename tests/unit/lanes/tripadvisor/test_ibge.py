"""Tests for TripAdvisor IBGE municipality resolver (TA-03).

Tests fuzzy matching, haversine fallback, and None-return for unresolvable names.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from brave.lanes.tripadvisor.ibge import IbgeMunicipio, load_ibge_csv, resolve_municipio


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MINIMAL_CSV = """\
ibge_code,nome,uf,lat,lng
3550308,São Paulo,SP,-23.5505,-46.6333
2927408,Salvador,BA,-12.9714,-38.5014
3304557,Rio de Janeiro,RJ,-22.9068,-43.1729
2910800,Feira de Santana,BA,-12.2664,-38.9663
1100205,Porto Velho,RO,-8.7612,-63.9004
"""


def _make_records() -> list[IbgeMunicipio]:
    """Build a small in-memory IbgeMunicipio list from MINIMAL_CSV."""
    lines = MINIMAL_CSV.strip().split("\n")
    header, *rows = lines
    result = []
    for row in rows:
        ibge_code, nome, uf, lat, lng = row.split(",")
        result.append(
            IbgeMunicipio(
                ibge_code=ibge_code,
                nome=nome,
                uf=uf,
                lat=float(lat),
                lng=float(lng),
            )
        )
    return result


PR_ROWS_CSV = """\
ibge_code,nome,uf,lat,lng
4106902,Curitiba,PR,-25.4195,-49.2646
4104659,Carambeí,PR,-24.9152,-50.0986
4115200,Maringá,PR,-23.4205,-51.9333
"""


def _make_pr_records() -> list[IbgeMunicipio]:
    """Build 3 Paraná IbgeMunicipio records for accent-fold tests (TA-03).

    Separate from _make_records() / MINIMAL_CSV so TestLoadIbgeCsv::test_load_ibge_csv_from_file
    len(records) == 5 assertion is never disturbed.
    """
    lines = PR_ROWS_CSV.strip().split("\n")
    _header, *rows = lines
    result = []
    for row in rows:
        ibge_code, nome, uf, lat, lng = row.split(",")
        result.append(
            IbgeMunicipio(
                ibge_code=ibge_code,
                nome=nome,
                uf=uf,
                lat=float(lat),
                lng=float(lng),
            )
        )
    return result


# ---------------------------------------------------------------------------
# Tests: IbgeMunicipio dataclass
# ---------------------------------------------------------------------------


class TestIbgeMunicipioDataclass:
    def test_ibge_municipio_attributes(self) -> None:
        m = IbgeMunicipio(
            ibge_code="3550308",
            nome="São Paulo",
            uf="SP",
            lat=-23.5505,
            lng=-46.6333,
        )
        assert m.ibge_code == "3550308"
        assert m.nome == "São Paulo"
        assert m.uf == "SP"
        assert m.lat == pytest.approx(-23.5505)
        assert m.lng == pytest.approx(-46.6333)


# ---------------------------------------------------------------------------
# Tests: load_ibge_csv
# ---------------------------------------------------------------------------


class TestLoadIbgeCsv:
    def test_load_ibge_csv_from_file(self, tmp_path: Path) -> None:
        csv_file = tmp_path / "ibge.csv"
        csv_file.write_text(MINIMAL_CSV)
        records = load_ibge_csv(csv_file)
        assert len(records) == 5
        assert records[0].nome == "São Paulo"
        assert records[0].ibge_code == "3550308"

    def test_load_ibge_csv_header_only(self, tmp_path: Path) -> None:
        csv_file = tmp_path / "ibge.csv"
        csv_file.write_text("ibge_code,nome,uf,lat,lng\n")
        records = load_ibge_csv(csv_file)
        assert records == []


# ---------------------------------------------------------------------------
# Tests: resolve_municipio — fuzzy matching
# ---------------------------------------------------------------------------


class TestResolveMunicipio:
    def test_ibge_exact_match(self) -> None:
        """Exact name match should resolve to the correct record."""
        records = _make_records()
        result = resolve_municipio("São Paulo", "SP", records)
        assert result is not None
        assert result.ibge_code == "3550308"

    def test_ibge_fuzzy_match_accents(self) -> None:
        """'Sao Paulo' (no accent) should match 'São Paulo' via token_sort_ratio."""
        records = _make_records()
        result = resolve_municipio("Sao Paulo", "SP", records)
        assert result is not None
        assert result.ibge_code == "3550308"
        assert result.nome == "São Paulo"

    def test_ibge_fuzzy_match_wrong_uf_returns_none(self) -> None:
        """Search filtered by UF — 'São Paulo' in UF='BA' should return None."""
        records = _make_records()
        result = resolve_municipio("São Paulo", "BA", records)
        assert result is None

    def test_ibge_no_match_returns_none(self) -> None:
        """Name with no fuzzy match and no coords should return None → quarantine ibge_unmatched."""
        records = _make_records()
        result = resolve_municipio("ZZZUnknown", "SP", records)
        assert result is None

    @pytest.mark.parametrize("bad_name", [None, "", "   "])
    def test_ibge_none_or_blank_name_returns_none(self, bad_name) -> None:
        """Falsy/non-str name → None (unmatched), never a normalize() crash.

        Regression: TA fetch_attraction_geo can return cityName=None; feeding that
        to _fold_accents → unicodedata.normalize raised
        'normalize() argument 2 must be str, not None', poisoning the whole
        produce task (5 coordless ES beach attractions, 2026-07-05).
        """
        records = _make_records()
        assert resolve_municipio(bad_name, "SP", records) is None

    def test_ibge_case_insensitive_like_match(self) -> None:
        """'salvador' (lowercase) should match 'Salvador' in BA."""
        records = _make_records()
        result = resolve_municipio("salvador", "BA", records)
        assert result is not None
        assert result.ibge_code == "2927408"

    # ---------------------------------------------------------------------------
    # Tests: haversine fallback
    # ---------------------------------------------------------------------------

    def test_ibge_haversine_fallback(self) -> None:
        """When name doesn't match, coordinates within max_distance should return a record."""
        records = _make_records()
        # Use coordinates very close to Salvador (< 15km away)
        result = resolve_municipio(
            "XYZ City",  # won't fuzzy-match
            "BA",
            records,
            candidate_lat=-12.98,  # ~1km from Salvador
            candidate_lng=-38.51,
        )
        assert result is not None
        assert result.ibge_code == "2927408"  # Salvador

    def test_ibge_haversine_no_fallback_when_too_far(self) -> None:
        """Coordinates far away from any record should return None."""
        records = _make_records()
        result = resolve_municipio(
            "XYZ City",
            "BA",
            records,
            candidate_lat=-1.0,  # very far from Salvador
            candidate_lng=-40.0,
        )
        assert result is None

    def test_ibge_no_match_no_coords_returns_none(self) -> None:
        """When name doesn't match and no coords provided, must return None."""
        records = _make_records()
        result = resolve_municipio("Inexistente Cidade", "SP", records)
        assert result is None

    # ---------------------------------------------------------------------------
    # Tests: resolve_municipio — accent-fold (TA-03 fix, 2026-06-30)
    # ---------------------------------------------------------------------------

    def test_ibge_accent_fold_maringa(self) -> None:
        """ASCII 'Maringa' must resolve to accented IBGE 'Maringá' (TA-03 accent fix).

        Before the _fold_accents fix, fuzz.token_sort_ratio('maringa', 'maringá') = 85.7
        which fell below the 88 threshold, causing a None return for 4 of 60 PR atrativos.
        """
        records = _make_pr_records()
        result = resolve_municipio("Maringa", "PR", records)
        assert result is not None, "Expected Maringá — accent fold must bridge ASCII→accented"
        assert result.nome == "Maringá"
        assert result.ibge_code == "4115200"

    def test_ibge_accent_fold_carambei(self) -> None:
        """ASCII 'Carambei' must resolve to accented IBGE 'Carambeí' (TA-03 accent fix)."""
        records = _make_pr_records()
        result = resolve_municipio("Carambei", "PR", records)
        assert result is not None, "Expected Carambeí — accent fold must bridge ASCII→accented"
        assert result.nome == "Carambeí"
        assert result.ibge_code == "4104659"

    def test_ibge_exact_match_still_works_curitiba(self) -> None:
        """Exact name 'Curitiba' (no accent needed) must still resolve after fold."""
        records = _make_pr_records()
        result = resolve_municipio("Curitiba", "PR", records)
        assert result is not None
        assert result.ibge_code == "4106902"

    def test_ibge_accent_fold_no_overmatch(self) -> None:
        """Accent-folding must not cause over-matching: 'ZZZFantasia' in PR must return None."""
        records = _make_pr_records()
        result = resolve_municipio("ZZZFantasia", "PR", records)
        assert result is None


# ---------------------------------------------------------------------------
# Tests: resolve_municipio — default parameter invariant (TA-15)
# ---------------------------------------------------------------------------


def test_resolve_municipio_default_max_distance_km_is_15() -> None:
    """resolve_municipio default max_distance_km must remain 15.0 (TA-15 invariant).

    This assertion guards against accidental modification of the default, which
    would break Phase-11/13 destinos behavior. The geo-enrichment block in
    atrativos._ingest_one passes max_distance_km=50.0 explicitly — the default
    must NOT change.
    """
    import inspect

    sig = inspect.signature(resolve_municipio)
    default = sig.parameters["max_distance_km"].default
    assert default == 15.0, (
        f"resolve_municipio default max_distance_km must be 15.0 (TA-15 invariant), "
        f"got {default!r}. Changing this default would break Phase-11/13 destinos "
        f"behavior. Pass max_distance_km=50.0 explicitly at the geo-enrichment call site."
    )
