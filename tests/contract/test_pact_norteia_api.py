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

# D-10 (Phase 2): ibge_code added to canonical to support IBGE → municipality_id
# resolution in norteia-api. Breaking change from Phase 1 frozen contract — coordinate
# with norteia-api Laravel team (Trilha 5) to update provider verification.
# source_ref format updated to "mtur:{uf}:{ibge_code}" (IBGE code replaces sequential id).
DESTINATION_PAYLOAD = {
    "source": "mtur",
    "source_ref": "mtur:BA:2927408",
    "entity_type": "destination",
    "canonical": {
        "name": "Trancoso",
        "uf": "BA",
        "municipio": "Porto Seguro",
        "ibge_code": "2927408",
    },
    "reliability_score": 87.5,
    "score_version": "v1.0",
    "provenance": {
        "origem": 30.0,
        "completude": 20.0,
        "corroboracao": 16.0,
        "atualidade": 12.0,
        "validacao_humana": 9.5,
    },
}

ATTRACTION_PAYLOAD = {
    "source": "mtur",
    "source_ref": "mtur:BA:atr:001",
    "entity_type": "attraction",
    "canonical": {
        "name": "Chapada Diamantina",
        "uf": "BA",
        "municipio": "Lencois",
        # Curated editorial description in the Norteia voice (DescriptionEnrichmentAgent).
        # Schemaless in the medallion; documents the new key on the wire so the
        # norteia-api (Laravel) ingestion can accept/persist it. Cross-repo: coordinate
        # provider verification (precedent: ibge_code on the destination payload).
        "descricao_editorial": "Um dos maiores parques nacionais da Bahia, a Chapada "
        "Diamantina reune cachoeiras, grutas e trilhas em um cenario de serras.",
        # Distrito/subdistrito localization (IBGE DTB 2025), resolved from the Places
        # administrative_area_level_3 text name-matched against the município's distritos
        # (brave.shared.ibge_distritos.resolve_distrito). Public geo-territorial fields
        # (same class as municipio/ibge_code) — schemaless passthrough, documented on the
        # wire so norteia-api (Laravel) ingestion can persist them. subdistrito_* are
        # reserved keys (Google returns no admin_area_level_4) → always null for now.
        # Cross-repo: coordinate provider verification (precedent: ibge_code / descricao_editorial).
        "distrito_name": "Arraial D'Ajuda",
        "distrito_code": "292530307",
        "subdistrito_name": None,
        "subdistrito_code": None,
    },
    "reliability_score": 90.0,
    "score_version": "v1.0",
    "provenance": {
        "origem": 30.0,
        "completude": 20.0,
        "corroboracao": 20.0,
        "atualidade": 12.0,
        "validacao_humana": 8.0,
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
        .with_body({"id": "550e8400-e29b-41d4-a716-446655440000", "source_ref": "mtur:BA:2927408"})
    )

    with pact.serve() as mock:
        client = NorteiaApiClient(base_url=str(mock.url), service_token="test-service-token")

        async def _run():
            async with client as c:
                return await c.push_destination(DESTINATION_PAYLOAD)

        result = asyncio.run(_run())

    pact.write_file(str(PACT_DIR))

    assert "source_ref" in result
    assert result["source_ref"] == "mtur:BA:2927408"


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
        .with_body({"id": "550e8400-e29b-41d4-a716-446655440001", "source_ref": "mtur:BA:2927408"})
    )

    with pact.serve() as mock:
        client = NorteiaApiClient(base_url=str(mock.url), service_token="test-service-token")

        async def _run():
            async with client as c:
                return await c.push_destination(DESTINATION_PAYLOAD)

        result = asyncio.run(_run())

    pact.write_file(str(PACT_DIR))

    assert "source_ref" in result


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
        .with_body({"id": "550e8400-e29b-41d4-a716-446655440002", "source_ref": "mtur:BA:atr:001"})
    )

    with pact.serve() as mock:
        client = NorteiaApiClient(base_url=str(mock.url), service_token="test-service-token")

        async def _run():
            async with client as c:
                return await c.push_attraction(ATTRACTION_PAYLOAD)

        result = asyncio.run(_run())

    pact.write_file(str(PACT_DIR))

    assert "source_ref" in result
    assert result["source_ref"] == "mtur:BA:atr:001"


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
