"""_is_not_atrativo: agencies, services and plain stores leave the TA sweep (real SE cards)."""

import pytest

from brave.domains.tripadvisor.atrativos import _is_not_atrativo


@pytest.mark.parametrize(
    ("category", "review_count", "expected"),
    [
        ("Lojas especializadas e de suvenir", 0, True),  # M Motos Aracaju
        ("Lojas de departamento", 0, True),  # Magazine Luiza
        ("Passeios de barco • Passeios turísticos", 237, True),  # GuiaFD - Agência de Turismo
        ("Excursões culturais • Excursões por patrimônios históricos", 329, True),  # agency
        ("Táxis e traslados • Passeios de barco", 0, True),
        ("Academias de ginástica • Complexos esportivos", 1, True),
        ("Spas", 0, True),
        ("Baladas", 307, True),  # Casa de Forró Cariri: commercial leisure
        ("Lojas especializadas e de suvenir", 465, False),  # Riomar Aracaju
        ("Lojas especializadas e de suvenir • Passeios urbanos", 547, False),  # Feira do Turista
        ("Shopping centers", 275, False),
        ("Natureza e vida selvagem • Parques aquáticos", 160, False),
        ("Igrejas e catedrais", 0, False),  # unreviewed but a real atrativo
        ("Cânions • Trilhas para caminhada", 1, False),
        ("", 0, False),
    ],
)
def test_is_not_atrativo(category: str, review_count: int, expected: bool) -> None:
    assert _is_not_atrativo({"category": category, "review_count": review_count}) is expected


def test_missing_fields_stay() -> None:
    assert _is_not_atrativo({}) is False
