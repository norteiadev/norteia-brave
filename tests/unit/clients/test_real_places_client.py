"""Offline unit tests for RealPlacesClient (D-08, Phase 7).

100% offline — no real Places API calls, no network (TEST-01).

Tests:
  T1 — guard: RuntimeError when run_real_externals=False
  T2 — text_search sends x-goog-fieldmask with 'places.' prefix + 'addressComponents'
  T3 — place_details sends x-goog-fieldmask WITHOUT 'places.' prefix + 'regularOpeningHours'
  T4 — addressComponents → municipio_nome + municipio_ibge via ibge_lookup
  T5 — review.publish_time proto Timestamp converted safely via ToDatetime()
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Proto-like fixture builders (module-level, not fixtures)
# ---------------------------------------------------------------------------


def _make_search_response(municipio: str = "Porto Seguro", uf_short: str = "BA") -> MagicMock:
    """Canned SearchTextResponse with one place + addressComponents."""
    place = MagicMock()
    place.id = "ChIJtest001"
    place.display_name.text = "Praia de Trancoso"
    place.formatted_address = f"Trancoso, {municipio} - {uf_short}, Brasil"
    place.types = ["tourist_attraction", "point_of_interest"]
    place.location.latitude = -16.57
    place.location.longitude = -39.08

    comp_municipio = MagicMock()
    comp_municipio.long_text = municipio
    comp_municipio.types = ["administrative_area_level_2", "political"]

    comp_state = MagicMock()
    comp_state.long_text = "Bahia"
    comp_state.short_text = uf_short
    comp_state.types = ["administrative_area_level_1", "political"]

    place.address_components = [comp_municipio, comp_state]

    response = MagicMock()
    response.places = [place]
    return response


def _make_place_response() -> MagicMock:
    """Canned Place response (for place_details) with reviews + regular_opening_hours."""
    place = MagicMock()
    place.id = "ChIJtest001"
    place.display_name.text = "Praia de Trancoso"
    place.formatted_address = "Trancoso, Porto Seguro - BA, Brasil"
    place.types = ["tourist_attraction"]
    place.location.latitude = -16.57
    place.location.longitude = -39.08
    place.address_components = []

    # business_status enum mock
    place.business_status.name = "OPERATIONAL"

    # regular_opening_hours
    place.regular_opening_hours.weekday_descriptions = ["Mon: 9:00 AM – 5:00 PM"]

    # review with a mock proto Timestamp
    review = MagicMock()
    review.publish_time.ToDatetime.return_value = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    review.text.text = "Great place!"
    review.rating = 5.0

    place.reviews = [review]
    place.international_phone_number = "+55 73 1234-5678"
    place.website_uri = "https://example.com"

    return place


# ---------------------------------------------------------------------------
# T1 — guard raises RuntimeError when run_real_externals=False
# ---------------------------------------------------------------------------


def test_guard_raises_when_run_real_externals_false(monkeypatch):
    """RealPlacesClient raises RuntimeError containing 'run_real_externals=False'
    when RUN_REAL_EXTERNALS env var is absent/false.

    No network calls; import happens inside test to pick up the env state.
    """
    monkeypatch.delenv("RUN_REAL_EXTERNALS", raising=False)

    from brave.clients.places import RealPlacesClient

    with pytest.raises(RuntimeError, match="run_real_externals=False"):
        RealPlacesClient(api_key="test-key")


# ---------------------------------------------------------------------------
# T2 — text_search sends x-goog-fieldmask with 'places.' prefix + addressComponents
# ---------------------------------------------------------------------------


async def test_text_search_sends_field_mask_with_places_prefix(monkeypatch):
    """text_search metadata must include x-goog-fieldmask starting with 'places.'
    and containing 'addressComponents' — D-01 fix.
    """
    monkeypatch.setenv("RUN_REAL_EXTERNALS", "true")

    mock_client = MagicMock()
    mock_client.search_text = AsyncMock(return_value=_make_search_response())

    with patch("google.maps.places_v1.PlacesAsyncClient", return_value=mock_client):
        from brave.clients.places import RealPlacesClient

        client = RealPlacesClient(api_key="test-key")
        client._client = mock_client  # inject mock directly — skip lazy init

        await client.text_search(query="praias em Porto Seguro", uf="BA")

    assert mock_client.search_text.called, "search_text was not called"
    call_kwargs = mock_client.search_text.call_args.kwargs
    metadata = dict(call_kwargs.get("metadata", []))

    assert "x-goog-fieldmask" in metadata, (
        f"x-goog-fieldmask missing from metadata; got: {metadata!r}"
    )
    mask = metadata["x-goog-fieldmask"]
    assert mask.startswith("places."), (
        f"text_search mask must start with 'places.', got: {mask!r}"
    )
    assert "addressComponents" in mask, (
        f"'addressComponents' missing from text_search mask: {mask!r}"
    )


# ---------------------------------------------------------------------------
# T3 — place_details sends x-goog-fieldmask WITHOUT 'places.' prefix
# ---------------------------------------------------------------------------


async def test_place_details_sends_field_mask_without_places_prefix(monkeypatch):
    """place_details metadata must NOT have 'places.' prefix in x-goog-fieldmask
    and must include 'regularOpeningHours' and 'businessStatus' — D-01 fix.
    """
    monkeypatch.setenv("RUN_REAL_EXTERNALS", "true")

    mock_client = MagicMock()
    mock_client.get_place = AsyncMock(return_value=_make_place_response())

    with patch("google.maps.places_v1.PlacesAsyncClient", return_value=mock_client):
        from brave.clients.places import RealPlacesClient

        client = RealPlacesClient(api_key="test-key")
        client._client = mock_client

        await client.place_details(place_id="ChIJtest001")

    assert mock_client.get_place.called, "get_place was not called"
    call_kwargs = mock_client.get_place.call_args.kwargs
    metadata = dict(call_kwargs.get("metadata", []))

    assert "x-goog-fieldmask" in metadata, (
        f"x-goog-fieldmask missing from metadata; got: {metadata!r}"
    )
    mask = metadata["x-goog-fieldmask"]
    assert not mask.startswith("places."), (
        f"place_details mask must NOT start with 'places.', got: {mask!r}"
    )
    assert "regularOpeningHours" in mask, (
        f"'regularOpeningHours' missing from place_details mask: {mask!r}"
    )
    assert "businessStatus" in mask, (
        f"'businessStatus' missing from place_details mask: {mask!r}"
    )


# ---------------------------------------------------------------------------
# T4 — addressComponents → municipio_nome + municipio_ibge via ibge_lookup
# ---------------------------------------------------------------------------


async def test_text_search_maps_address_components_to_municipio_fields(monkeypatch):
    """text_search results include municipio_nome from addressComponents and
    municipio_ibge resolved via the ibge_lookup passed to RealPlacesClient — D-02 fix.
    """
    monkeypatch.setenv("RUN_REAL_EXTERNALS", "true")

    mock_client = MagicMock()
    mock_client.search_text = AsyncMock(
        return_value=_make_search_response(municipio="Porto Seguro", uf_short="BA")
    )

    with patch("google.maps.places_v1.PlacesAsyncClient", return_value=mock_client):
        from brave.clients.places import RealPlacesClient

        # Pass ibge_lookup with the normalized key the client will build
        client = RealPlacesClient(
            api_key="test-key",
            ibge_lookup={("porto seguro", "BA"): "2927408"},
        )
        client._client = mock_client

        results = await client.text_search(query="praias", uf="BA")

    assert len(results) == 1, f"Expected 1 result, got {len(results)}"
    assert results[0]["municipio_nome"] == "Porto Seguro", (
        f"Expected 'Porto Seguro', got {results[0]['municipio_nome']!r}"
    )
    assert results[0]["municipio_ibge"] == "2927408", (
        f"Expected '2927408', got {results[0]['municipio_ibge']!r}"
    )


# ---------------------------------------------------------------------------
# T5 — review.publish_time proto Timestamp converted via ToDatetime()
# ---------------------------------------------------------------------------


async def test_place_details_converts_publish_time_via_to_datetime(monkeypatch):
    """place_details converts review.publish_time using ToDatetime(tzinfo=utc).isoformat()
    — no AttributeError even when publish_time is a proto Timestamp.
    """
    monkeypatch.setenv("RUN_REAL_EXTERNALS", "true")

    mock_client = MagicMock()
    place_response = _make_place_response()
    # The review's publish_time.ToDatetime() returns a known datetime
    expected_dt = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    place_response.reviews[0].publish_time.ToDatetime.return_value = expected_dt
    mock_client.get_place = AsyncMock(return_value=place_response)

    with patch("google.maps.places_v1.PlacesAsyncClient", return_value=mock_client):
        from brave.clients.places import RealPlacesClient

        client = RealPlacesClient(api_key="test-key")
        client._client = mock_client

        result = await client.place_details(place_id="ChIJtest001")

    assert len(result["reviews"]) == 1, f"Expected 1 review, got {len(result['reviews'])}"
    publish_time = result["reviews"][0]["publishTime"]
    assert publish_time is not None, "publishTime should not be None"
    # The isoformat of 2026-06-01T12:00:00+00:00
    assert "2026-06-01" in publish_time, (
        f"Expected 2026-06-01 in publishTime, got: {publish_time!r}"
    )
    assert "12:00:00" in publish_time, (
        f"Expected 12:00:00 in publishTime, got: {publish_time!r}"
    )
    # Verify ToDatetime was called with tzinfo=utc
    place_response.reviews[0].publish_time.ToDatetime.assert_called_once_with(
        tzinfo=timezone.utc
    )
