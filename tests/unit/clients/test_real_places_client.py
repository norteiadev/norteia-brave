"""Offline unit tests for RealPlacesClient (D-08, Phase 7).

100% offline — no real Places API calls, no network (TEST-01).

Tests:
  T1 — guard: RuntimeError when run_real_externals=False
  T2 — text_search sends x-goog-fieldmask with 'places.' prefix + 'addressComponents'
  T3 — place_details sends x-goog-fieldmask WITHOUT 'places.' prefix + 'regularOpeningHours'
  T4 — addressComponents → municipio_nome + municipio_ibge via ibge_lookup
  T5 — review.publish_time proto Timestamp converted safely via ToDatetime()
  T6 — addressComponents admin_area_level_3 → distrito_hint (text_search + place_details)
T7 — include_reviews picks the Details mask (Enterprise+Atmosphere vs Enterprise);
     types parsed; Null/Fake clients accept the kwarg
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


async def test_text_search_drops_places_google_puts_in_another_uf(monkeypatch):
    """The UF in the query is only a hint: a namesake in ANOTHER state is dropped, even
    when its município is a homonym of one in the requested UF (Bom Jesus/RS for uf=PI
    would otherwise get the PI IBGE code). No level_1 component → kept (fail open)."""
    monkeypatch.setenv("RUN_REAL_EXTERNALS", "true")

    def _place(place_id: str, components: list) -> MagicMock:
        place = _make_search_response().places[0]
        place.id = place_id
        place.address_components = components
        return place

    rs = _make_search_response(municipio="Bom Jesus", uf_short="RS").places[0].address_components
    pi = _make_search_response(municipio="Bom Jesus", uf_short="pi").places[0].address_components
    response = MagicMock()
    response.places = [
        _place("ChIJrs", rs),
        _place("ChIJpi", pi),
        _place("ChIJnolevel1", [rs[0]]),  # level_2 only
    ]
    mock_client = MagicMock()
    mock_client.search_text = AsyncMock(return_value=response)

    with patch("google.maps.places_v1.PlacesAsyncClient", return_value=mock_client):
        from brave.clients.places import RealPlacesClient

        client = RealPlacesClient(
            api_key="test-key", ibge_lookup={("bom jesus", "PI"): "2201903"}
        )
        client._client = mock_client

        results = await client.text_search(query="Igreja Matriz", uf="PI")

    assert [r["place_id"] for r in results] == ["ChIJpi", "ChIJnolevel1"]
    assert results[0]["municipio_ibge"] == "2201903"


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


# ---------------------------------------------------------------------------
# T6 — addressComponents admin_area_level_3 → distrito_hint
# ---------------------------------------------------------------------------


def _make_distrito_component(long_text: str) -> MagicMock:
    """A canned addressComponent carrying the distrito name (admin_area_level_3)."""
    comp = MagicMock()
    comp.long_text = long_text
    comp.types = ["administrative_area_level_3", "political"]
    return comp


async def test_text_search_extracts_distrito_hint_from_level_3(monkeypatch):
    """text_search results surface distrito_hint from an addressComponent whose
    types contain 'administrative_area_level_3' (its long_text) — DTB name-match signal.
    """
    monkeypatch.setenv("RUN_REAL_EXTERNALS", "true")

    response = _make_search_response(municipio="Porto Seguro", uf_short="BA")
    # Splice the distrito component into the existing components list
    response.places[0].address_components = [
        *response.places[0].address_components,
        _make_distrito_component("Arraial d'Ajuda"),
    ]
    mock_client = MagicMock()
    mock_client.search_text = AsyncMock(return_value=response)

    with patch("google.maps.places_v1.PlacesAsyncClient", return_value=mock_client):
        from brave.clients.places import RealPlacesClient

        client = RealPlacesClient(api_key="test-key")
        client._client = mock_client

        results = await client.text_search(query="igrejas", uf="BA")

    assert results[0]["distrito_hint"] == "Arraial d'Ajuda", (
        f"Expected distrito_hint 'Arraial d'Ajuda', got {results[0]['distrito_hint']!r}"
    )


async def test_text_search_distrito_hint_empty_without_level_3(monkeypatch):
    """text_search yields distrito_hint == '' when no admin_area_level_3 component
    is present (município seat with no distrito text) — keys stay null downstream.
    """
    monkeypatch.setenv("RUN_REAL_EXTERNALS", "true")

    # Default _make_search_response has only level_2 + level_1 (no level_3)
    mock_client = MagicMock()
    mock_client.search_text = AsyncMock(return_value=_make_search_response())

    with patch("google.maps.places_v1.PlacesAsyncClient", return_value=mock_client):
        from brave.clients.places import RealPlacesClient

        client = RealPlacesClient(api_key="test-key")
        client._client = mock_client

        results = await client.text_search(query="praias", uf="BA")

    assert results[0]["distrito_hint"] == "", (
        f"Expected empty distrito_hint, got {results[0]['distrito_hint']!r}"
    )


async def test_place_details_extracts_distrito_hint_from_level_3(monkeypatch):
    """place_details surfaces distrito_hint from an admin_area_level_3 component."""
    monkeypatch.setenv("RUN_REAL_EXTERNALS", "true")

    place_response = _make_place_response()
    place_response.address_components = [_make_distrito_component("Trancoso")]
    mock_client = MagicMock()
    mock_client.get_place = AsyncMock(return_value=place_response)

    with patch("google.maps.places_v1.PlacesAsyncClient", return_value=mock_client):
        from brave.clients.places import RealPlacesClient

        client = RealPlacesClient(api_key="test-key")
        client._client = mock_client

        result = await client.place_details(place_id="ChIJtest001")

    assert result["distrito_hint"] == "Trancoso", (
        f"Expected distrito_hint 'Trancoso', got {result['distrito_hint']!r}"
    )


# ---------------------------------------------------------------------------
# T7 — include_reviews mask choice + types parsing
# ---------------------------------------------------------------------------


async def _place_details_with_mask(monkeypatch, **kwargs) -> tuple[dict, str]:
    """Run RealPlacesClient.place_details on the canned Place; return (result, mask)."""
    monkeypatch.setenv("RUN_REAL_EXTERNALS", "true")

    place_response = _make_place_response()
    place_response.types = ["tourist_attraction", "park"]
    mock_client = MagicMock()
    mock_client.get_place = AsyncMock(return_value=place_response)

    from brave.clients.places import RealPlacesClient

    client = RealPlacesClient(api_key="test-key")
    client._client = mock_client

    result = await client.place_details(place_id="ChIJtest001", **kwargs)
    mask = dict(mock_client.get_place.call_args.kwargs["metadata"])["x-goog-fieldmask"]
    return result, mask


async def test_place_details_default_mask_includes_reviews(monkeypatch):
    """Default call keeps reviews (Enterprise + Atmosphere SKU)."""
    result, mask = await _place_details_with_mask(monkeypatch)

    fields = mask.split(",")
    assert "reviews" in fields
    assert len(result["reviews"]) == 1


async def test_place_details_without_reviews_drops_reviews_from_mask(monkeypatch):
    """include_reviews=False → reviews-free mask (Enterprise SKU), dict keeps reviews == []."""
    result, mask = await _place_details_with_mask(monkeypatch, include_reviews=False)

    fields = mask.split(",")
    assert "reviews" not in fields
    assert "regularOpeningHours" in fields
    assert result["reviews"] == []


async def test_place_details_parses_types(monkeypatch):
    """types (list[str]) is surfaced in the details dict (requested + billed; copywriter reads it)."""
    result, _mask = await _place_details_with_mask(monkeypatch)

    assert result["types"] == ["tourist_attraction", "park"]
    assert "user_rating_count" not in result


async def test_null_places_client_accepts_include_reviews():
    """NullPlacesClient takes the include_reviews kwarg and stays an empty no-op."""
    from brave.clients.null_places import NullPlacesClient

    assert await NullPlacesClient().place_details("ChIJx", include_reviews=False) == {}


async def test_fake_places_client_records_include_reviews_and_strips_reviews():
    """FakePlacesClient records each flag; include_reviews=False returns a reviews-free
    copy without mutating the fixture. place_details_calls keeps its plain-str shape."""
    from tests.fakes.fake_places import SIGNAL_FIXTURE_OPEN, FakePlacesClient

    fake = FakePlacesClient(fixture_details={"ChIJtest001": SIGNAL_FIXTURE_OPEN})

    with_reviews = await fake.place_details("ChIJtest001")
    without = await fake.place_details("ChIJtest001", include_reviews=False)

    assert fake.place_details_calls == ["ChIJtest001", "ChIJtest001"]
    assert fake.place_details_include_reviews == [True, False]
    assert len(with_reviews["reviews"]) == 1
    assert without["reviews"] == []
    assert without["business_status"] == "OPERATIONAL"
    assert len(SIGNAL_FIXTURE_OPEN["reviews"]) == 1


# ---------------------------------------------------------------------------
# T8 — retry before balance classification (F29) + daily Places budget (C4)
# ---------------------------------------------------------------------------


class ResourceExhausted(Exception):  # noqa: N818 — mirrors google.api_core's class name
    """Stand-in for google.api_core.exceptions.ResourceExhausted (matched by name)."""


def _client_with(
    monkeypatch, *, search_text=None, get_place=None, redis_client=None, cap=300.0,
    total_cap=300.0, api_key="test-key",
):
    from tenacity import wait_none

    from brave.clients.places import RealPlacesClient

    monkeypatch.setenv("RUN_REAL_EXTERNALS", "true")
    # No real backoff in tests.
    monkeypatch.setattr(RealPlacesClient._search_text_once.retry, "wait", wait_none())
    monkeypatch.setattr(RealPlacesClient._get_place_once.retry, "wait", wait_none())
    client = RealPlacesClient(
        api_key=api_key, redis_client=redis_client, daily_usd_cap=cap, total_usd_cap=total_cap
    )
    mock_sdk = MagicMock()
    mock_sdk.search_text = search_text or AsyncMock(return_value=_make_search_response())
    mock_sdk.get_place = get_place or AsyncMock(return_value=_make_place_response())
    client._client = mock_sdk
    return client, mock_sdk


async def test_minute_burst_is_retried_not_a_balance_wall(monkeypatch):
    """Two 429s then success → results; the first 429 used to raise ProviderBalanceError
    inside the @retry'd call (rejected by the retry predicate) and pause the engine."""
    burst = ResourceExhausted("Quota exceeded ... 'Text Search requests per minute'")
    search = AsyncMock(side_effect=[burst, burst, _make_search_response()])
    client, sdk = _client_with(monkeypatch, search_text=search)

    results = await client.text_search("praias", uf="BA")

    assert len(results) == 1
    assert sdk.search_text.await_count == 3


async def test_minute_burst_outliving_retries_stays_transient(monkeypatch):
    """A per-minute limit still active after the retries is NOT a balance wall."""
    from brave.shared.exceptions import ProviderBalanceError

    burst = ResourceExhausted("Quota exceeded ... 'GetPlace requests per minute'")
    client, sdk = _client_with(monkeypatch, get_place=AsyncMock(side_effect=burst))

    with pytest.raises(ResourceExhausted) as info:
        await client.place_details("ChIJtest001")
    assert not isinstance(info.value, ProviderBalanceError)
    assert sdk.get_place.await_count == 3


async def test_daily_quota_after_retries_is_a_balance_wall(monkeypatch):
    from brave.shared.exceptions import ProviderBalanceError

    wall = ResourceExhausted("Quota exceeded ... 'Text Search requests per day'")
    client, sdk = _client_with(monkeypatch, search_text=AsyncMock(side_effect=wall))

    with pytest.raises(ProviderBalanceError):
        await client.text_search("praias", uf="BA")
    assert sdk.search_text.await_count == 3


class PermissionDenied(Exception):  # noqa: N818 — mirrors google.api_core's class name
    """Stand-in for google.api_core.exceptions.PermissionDenied (403, matched by name)."""


class Unauthenticated(Exception):  # noqa: N818 — mirrors google.api_core's class name
    """Stand-in for google.api_core.exceptions.Unauthenticated (401, matched by name)."""


@pytest.mark.parametrize(
    "exc",
    [
        PermissionDenied("Places API (New) has not been used in project 123 ... SERVICE_DISABLED"),
        PermissionDenied("Requests from referer <empty> are blocked. API_KEY_HTTP_REFERRER_BLOCKED"),
        Unauthenticated("API key not valid. API_KEY_INVALID"),
    ],
)
async def test_misconfigured_key_is_a_balance_wall(monkeypatch, exc):
    """A swapped key without Places API (New) enabled / invalid / restricted never fixes itself:
    it must pause (ProviderBalanceError), not be swallowed per record until the run is "done"."""
    from brave.shared.exceptions import ProviderBalanceError

    client, sdk = _client_with(monkeypatch, search_text=AsyncMock(side_effect=exc))
    with pytest.raises(ProviderBalanceError):
        await client.text_search("praias", uf="BA")
    assert sdk.search_text.await_count == 1, "a 403/401 is not retried"


async def test_spend_recorded_per_sku_on_places_counter(monkeypatch, fake_redis):
    from brave.observability.cost_guard import daily_spend, places_lane, places_total_spend

    client, _ = _client_with(monkeypatch, redis_client=fake_redis)
    lane = places_lane("test-key")

    await client.text_search("praias", uf="BA")
    assert daily_spend(fake_redis, lane) == pytest.approx(0.032)
    await client.place_details("ChIJtest001")  # reviews → Enterprise + Atmosphere
    assert daily_spend(fake_redis, lane) == pytest.approx(0.057)
    await client.place_details("ChIJtest001", include_reviews=False)  # Enterprise
    assert daily_spend(fake_redis, lane) == pytest.approx(0.077)
    assert places_total_spend(fake_redis, "test-key") == pytest.approx(0.077)
    assert daily_spend(fake_redis) == 0.0, "Places must not consume the LLM budget"


async def test_cap_reached_raises_before_any_call(monkeypatch, fake_redis):
    from brave.observability.cost_guard import places_lane, record_spend
    from brave.shared.exceptions import CostGuardError

    record_spend(fake_redis, 0.99, places_lane("test-key"))
    client, sdk = _client_with(monkeypatch, redis_client=fake_redis, cap=1.0)

    with pytest.raises(CostGuardError):
        await client.text_search("praias", uf="BA")  # 0.99 + 0.032 > 1.0
    assert sdk.search_text.await_count == 0
    with pytest.raises(CostGuardError):
        await client.place_details("ChIJtest001", include_reviews=False)  # 0.99 + 0.020
    assert sdk.get_place.await_count == 0


async def test_total_credit_cap_is_a_balance_wall_and_a_new_key_resumes(monkeypatch, fake_redis):
    """The trial credit is per key and never resets at midnight: reaching it is a billing
    wall (ProviderBalanceError — pause until the operator swaps the key), no call made. The
    swapped key has its own counters, so it works the SAME UTC day (the old shared daily
    counter kept it blocked until 00:00 UTC)."""
    from brave.observability.cost_guard import record_places_spend
    from brave.shared.exceptions import ProviderBalanceError

    record_places_spend(fake_redis, 299.99, "key-a")
    old, old_sdk = _client_with(monkeypatch, redis_client=fake_redis, api_key="key-a")
    with pytest.raises(ProviderBalanceError):
        await old.text_search("praias", uf="BA")
    assert old_sdk.search_text.await_count == 0

    new, new_sdk = _client_with(monkeypatch, redis_client=fake_redis, api_key="key-b")
    await new.text_search("praias", uf="BA")
    assert new_sdk.search_text.await_count == 1


async def test_total_cap_survives_the_utc_day_turn(monkeypatch, fake_redis):
    """The daily counter expires; the total does not — a new day is no new credit."""
    from brave.observability.cost_guard import places_lane, record_places_spend
    from brave.shared.exceptions import ProviderBalanceError

    record_places_spend(fake_redis, 299.99, "key-a")
    fake_redis.delete(*fake_redis.keys(f"brave:cost:{places_lane('key-a')}:daily:*"))
    client, sdk = _client_with(monkeypatch, redis_client=fake_redis, api_key="key-a")
    with pytest.raises(ProviderBalanceError):
        await client.place_details("ChIJtest001")
    assert sdk.get_place.await_count == 0

