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
        ("Shopping centers", 275, True),  # Shopping Jardins: malls are never atrativos
        ("Natureza e vida selvagem • Parques aquáticos", 160, False),
        ("Igrejas e catedrais", 0, False),  # unreviewed but a real atrativo
        ("Cânions • Trilhas para caminhada", 1, False),
        # Venues, casinos, bars and clubs of any kind (operator 10/2026; Arena Castelão).
        ("Arenas e estádios", 900, True),
        ("Centros de convenção", 50, True),
        ("Bares de karaokê", 12, True),
        ("Clubes de comédia", 30, True),
        ("Universidades e escolas", 40, True),
        ("Arenas e estádios • Pontos de interesse", 900, False),  # a place category stays
        ("", 0, False),
    ],
)
def test_is_not_atrativo(category: str, review_count: int, expected: bool) -> None:
    assert _is_not_atrativo({"category": category, "review_count": review_count}) is expected


def test_missing_fields_stay() -> None:
    assert _is_not_atrativo({}) is False


@pytest.mark.parametrize(
    ("name", "category", "expected"),
    [
        # Agencies that file one place-like activity too (real AL/DF/GO/ES cards, 10/2026).
        ("Sergio Tour: Passeios Privativos e Transfer Maceió", "Passeios urbanos • Mergulho com e sem snorkel", True),
        ("Sherpa Tour", "Pontes • Passeios de barco", True),
        ("Atlantes Viagens e Mergulho", "Mergulho com e sem snorkel • Excursões subaquáticas", True),
        ("Rafael Turismo Receptivo", "Centros turísticos", True),
        ("Andre Pichler Passeios Turísticos", "Praias • Passeios turísticos", True),
        # Operators with no agency word: every category an activity, one a sold service (RN).
        ("Jr Mossoró", "Mergulho com e sem snorkel • Excursões de off-road, quadriciclo e 4WD", True),
        ("Seabob Natal", "Mergulho com e sem snorkel • Locação de equipamentos", True),
        # Operator/studio names on a single activity category (RN/AL).
        ("Discovery Tur Natal", "Mergulho com e sem snorkel", True),
        ("Maracajaú Diver", "Mergulho com e sem snorkel", True),
        ("Yoga na Rota", "Ioga e pilates", True),
        # Sports clinics/schools are services, not places.
        ("Kauli Seadi Water Sports Club", "Clínicas e quadras esportivas • Surfe e windsurfe", True),
        # A park TA files under yoga keeps its place (no studio name, nothing sold).
        ("Bosque dos Namorados", "Ioga e pilates", False),
        # An agency-sounding name on a plain place category stays.
        ("Centro de Turismo", "Pontos de interesse", False),
        ("Praça de Apoio Ao Turismo", "Pontos de interesse", False),
        # A place with a diving category and no agency name stays.
        ("Piscinas Naturais de Ipioca", "Mergulho com e sem snorkel", False),
        ("Feira do Turista", "Lojas especializadas e de suvenir • Passeios urbanos", False),
        # "Passeio" alone is a place name (Passeio Público) unless a tour category backs it.
        ("Passeio Público", "Parques", False),
    ],
)
def test_agency_by_name_and_activity(name: str, category: str, expected: bool) -> None:
    assert _is_not_atrativo({"name": name, "category": category, "review_count": 50}) is expected
