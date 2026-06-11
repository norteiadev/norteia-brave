"""Integration tests for NorteiaApiClient — Mar push shape, headers, retry, idempotency.

Tests use respx to intercept httpx calls (no real network).
All tests run with --disable-socket for offline CI compliance (TEST-01, PITFALLS §5).

Coverage:
  - push_destination happy path (200 OK)
  - Bearer Authorization header is asserted
  - push_destination on 5xx raises httpx.HTTPStatusError
  - Idempotent double-push: same source_ref → 200 both times (norteia-api handles upsert)
  - push_attraction routes to /api/internal/territorial/attractions
"""

import json

import httpx
import pytest
import respx

# Mark integration for CI purposes
pytestmark = pytest.mark.integration

BASE_URL = "https://api.norteia.example"
SERVICE_TOKEN = "test-service-token-abc123"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def api_client():
    """NorteiaApiClient instance with known base_url and service_token."""
    from brave.clients.norteia_api import NorteiaApiClient

    return NorteiaApiClient(base_url=BASE_URL, service_token=SERVICE_TOKEN)


@pytest.fixture
def destination_payload():
    """Flat-provenance Mar push payload (Pact contract shape)."""
    return {
        "source": "mtur",
        "source_ref": "mtur:BA:123",
        "entity_type": "destination",
        "canonical": {
            "name": "Praia do Forte",
            "uf": "BA",
            "municipio": "Mata de Sao Joao",
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


# ---------------------------------------------------------------------------
# Test 1: push_destination happy path
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@respx.mock
async def test_push_destination_happy_path(api_client, destination_payload):
    """push_destination returns the response dict on 200 OK."""
    expected_response = {"id": "uuid-123", "source_ref": "mtur:BA:123"}
    mock_route = respx.post(
        f"{BASE_URL}/api/internal/territorial/destinations"
    ).mock(return_value=httpx.Response(200, json=expected_response))

    async with api_client as client:
        result = await client.push_destination(destination_payload)

    assert result == expected_response
    assert mock_route.called


# ---------------------------------------------------------------------------
# Test 2: Bearer Authorization header is sent
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@respx.mock
async def test_push_destination_bearer_auth_header(api_client, destination_payload):
    """push_destination sends Authorization: Bearer {service_token} header."""
    expected_response = {"id": "uuid-456", "source_ref": "mtur:BA:123"}
    respx.post(
        f"{BASE_URL}/api/internal/territorial/destinations"
    ).mock(return_value=httpx.Response(200, json=expected_response))

    async with api_client as client:
        await client.push_destination(destination_payload)

    # Verify the request had the Bearer token
    call = respx.calls[0]
    auth_header = call.request.headers.get("authorization", "")
    assert auth_header == f"Bearer {SERVICE_TOKEN}", (
        f"Expected 'Bearer {SERVICE_TOKEN}', got {auth_header!r}"
    )


# ---------------------------------------------------------------------------
# Test 3: 5xx raises httpx.HTTPStatusError
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@respx.mock
async def test_push_destination_5xx_raises(api_client, destination_payload):
    """push_destination raises httpx.HTTPStatusError on 5xx after retries exhausted."""
    respx.post(
        f"{BASE_URL}/api/internal/territorial/destinations"
    ).mock(return_value=httpx.Response(503, json={"error": "Service Unavailable"}))

    with pytest.raises(httpx.HTTPStatusError):
        async with api_client as client:
            await client.push_destination(destination_payload)


# ---------------------------------------------------------------------------
# Test 4: Idempotent double-push
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@respx.mock
async def test_push_destination_idempotent_double_push(api_client, destination_payload):
    """push_destination called twice with same source_ref succeeds both times.

    norteia-api handles upsert by source_ref; the client has no guard.
    Both calls return 200.
    """
    expected_response = {"id": "uuid-789", "source_ref": "mtur:BA:123"}
    route = respx.post(
        f"{BASE_URL}/api/internal/territorial/destinations"
    ).mock(return_value=httpx.Response(200, json=expected_response))

    async with api_client as client:
        result1 = await client.push_destination(destination_payload)
        result2 = await client.push_destination(destination_payload)

    assert result1 == expected_response
    assert result2 == expected_response
    assert route.call_count == 2


# ---------------------------------------------------------------------------
# Test 5: push_attraction routes to the attractions endpoint
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@respx.mock
async def test_push_attraction_routes_to_correct_endpoint(api_client):
    """push_attraction sends POST to /api/internal/territorial/attractions."""
    attraction_payload = {
        "source": "mtur",
        "source_ref": "mtur:BA:atr:001",
        "entity_type": "attraction",
        "canonical": {
            "name": "Chapada Diamantina",
            "uf": "BA",
            "municipio": "Lencois",
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
    expected_response = {"id": "uuid-atr-001", "source_ref": "mtur:BA:atr:001"}
    mock_route = respx.post(
        f"{BASE_URL}/api/internal/territorial/attractions"
    ).mock(return_value=httpx.Response(200, json=expected_response))

    async with api_client as client:
        result = await client.push_attraction(attraction_payload)

    assert result == expected_response
    assert mock_route.called
    # Ensure destination endpoint was NOT called
    assert not respx.calls.call_count or all(
        "/attractions" in str(c.request.url) for c in respx.calls
    )


# ---------------------------------------------------------------------------
# Test 6: push_mar Celery task wires to NorteiaApiClient
# ---------------------------------------------------------------------------


def test_push_mar_imports_norteia_api_client():
    """push_mar Celery task imports NorteiaApiClient from brave.clients.norteia_api."""
    import brave.tasks.pipeline as pipeline_module
    import inspect

    source = inspect.getsource(pipeline_module)
    assert "from brave.clients.norteia_api import NorteiaApiClient" in source, (
        "push_mar must import NorteiaApiClient from brave.clients.norteia_api"
    )
