"""Extraction schema: the API's enum and limits, seasonality as an array, stability per
column and the D3 clean-up of ``tips``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from brave.domains.editorial.schema import (
    DESTINATION_PUSHED,
    claim_drafts,
    parse_extraction,
    stability_of,
    strip_volatile_tips,
)

FIX = Path(__file__).resolve().parents[3] / "fixtures" / "editorial"


def _entity(kind: str = "destino", **fields) -> dict:
    return {
        "kind": kind,
        "name": "Anchieta",
        "municipio": "Anchieta",
        "fields": {k: {"value": v, "evidence": "trecho"} for k, v in fields.items()},
    }


def test_the_spike_output_for_anchieta_validates_as_is() -> None:
    raw = json.loads((FIX / "extraction-anchieta-es.json").read_text(encoding="utf-8"))
    entities, errors = parse_extraction(raw)
    assert errors == [] and len(entities) == 24
    anchieta = entities[0]
    assert anchieta.kind == "destino" and anchieta.name == "Anchieta"
    # the spike wrote seasonality as a sentence; the API wants an array
    assert anchieta.fields.seasonality.value == [
        raw["entities"][0]["fields"]["seasonality"]["value"]
    ]


@pytest.mark.parametrize(
    "field, value",
    [
        ("pace_profile", "moderado"),  # not lento|equilibrado|intenso
        ("recommended_duration", "x" * 101),
        ("estimated_spending_range", "x" * 101),
        ("connectivity_level", "x" * 51),
        ("predominant_audience_profile", "x" * 201),
    ],
)
def test_destination_values_outside_the_api_limits_are_rejected(field, value) -> None:
    entities, errors = parse_extraction({"entities": [_entity(**{field: value})]})
    assert entities == [] and len(errors) == 1 and field in errors[0]


def test_at_the_limit_is_accepted_and_pace_enum_values_pass() -> None:
    ok = _entity(
        recommended_duration="x" * 100,
        connectivity_level="x" * 50,
        predominant_audience_profile="x" * 200,
        pace_profile="equilibrado",
    )
    entities, errors = parse_extraction({"entities": [ok]})
    assert errors == [] and entities[0].fields.pace_profile.value == "equilibrado"


def test_seasonality_list_stays_a_list_and_empty_values_are_rejected() -> None:
    entities, _ = parse_extraction(
        {"entities": [_entity(seasonality=["jan a mar: chuva", "jul: seco"])]}
    )
    assert entities[0].fields.seasonality.value == ["jan a mar: chuva", "jul: seco"]
    _, errors = parse_extraction({"entities": [_entity(seasonality=[])]})
    assert errors


def test_one_bad_entity_does_not_drop_the_good_ones() -> None:
    raw = {
        "entities": [
            _entity(pace_profile="rapido"),
            _entity("atrativo", curiosities="Tombada em 1940."),
        ]
    }
    entities, errors = parse_extraction(raw)
    assert [e.kind for e in entities] == ["atrativo"] and len(errors) == 1


def test_unknown_columns_and_stability_hints_are_ignored() -> None:
    e = _entity("atrativo", curiosities="Fato.")
    e["fields"]["curiosities"]["stability"] = "volatil"  # the code decides, not the model
    e["fields"]["foo"] = {"value": "x", "evidence": "y"}
    entities, errors = parse_extraction({"entities": [e]})
    assert errors == [] and entities[0].fields.curiosities.value == "Fato."


def test_a_destination_column_on_an_attraction_is_not_read() -> None:
    entities, _ = parse_extraction({"entities": [_entity("atrativo", best_time_to_visit="verão")]})
    assert [d.field for d in claim_drafts(entities[0])] == []


def test_stability_is_fixed_per_column() -> None:
    for volatile in ("free_entry", "price", "opening_hours", "estimated_capacity",
                     "conservation_level", "safety_alerts"):  # fmt: skip
        assert stability_of(volatile) == "volatil"
    for stable in ("curiosities", "how_to_get_there", "local_infrastructure", "tips",
                   "accessibility", "best_time_to_visit", "seasonality", "pace_profile"):  # fmt: skip
        assert stability_of(stable) == "estavel"
    assert "safety_alerts" not in DESTINATION_PUSHED


def test_claim_drafts_cover_every_filled_column_with_its_stability() -> None:
    raw = _entity(
        "atrativo",
        curiosities="Fato.",
        opening_hours="Das 8h às 18h.",
        free_entry=True,
        price="R$ 10",
    )
    drafts = {d.field: d for d in claim_drafts(parse_extraction({"entities": [raw]})[0][0])}
    assert set(drafts) == {"curiosities", "opening_hours", "free_entry", "price"}
    assert drafts["free_entry"].value is True and drafts["free_entry"].stability == "volatil"
    assert (
        drafts["curiosities"].stability == "estavel" and drafts["curiosities"].evidence == "trecho"
    )


@pytest.mark.parametrize(
    "tips, expected",
    [
        ("Entrada R$ 10. A vista compensa o passeio.", "A vista compensa o passeio."),
        ("O pedágio de R$ 2 é cobrado na ponte. Evite o feriado.", "Evite o feriado."),
        ("Abre das 8h às 18h. Leve água.", "Leve água."),
        ("Funciona de terça a domingo, das 8:00 às 18:00. Vá cedo.", "Vá cedo."),
        ("Limite de 20 pessoas por dia. Chegue cedo.", "Chegue cedo."),
        ("São no máximo 6 pessoas por grupo. Leve calçado fechado.", "Leve calçado fechado."),
        (
            "A trilha leva 2 horas e é pesada. Use bastão.",
            "A trilha leva 2 horas e é pesada. Use bastão.",
        ),
        (
            "O Mercado dos Peixes tem preços acessíveis.",
            "O Mercado dos Peixes tem preços acessíveis.",
        ),
        ("Entrada R$ 10. Abre das 8h às 18h.", ""),
    ],
)
def test_tips_lose_prices_opening_hours_and_people_limits(tips, expected) -> None:
    assert strip_volatile_tips(tips) == expected


def test_a_tip_left_empty_by_the_clean_up_yields_no_claim() -> None:
    raw = _entity("atrativo", tips="Entrada R$ 10. Abre das 8h às 18h.", curiosities="Fato.")
    assert [d.field for d in claim_drafts(parse_extraction({"entities": [raw]})[0][0])] == [
        "curiosities"
    ]
