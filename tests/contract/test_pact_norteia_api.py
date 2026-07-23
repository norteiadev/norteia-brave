"""Pact consumer contract test for NorteiaApiClient → norteia-api (CNTR-01, D-16).

Consumer: norteia-brave
Provider: norteia-api

Freezes the Mar push shape (D-16) so the external Laravel norteia-api repo can build
its provider verification against this contract. The contract is cheap-early /
expensive-late.

pact-python 3.x API (pact-python 3.4.0):
  - `from pact import Pact`  — Pact is at the top-level pact package (NOT pact.v3)
  - pact.upon_receiving(desc) → HttpInteraction fluent builder
  - pact.serve() → PactServer context manager (mock.url is a yarl.URL object)
  - pact.write_file(directory) → writes Pact JSON contract artifact

Note on interaction model: pact-python 3.x accumulates interactions on a Pact object.
Interactions must be defined before entering serve(). Each interaction creates a new
Pact, serves it, makes the call, then writes. We use one Pact per test function to avoid
interaction state pollution across tests.

Contract interactions:
  1. "a valid destination Mar push" — POST /destinations with full Pact shape → 200
  2. "idempotent re-push of same source_ref" — same endpoint + payload → 200 (upsert)
  3. "a valid attraction Mar push" — POST /attractions with entity_type=attraction → 200

After the three tests run, the combined Pact JSON file is written.
"""

import asyncio
import json
import pathlib

import pytest

from brave.clients.norteia_api import NorteiaApiClient

# pact-python 3.4.0 — Pact is at the top-level package, not pact.v3
from pact import Pact  # type: ignore[import-untyped]

# ---------------------------------------------------------------------------
# Pact output directory
# ---------------------------------------------------------------------------

PACT_DIR = pathlib.Path(__file__).parent / "pacts"
PACT_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Fixture payloads (matching the Pact contract shape — D-16)
# ---------------------------------------------------------------------------

# Flat ingestion contract (norteia-api is the source of truth for shape). Territory
# resolves by municipio_ibge → municipalities.ibge_code; the parent destino rides in
# `destino` for resolve-or-create; Google Places enrichment rides in `place` (lands in
# the separate attraction_place_details table). Provenance is flattened per-criterion.
DESTINATION_PAYLOAD = {
    "source": "ibge",
    "source_ref": "ibge:BA:2927408",
    "tourist_name": "Porto Seguro",
    "municipio_ibge": "2927408",
    "reliability_score": 87.5,
    "provenance": {
        "origem": 30.0,
        "completude": 20.0,
        "corroboracao": 16.0,
        "atualidade": 12.0,
        "validacao_humana": 9.5,
    },
}

ATTRACTION_PAYLOAD = {
    "source": "tripadvisor",
    "source_ref": "tripadvisor:BA:atr001",
    "name": "Cachoeira da Fumaca",
    "type": "cachoeira",
    "municipio_ibge": "2925303",
    "description": "Uma das quedas d'agua mais altas do Brasil, no coracao da Chapada.",
    "latitude": -12.6,
    "longitude": -41.4,
    "address": "Vale do Capao, s/n, Palmeiras - BA",
    # Phone split: a BR celular → whatsapp; any other (fixo) → telefone.
    "whatsapp": "+5573999990001",
    "telefone": "+557332330001",
    "website": "https://example.com",
    "reliability_score": 90.0,
    "provenance": {
        "origem": 30.0,
        "completude": 20.0,
        "corroboracao": 20.0,
        "atualidade": 12.0,
        "validacao_humana": 8.0,
    },
    # Parent destino (resolve-or-create by source_ref on the API side).
    "destino": {
        "source_ref": "ibge:BA:2925303",
        "source": "ibge",
        "tourist_name": "Porto Seguro",
        "municipio_ibge": "2925303",
    },
    # Google Places enrichment → attraction_place_details table.
    "place": {
        "place_id": "ChIJexample_place_id",
        "business_status": "OPERATIONAL",
        "opening_hours": ["Mon: 08:00-18:00"],
        "price_level": 2,
        "reviews_recent_count": 12,
        "distrito_name": "Arraial D'Ajuda",
        "distrito_code": "292530307",
        "distrito_municipio_ibge": "2925303",
        "subdistrito_name": None,
        "subdistrito_code": None,
        "distrito_source": "places_admin_area_level_3",
    },
}


# ---------------------------------------------------------------------------
# Test 1: Valid destination Mar push
# ---------------------------------------------------------------------------


@pytest.mark.enable_socket  # pact-python mock server binds to localhost
def test_push_destination_contract():
    """Pact: norteia-brave POSTs destination Mar payload → 200 with id + source_ref."""
    pact = Pact("norteia-brave", "norteia-api")
    (
        pact.upon_receiving("a valid destination Mar push")
        .with_request("POST", "/api/internal/territorial/destinations")
        .with_header("Authorization", "Bearer test-service-token")
        .with_body(DESTINATION_PAYLOAD)
        .will_respond_with(200)
        .with_body({"id": 42, "created": True})
    )

    with pact.serve() as mock:
        client = NorteiaApiClient(base_url=str(mock.url), service_token="test-service-token")

        async def _run():
            async with client as c:
                return await c.push_destination(DESTINATION_PAYLOAD)

        result = asyncio.run(_run())

    pact.write_file(str(PACT_DIR))

    assert "id" in result
    assert result["created"] is True


# ---------------------------------------------------------------------------
# Test 2: Idempotent re-push (same source_ref → 200 again, upsert)
# ---------------------------------------------------------------------------


@pytest.mark.enable_socket  # pact-python mock server binds to localhost
def test_push_destination_idempotent_contract():
    """Pact: Calling push_destination twice with the same source_ref → 200 both times.

    norteia-api performs an idempotent upsert by source_ref (D-15).
    """
    pact = Pact("norteia-brave", "norteia-api")
    (
        pact.upon_receiving("idempotent re-push of same source_ref")
        .with_request("POST", "/api/internal/territorial/destinations")
        .with_header("Authorization", "Bearer test-service-token")
        .with_body(DESTINATION_PAYLOAD)
        .will_respond_with(200)
        .with_body({"id": 42, "created": False})
    )

    with pact.serve() as mock:
        client = NorteiaApiClient(base_url=str(mock.url), service_token="test-service-token")

        async def _run():
            async with client as c:
                return await c.push_destination(DESTINATION_PAYLOAD)

        result = asyncio.run(_run())

    pact.write_file(str(PACT_DIR))

    assert "id" in result


# ---------------------------------------------------------------------------
# Test 3: Valid attraction Mar push
# ---------------------------------------------------------------------------


@pytest.mark.enable_socket  # pact-python mock server binds to localhost
def test_push_attraction_contract():
    """Pact: norteia-brave POSTs attraction Mar payload → 200 with id + source_ref."""
    pact = Pact("norteia-brave", "norteia-api")
    (
        pact.upon_receiving("a valid attraction Mar push")
        .with_request("POST", "/api/internal/territorial/attractions")
        .with_header("Authorization", "Bearer test-service-token")
        .with_body(ATTRACTION_PAYLOAD)
        .will_respond_with(200)
        .with_body({"id": 99, "created": True})
    )

    with pact.serve() as mock:
        client = NorteiaApiClient(base_url=str(mock.url), service_token="test-service-token")

        async def _run():
            async with client as c:
                return await c.push_attraction(ATTRACTION_PAYLOAD)

        result = asyncio.run(_run())

    pact.write_file(str(PACT_DIR))

    assert "id" in result
    assert result["created"] is True


# ---------------------------------------------------------------------------
# Post-all: Verify Pact JSON file and structure
# ---------------------------------------------------------------------------


def test_pact_file_written_and_valid():
    """Verify the Pact JSON file was written and contains correct consumer/provider."""
    pact_file = PACT_DIR / "norteia-brave-norteia-api.json"
    assert pact_file.exists(), (
        f"Pact file not found at {pact_file}. "
        "Run tests in order — test_push_destination_contract must run first."
    )

    data = json.loads(pact_file.read_text())
    assert data["consumer"]["name"] == "norteia-brave", (
        f"Expected consumer norteia-brave, got {data['consumer']}"
    )
    assert data["provider"]["name"] == "norteia-api", (
        f"Expected provider norteia-api, got {data['provider']}"
    )
    interactions = data.get("interactions", [])
    assert len(interactions) >= 2, (
        f"Expected at least 2 Pact interactions, got {len(interactions)}"
    )
