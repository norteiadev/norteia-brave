"""Unit tests for brave.lanes.tripadvisor.uf_names.state_name_to_uf (TA-ftx).

Parametrized coverage:
  (a) All 27 UFs — at least one test case per UF
  (b) DF special cases: "Federal District"→"DF", "Distrito Federal"→"DF",
      "State of Distrito Federal"→"DF"
  (c) "State of " strip: "State of Sao Paulo"→"SP", etc.
  (d) ASCII-fold: "State of São Paulo"→"SP", "State of Pará"→"PA"
  (e) Unknown → None, "" → None
  (f) Whitespace tolerance
  (g) Canonical dict invariants (28 keys, 27 unique UFs)

No external dependencies — pure unit test.
"""
from __future__ import annotations

import pytest

from brave.lanes.tripadvisor.uf_names import _TA_STATE_CANONICAL, state_name_to_uf


# ---------------------------------------------------------------------------
# Parametrized: all 27 UFs (one canonical case each)
# ---------------------------------------------------------------------------

_ALL_UF_CASES: list[tuple[str, str]] = [
    ("State of Acre", "AC"),
    ("State of Alagoas", "AL"),
    ("State of Amapa", "AP"),
    ("State of Amazonas", "AM"),
    ("State of Bahia", "BA"),
    ("State of Ceara", "CE"),
    ("Federal District", "DF"),          # live-confirmed 2026-06-30: DF has no "State of " prefix
    ("State of Espirito Santo", "ES"),
    ("State of Goias", "GO"),
    ("State of Maranhao", "MA"),
    ("State of Mato Grosso", "MT"),
    ("State of Mato Grosso do Sul", "MS"),
    ("State of Minas Gerais", "MG"),
    ("State of Para", "PA"),
    ("State of Paraiba", "PB"),
    ("State of Parana", "PR"),
    ("State of Pernambuco", "PE"),
    ("State of Piaui", "PI"),
    ("State of Rio de Janeiro", "RJ"),
    ("State of Rio Grande do Norte", "RN"),
    ("State of Rio Grande do Sul", "RS"),
    ("State of Rondonia", "RO"),
    ("State of Roraima", "RR"),
    ("State of Santa Catarina", "SC"),
    ("State of Sao Paulo", "SP"),
    ("State of Sergipe", "SE"),
    ("State of Tocantins", "TO"),
]


class TestStateNameToUf:
    """Parametrized suite for state_name_to_uf."""

    @pytest.mark.parametrize("name,expected_uf", _ALL_UF_CASES)
    def test_all_27_ufs(self, name: str, expected_uf: str) -> None:
        """All 27 UFs resolve correctly via 'State of X' or bare name."""
        result = state_name_to_uf(name)
        assert result == expected_uf, (
            f"state_name_to_uf({name!r}) returned {result!r}, expected {expected_uf!r}"
        )

    # (b) DF special cases -------------------------------------------------

    def test_df_english_bare(self) -> None:
        """'Federal District' (English bare form, no prefix) → 'DF' (live-confirmed 2026-06-30)."""
        assert state_name_to_uf("Federal District") == "DF"

    def test_df_portuguese_form(self) -> None:
        """'Distrito Federal' (Portuguese form) → 'DF'."""
        assert state_name_to_uf("Distrito Federal") == "DF"

    def test_df_with_state_of_prefix(self) -> None:
        """'State of Distrito Federal' (extra robustness) → 'DF'."""
        assert state_name_to_uf("State of Distrito Federal") == "DF"

    # (c) "State of " strip ------------------------------------------------

    def test_state_of_sao_paulo_ascii(self) -> None:
        """'State of Sao Paulo' (ASCII, no accent) → 'SP'."""
        assert state_name_to_uf("State of Sao Paulo") == "SP"

    def test_state_of_para_ascii(self) -> None:
        """'State of Para' (ASCII, no accent) → 'PA'."""
        assert state_name_to_uf("State of Para") == "PA"

    def test_state_of_minas_gerais(self) -> None:
        """'State of Minas Gerais' → 'MG'."""
        assert state_name_to_uf("State of Minas Gerais") == "MG"

    # (d) ASCII-fold / accent normalization --------------------------------

    def test_accented_sao_paulo(self) -> None:
        """'State of São Paulo' (accented ã) → 'SP' after NFKD normalization."""
        assert state_name_to_uf("State of São Paulo") == "SP"

    def test_accented_para(self) -> None:
        """'State of Pará' (accented á) → 'PA' after NFKD normalization."""
        assert state_name_to_uf("State of Pará") == "PA"

    # bare name (no "State of " prefix) ------------------------------------

    def test_bare_parana(self) -> None:
        """'Parana' (bare, no prefix) → 'PR' — strip is conditional."""
        assert state_name_to_uf("Parana") == "PR"

    # (e) Unknown / empty → None -------------------------------------------

    def test_unknown_state_returns_none(self) -> None:
        assert state_name_to_uf("unknown state xyz") is None

    def test_empty_string_returns_none(self) -> None:
        assert state_name_to_uf("") is None

    def test_federal_republic_returns_none(self) -> None:
        assert state_name_to_uf("Federal Republic") is None

    # (f) Whitespace tolerance ---------------------------------------------

    def test_leading_trailing_whitespace(self) -> None:
        """Leading/trailing whitespace is stripped before processing."""
        assert state_name_to_uf("  State of Parana  ") == "PR"

    # (g) Canonical dict invariants ----------------------------------------

    def test_canonical_dict_has_27_unique_ufs(self) -> None:
        """_TA_STATE_CANONICAL values contain exactly 27 distinct UF codes."""
        ufs = set(_TA_STATE_CANONICAL.values())
        assert len(ufs) == 27, (
            f"Expected 27 distinct UF codes in _TA_STATE_CANONICAL, got {len(ufs)}: {sorted(ufs)}"
        )

    def test_canonical_dict_has_28_keys(self) -> None:
        """_TA_STATE_CANONICAL has 28 keys: 27 UFs × 1 key each + 1 extra for DF."""
        assert len(_TA_STATE_CANONICAL) == 28, (
            f"Expected 28 keys (DF has 2 entries: 'distrito federal' and 'federal district'), "
            f"got {len(_TA_STATE_CANONICAL)}"
        )
