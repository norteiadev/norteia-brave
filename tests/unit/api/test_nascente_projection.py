"""Unit tests for the Nascente list projection helper (quick-260701-kiy).

Tests `_project_nascente_item` in isolation — a pure, DB-free projection of a
single NascenteRecord into the LGPD field allow-list surfaced by
GET /api/v1/nascente. This plan adds `municipio` (público nome) and
`municipio_id` (IBGE code) to that allow-list, both null-safe.

No DB, no respx, no network — the helper takes a record-like object and returns
a plain dict, so we drive it with types.SimpleNamespace fakes.
"""

from __future__ import annotations

import types

from brave.api.routers.engine import _project_nascente_item


def _rec(**overrides) -> types.SimpleNamespace:
    """A minimal NascenteRecord stand-in (only the fields the helper reads)."""
    base = {
        "id": "11111111-1111-1111-1111-111111111111",
        "entity_type": "attraction",
        "uf": "ES",
        "source": "tripadvisor",
        "source_ref": "ta:12345",
        "ingested_at": None,
        "payload": {},
    }
    base.update(overrides)
    return types.SimpleNamespace(**base)


def test_ta_atrativo_surfaces_municipio_and_municipio_id():
    rec = _rec(
        payload={
            "canonical": {"municipio": "Vila Velha"},
            "municipio_id": "3205200",
            "name": "Convento da Penha",
        },
    )
    item = _project_nascente_item(rec)
    assert item["municipio"] == "Vila Velha"
    assert item["municipio_id"] == "3205200"
    # existing allow-list fields still project correctly
    assert item["id"] == "11111111-1111-1111-1111-111111111111"
    assert item["entity_type"] == "attraction"
    assert item["uf"] == "ES"
    assert item["source"] == "tripadvisor"
    assert item["name"] == "Convento da Penha"


def test_mtur_destino_surfaces_municipio():
    rec = _rec(
        entity_type="destination",
        source="mtur",
        payload={
            "canonical": {"municipio": "Ouro Preto"},
            "municipio_id": "3146107",
            "name": "Ouro Preto",
        },
    )
    item = _project_nascente_item(rec)
    assert item["municipio"] == "Ouro Preto"
    assert item["municipio_id"] == "3146107"
    assert item["entity_type"] == "destination"


def test_missing_canonical_yields_none_municipio_no_keyerror():
    rec = _rec(payload={"municipio_id": "3205200", "name": "X"})
    item = _project_nascente_item(rec)
    assert item["municipio"] is None
    assert item["municipio_id"] == "3205200"


def test_missing_municipio_id_yields_none():
    rec = _rec(payload={"canonical": {"municipio": "Vitória"}, "name": "X"})
    item = _project_nascente_item(rec)
    assert item["municipio"] == "Vitória"
    assert item["municipio_id"] is None


def test_empty_payload_both_none_no_crash():
    rec = _rec(payload={})
    item = _project_nascente_item(rec)
    assert item["municipio"] is None
    assert item["municipio_id"] is None
    # name falls back to source_ref when payload has no name
    assert item["name"] == "ta:12345"


def test_none_payload_both_none_no_crash():
    rec = _rec(payload=None)
    item = _project_nascente_item(rec)
    assert item["municipio"] is None
    assert item["municipio_id"] is None
    assert item["name"] == "ta:12345"


def test_none_canonical_no_crash():
    rec = _rec(payload={"canonical": None, "municipio_id": "3205200", "name": "X"})
    item = _project_nascente_item(rec)
    assert item["municipio"] is None
    assert item["municipio_id"] == "3205200"
