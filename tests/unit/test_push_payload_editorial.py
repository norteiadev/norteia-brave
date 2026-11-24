"""build_push_payload carries the editorial attraction columns — only the filled stable ones —
and an attraction WITHOUT editorial data keeps today's payload byte for byte (same push_hash)."""

from __future__ import annotations

from types import SimpleNamespace

from brave.core.mar.publication import _digest
from brave.core.mar.service import build_push_payload

BASE = {"name": "Pedra do Frade", "municipio_id": "3205200", "lat": -20.1, "lon": -40.5}


def _mar(canonical: dict) -> SimpleNamespace:
    return SimpleNamespace(
        entity_type="attraction",
        source_ref="tripadvisor:attraction:1",
        canonical=canonical,
        provenance={"score_breakdown": {}},
        reliability_score=87.5,
    )


def test_filled_editorial_columns_ride_in_the_payload() -> None:
    editorial = {
        "curiosities": "Tombada em 1940.",
        "how_to_get_there": "Pela ES-060.",
        "tips": "Leve água.",
        "local_infrastructure": "Tem banheiro.",
        "accessibility": "Rampa na entrada.",
    }
    payload = build_push_payload(_mar({**BASE, "editorial": editorial}), None)
    for key, value in editorial.items():
        assert payload[key] == value
    assert "editorial" not in payload  # the namespace never leaks as a key


def test_only_the_filled_columns_are_sent_never_null_or_blank() -> None:
    payload = build_push_payload(
        _mar(
            {
                **BASE,
                "editorial": {
                    "curiosities": "Fato.",
                    "tips": "",
                    "accessibility": "  ",
                    "how_to_get_there": None,
                },
            }
        ),
        None,
    )
    assert payload["curiosities"] == "Fato."
    assert not {"tips", "accessibility", "how_to_get_there", "local_infrastructure"} & set(payload)


def test_volatile_or_unknown_editorial_keys_are_never_pushed() -> None:
    editorial = {
        "curiosities": "Fato.",
        "price": "R$ 10",
        "opening_hours": "8h",
        "free_entry": True,
        "safety_alerts": "x",
    }
    payload = build_push_payload(_mar({**BASE, "editorial": editorial}), None)
    assert payload["curiosities"] == "Fato."
    assert "price" not in payload and "free_entry" not in payload and "safety_alerts" not in payload
    assert "opening_hours" not in payload  # only a parsed Google hours map may fill this key


def test_an_attraction_without_editorial_has_the_same_payload_and_hash_as_before() -> None:
    without = build_push_payload(_mar(dict(BASE)), None)
    for empty in ({}, None):
        with_empty = build_push_payload(_mar({**BASE, "editorial": empty}), None)
        assert with_empty == without
        assert _digest(with_empty) == _digest(without)
    assert not {
        "curiosities",
        "tips",
        "how_to_get_there",
        "local_infrastructure",
        "accessibility",
    } & set(without)


def test_destination_payload_is_untouched() -> None:
    mar = SimpleNamespace(
        entity_type="destination",
        source_ref="ibge:ES:3205200",
        canonical={"name": "Vila Velha", "ibge_code": "3205200", "editorial": {"curiosities": "x"}},
        provenance={"score_breakdown": {}},
        reliability_score=80,
    )
    assert "curiosities" not in build_push_payload(mar, None)
