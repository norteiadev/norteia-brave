"""Unit tests for brave.clients.mtur._map_categoria nomenclature mapping.

Covers old (A–E) and 2025 nomenclature. The 2025 Mapa do Turismo "simplified
categorization" export uses the SINGULAR top-tier label "Município turístico"
(not the plural "Municípios turísticos" the README assumed). Real 2025 data has
622 such rows — they must map to "Oferta Principal", not the Apoio default.
"""

import pytest

from brave.clients.mtur import _map_categoria


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Old nomenclature (pre-2025).
        ("A", "Oferta Principal"),
        ("B", "Oferta Principal"),
        ("C", "Complementar"),
        ("D", "Complementar"),
        ("E", "Apoio"),
        # New 2025 nomenclature — exact portal strings (simplified export).
        ("Município turístico", "Oferta Principal"),  # SINGULAR — the live string
        ("Municípios turísticos", "Oferta Principal"),  # plural variant
        ("Município com oferta turística complementar", "Complementar"),
        ("Município de apoio ao turismo", "Apoio"),
        # Robustness.
        ("", "Apoio"),
        ("algo desconhecido", "Apoio"),
    ],
)
def test_map_categoria(raw: str, expected: str) -> None:
    assert _map_categoria(raw) == expected


def test_complementar_not_misread_as_principal() -> None:
    """'turística' (feminine, in the complementar label) must NOT match the
    'turístico' top-tier check — guards against the substring fix over-matching."""
    assert _map_categoria("Município com oferta turística complementar") == "Complementar"
