"""Editorial matching: KML coordinate first, name-only inside the municipality as fallback.

Pure: candidates are plain dataclasses (the resolver loads them from rio_records)."""

from __future__ import annotations

from brave.domains.editorial.matching import (
    Municipio,
    RioCandidate,
    match_attraction,
    match_destination,
)

LAT, LON = -20.1000, -40.5000
# 0.001 deg of latitude ~ 111 m
M_100 = (LAT - 0.0009, LON)  # ~100 m
M_250 = (LAT - 0.00225, LON)  # ~250 m
M_400 = (LAT - 0.0036, LON)  # ~400 m
M_5KM = (LAT - 0.045, LON)


def cand(
    key: str, name: str, at: tuple[float, float] | None, municipio: str = "Pancas"
) -> RioCandidate:
    return RioCandidate(key, name, at[0] if at else None, at[1] if at else None, municipio)


def point(name: str, at: tuple[float, float] = (LAT, LON)) -> dict:
    return {"name": name, "lat": at[0], "lon": at[1]}


def test_kml_point_matches_the_rio_attraction_within_300m() -> None:
    m = match_attraction(
        "Pedra do Frade", "Pancas", [point("Pedra do Frade")], [cand("a", "Pedra do Frade", M_100)]
    )
    assert (m.status, m.canonical_key, m.method) == ("matched", "a", "kml_coord")
    assert m.score == 1.0 and (m.lat, m.lon) == (LAT, LON)


def test_250m_still_matches_and_400m_does_not() -> None:
    near = match_attraction(
        "Pedra do Frade", None, [point("Pedra do Frade")], [cand("a", "Pedra do Frade", M_250)]
    )
    far = match_attraction(
        "Pedra do Frade", None, [point("Pedra do Frade")], [cand("a", "Pedra do Frade", M_400)]
    )
    assert near.status == "matched" and far.status == "unmatched"


def test_two_compatible_candidates_near_the_point_are_ambiguous() -> None:
    m = match_attraction(
        "Praia da Areia Preta",
        None,
        [point("Praia da Areia Preta")],
        [cand("a", "Praia da Areia Preta", M_100), cand("b", "Praia da Areia Preta Norte", M_250)],
    )
    assert m.status == "ambiguous" and m.canonical_key is None
    assert set(m.candidates) == {"a", "b"}


def test_no_candidate_near_the_point_is_unmatched_but_keeps_the_point_coordinates() -> None:
    m = match_attraction("Pedra do Frade", None, [point("Pedra do Frade")], [])
    assert m.status == "unmatched" and (m.lat, m.lon) == (LAT, LON)


def test_near_but_incompatible_name_is_not_a_match() -> None:
    m = match_attraction(
        "Pedra do Frade", None, [point("Pedra do Frade")], [cand("a", "Cachoeira da Fumaça", M_100)]
    )
    assert m.status == "unmatched"


def test_a_candidate_without_coordinates_cannot_match_by_coordinate() -> None:
    m = match_attraction(
        "Pedra do Frade", "Pancas", [point("Pedra do Frade")], [cand("a", "Pedra do Frade", None)]
    )
    assert m.status == "unmatched"


def test_the_kml_label_may_differ_from_the_text_name() -> None:
    # text says "Santuário Nacional de São José de Anchieta", the map says "Santuário de São José de Anchieta"
    m = match_attraction(
        "Santuário Nacional de São José de Anchieta",
        "Anchieta",
        [point("Santuário de São José de Anchieta")],
        [cand("a", "Santuário de São José de Anchieta", M_100, "Anchieta")],
    )
    assert m.status == "matched"


def test_two_equally_named_points_with_a_candidate_each_are_ambiguous() -> None:
    m = match_attraction(
        "Praia do Morro",
        None,
        [point("Praia do Morro"), point("Praia do Morro", M_5KM)],
        [
            cand("a", "Praia do Morro", M_100),
            cand("b", "Praia do Morro", (M_5KM[0] - 0.0009, M_5KM[1])),
        ],
    )
    assert m.status == "ambiguous"


def test_without_a_compatible_point_it_falls_back_to_name_within_the_municipality() -> None:
    m = match_attraction(
        "Pedra do Frade", "Pancas", [], [cand("a", "Pedra do Frade", M_5KM, "Pancas")]
    )
    assert (m.status, m.canonical_key, m.method) == ("matched", "a", "name_municipio")
    other = match_attraction(
        "Pedra do Frade",
        "Pancas",
        [point("Outro Lugar")],
        [cand("a", "Pedra do Frade", M_5KM, "Pancas")],
    )
    assert other.status == "matched"  # no COMPATIBLE point: still the name-only path


def test_the_municipality_named_in_the_text_never_decides_alone() -> None:
    # same name in ANOTHER municipality
    elsewhere = match_attraction(
        "Pedra do Frade", "Pancas", [], [cand("a", "Pedra do Frade", None, "Colatina")]
    )
    assert elsewhere.status == "unmatched"
    # right municipality, weak name (0.75: compatible by coordinate, below the name-only cutoff)
    weak = match_attraction(
        "Praia da Areia Preta",
        "Pancas",
        [],
        [cand("a", "Praia da Areia Preta Norte", None, "Pancas")],
    )
    assert weak.status == "unmatched"
    # no municipality in the text: name-only has nothing to scope with
    assert (
        match_attraction("Pedra do Frade", None, [], [cand("a", "Pedra do Frade", None)]).status
        == "unmatched"
    )


def test_the_text_municipality_cannot_rescue_a_compatible_point_with_nothing_near_it() -> None:
    m = match_attraction(
        "Pedra do Frade",
        "Pancas",
        [point("Pedra do Frade")],
        [cand("a", "Pedra do Frade", M_5KM, "Pancas")],
    )
    assert m.status == "unmatched"


def test_the_coordinate_wins_over_a_wrong_municipality_in_the_text() -> None:
    m = match_attraction(
        "Pedra do Frade",
        "Vitória",
        [point("Pedra do Frade")],
        [cand("a", "Pedra do Frade", M_100, "Pancas")],
    )
    assert (m.status, m.canonical_key) == ("matched", "a")


def test_two_same_name_candidates_in_the_municipality_are_ambiguous() -> None:
    m = match_attraction(
        "Pedra do Frade",
        "Pancas",
        [],
        [cand("a", "Pedra do Frade", None), cand("b", "Pedra do Frade", None)],
    )
    assert m.status == "ambiguous"


MUNICIPIOS = [
    Municipio("3200201", "Afonso Cláudio"),
    Municipio("3205309", "Vitória"),
    Municipio("3205200", "Vila Velha"),
]


def test_destination_matches_the_municipality_by_name_to_its_ibge() -> None:
    m = match_destination("Vitoria", MUNICIPIOS)
    assert (m.status, m.canonical_key, m.method) == ("matched", "3205309", "municipio_ibge")
    assert match_destination("Afonso Claudio", MUNICIPIOS).canonical_key == "3200201"


def test_destination_unknown_or_duplicated_name() -> None:
    assert match_destination("Atlântida", MUNICIPIOS).status == "unmatched"
    dup = [*MUNICIPIOS, Municipio("9999999", "Vitória")]
    assert match_destination("Vitória", dup).status == "ambiguous"
