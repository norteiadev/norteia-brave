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

Contract interactions (bodies built by build_push_payload — F53):
  1. "a valid destination Mar push" — POST /destinations with full Pact shape → 200
  2. "idempotent re-push of same source_ref" — same endpoint + payload → 200 (upsert)
  3. attraction pushes, one per lane shape (TA per-UF enriched, TA bulk, Places) → 200

After the tests run, the combined Pact JSON file is written.
"""

import asyncio
import json
import pathlib
from types import SimpleNamespace

import pytest

# pact-python 3.4.0 — Pact is at the top-level package, not pact.v3
from pact import Pact  # type: ignore[import-untyped]

from brave.clients.norteia_api import NorteiaApiClient
from brave.core.mar.service import build_push_payload

# ---------------------------------------------------------------------------
# Pact output directory
# ---------------------------------------------------------------------------

PACT_DIR = pathlib.Path(__file__).parent / "pacts"
PACT_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Fixture payloads — BUILT BY build_push_payload, never hand-written (F53)
# ---------------------------------------------------------------------------

# Flat ingestion contract (norteia-api is the source of truth for shape). Territory
# resolves by municipio_ibge → municipalities.ibge_code; the parent destino rides in
# `destino` for resolve-or-create; Google Places enrichment rides in `place` (lands in
# the separate attraction_place_details table). Provenance is flattened per-criterion.
#
# The bodies below come from the real build_push_payload over in-memory Mar/Rio stubs, one
# per lane shape, so a regression in the producer changes the frozen contract instead of
# passing next to a stale literal. The stubs mirror what each lane leaves in the canonical.

_BREAKDOWN = {
    "origem": 30.0, "completude": 20.0, "corroboracao": 20.0,
    "atualidade": 12.0, "validacao_humana": 8.0,
}

_RAW_HOURS = [
    "Monday: 8:00 AM – 6:00 PM",
    "Tuesday: 8:00 AM – 6:00 PM",
    "Wednesday: 8:00 AM – 6:00 PM",
    "Thursday: 8:00 AM – 6:00 PM",
    "Friday: 8:00 AM – 6:00 PM",
    "Saturday: 8:00 AM – 12:00 PM",
    "Sunday: Closed",
]


def _payload(entity_type: str, source_ref: str, canonical: dict, uf: str, score: float) -> dict:
    mar = SimpleNamespace(
        entity_type=entity_type,
        source_ref=source_ref,
        canonical=canonical,
        provenance={"score_breakdown": _BREAKDOWN},
        reliability_score=score,
    )
    return build_push_payload(mar, SimpleNamespace(uf=uf))


# Destino IBGE (ensure_destino → Mar).
DESTINATION_PAYLOAD = _payload(
    "destination",
    "ibge:BA:2927408",
    {"name": "Porto Seguro", "municipio": "Porto Seguro", "ibge_code": "2927408",
     # Mapa do Turismo Brasileiro. Frozen because the API has to declare a rule for it:
     # an unvalidated key is dropped by $request->validated() with no error.
     "participates_mtur": True},
    "BA",
    87.5,
)

# TripAdvisor per-UF lane, fully enriched (Places + description + distrito).
ATTRACTION_PAYLOAD = _payload(
    "attraction",
    "tripadvisor:attraction:2408107",
    {
        "name": "Cachoeira da Fumaca",
        "category": "Cachoeiras",  # C6: the card category becomes `type`
        "municipio": "Palmeiras",
        "municipio_id": "2923308",
        "parent_source_ref": "ibge:BA:2923308",
        "descricao_editorial": "Uma das quedas d'agua mais altas do Brasil, no coracao da Chapada.",
        "lat": -12.6,
        "lon": -41.4,
        "address": "Vale do Capao, s/n, Palmeiras - BA",
        "phone": "+55 73 99999-0001",  # celular → whatsapp
        "website": "https://example.com",
        "google_place_id": "ChIJexample_place_id",
        "weekday_text": _RAW_HOURS,
        "price_level": "PRICE_LEVEL_MODERATE",
        "signal": {"business_status": "OPERATIONAL", "reviews_recent_count": 12},
        "distrito_name": "Caete-Acu",
        "distrito_code": "292330810",
        "distrito_municipio_ibge": "2923308",
        "distrito_source": "places_admin_area_level_3",
    },
    "BA",
    90.0,
)

# TripAdvisor bulk_national lane, not yet enriched: no parent_source_ref (the destino falls
# back to the Rio's UF — never "ibge:attraction:…"), no Google data (no `place` block).
ATTRACTION_BULK_PAYLOAD = _payload(
    "attraction",
    "tripadvisor:attraction:311069",
    {
        "name": "Praia do Espelho",
        "category": "Praias",
        "municipio": "Porto Seguro",
        "municipio_id": "2925303",
        "parent_source_ref": None,
    },
    "BA",
    86.0,
)

# Places discovery lane: lane tipo, landline → telefone, instagram at the top level.
ATTRACTION_PLACES_PAYLOAD = _payload(
    "attraction",
    "places:BA:ChIJplaces_lane",
    {
        "name": "Museu de Arte da Bahia",
        "tipo": "museu",
        "municipio": "Salvador",
        "municipio_id": "2927408",
        "parent_source_ref": "ibge:BA:2927408",
        "phone": "+55 71 3117-6900",
        "instagram": "@museudeartedabahia",
        "website": "https://mab.example",
        "google_place_id": "ChIJplaces_lane",
    },
    "BA",
    91.0,
)

# Every key norteia-api's IngestAttractionRequest / IngestDestinationRequest validates. A key
# outside this set is silently DROPPED by $request->validated() — no 422, just lost data.
_API_ATTRACTION_KEYS = {
    "source_ref", "source", "name", "type", "municipio_ibge", "description", "latitude",
    "longitude", "address", "instagram", "whatsapp", "telefone", "website", "opening_hours",
    "accessibility", "how_to_get_there", "tips", "safety_alerts", "local_infrastructure",
    "curiosities", "reliability_score", "provenance", "visibility", "published_at",
    "destino", "place",
}
_API_DESTINO_KEYS = {"source_ref", "source", "tourist_name", "municipio_ibge",
                     "reliability_score", "provenance"}
_API_PLACE_KEYS = {
    "place_id", "business_status", "opening_hours", "price_level", "reviews_recent_count",
    "distrito_code", "distrito_name", "distrito_municipio_ibge", "subdistrito_name",
    "subdistrito_code", "distrito_source",
}
_API_DESTINATION_KEYS = {
    "source_ref", "source", "tourist_name", "slug", "municipio_ibge", "description",
    "reliability_score", "provenance", "participates_mtur", "visibility", "published_at",
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
# Test 3: Valid attraction Mar push — one interaction per lane shape
# ---------------------------------------------------------------------------


@pytest.mark.enable_socket  # pact-python mock server binds to localhost
@pytest.mark.parametrize(
    ("description", "payload"),
    [
        ("a valid attraction Mar push", ATTRACTION_PAYLOAD),
        ("a TripAdvisor bulk attraction Mar push (no place, UF-derived destino)",
         ATTRACTION_BULK_PAYLOAD),
        ("a Places-lane attraction Mar push", ATTRACTION_PLACES_PAYLOAD),
    ],
)
def test_push_attraction_contract(description, payload):
    """Pact: norteia-brave POSTs attraction Mar payload → 200 with id + source_ref."""
    pact = Pact("norteia-brave", "norteia-api")
    (
        pact.upon_receiving(description)
        .with_request("POST", "/api/internal/territorial/attractions")
        .with_header("Authorization", "Bearer test-service-token")
        .with_body(payload)
        .will_respond_with(200)
        .with_body({"id": 99, "created": True})
    )

    with pact.serve() as mock:
        client = NorteiaApiClient(base_url=str(mock.url), service_token="test-service-token")

        async def _run():
            async with client as c:
                return await c.push_attraction(payload)

        result = asyncio.run(_run())

    pact.write_file(str(PACT_DIR))

    assert "id" in result
    assert result["created"] is True


@pytest.mark.parametrize(
    "payload", [ATTRACTION_PAYLOAD, ATTRACTION_BULK_PAYLOAD, ATTRACTION_PLACES_PAYLOAD]
)
def test_every_produced_attraction_key_is_one_the_api_validates(payload):
    assert set(payload) <= _API_ATTRACTION_KEYS
    assert set(payload.get("destino", {})) <= _API_DESTINO_KEYS
    assert set(payload.get("place", {})) <= _API_PLACE_KEYS
    assert None not in payload.get("place", {}).values()  # F93: no null place keys


def test_every_produced_destination_key_is_one_the_api_validates():
    assert set(DESTINATION_PAYLOAD) <= _API_DESTINATION_KEYS


def test_lane_shapes_carry_the_contract_fixes():
    assert ATTRACTION_PAYLOAD["type"] == "cachoeira"
    assert ATTRACTION_BULK_PAYLOAD["type"] == "praia"
    assert ATTRACTION_BULK_PAYLOAD["destino"]["source_ref"] == "ibge:BA:2925303"  # F43
    assert "place" not in ATTRACTION_BULK_PAYLOAD  # F93
    assert ATTRACTION_PLACES_PAYLOAD["telefone"] and not ATTRACTION_PLACES_PAYLOAD["whatsapp"]
    assert ATTRACTION_PLACES_PAYLOAD["instagram"] == "@museudeartedabahia"
    for p in (DESTINATION_PAYLOAD, ATTRACTION_PAYLOAD, ATTRACTION_BULK_PAYLOAD):
        assert "visibility" not in p  # F47: a re-push never undoes an API-side hide


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
