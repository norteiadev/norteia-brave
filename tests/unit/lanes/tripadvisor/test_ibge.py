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
