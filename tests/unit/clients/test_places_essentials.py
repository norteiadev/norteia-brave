"""RealPlacesClient.place_essentials — Details at the Essentials SKU (places_provider="hybrid").

Offline: the google-maps-places SDK is a MagicMock, like test_real_places_client.py.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


def _component(long_text: str, short_text: str, *types: str) -> MagicMock:
    comp = MagicMock()
    comp.long_text, comp.short_text, comp.types = long_text, short_text, list(types)
    return comp


def _place(*components: MagicMock) -> MagicMock:
    place = MagicMock()
    place.id = "ChIJixdqrlIWuAARlfIafNUX-qk"
    place.formatted_address = "R. José Mário Lopes, 262 - Praia da Costa, Vila Velha - ES, Brasil"
    place.types = ["tourist_attraction", "point_of_interest"]
    place.location.latitude = -20.3263
    place.location.longitude = -40.2775
    place.address_components = list(components) or [
        _component("Ponta da Fruta", "Ponta da Fruta", "administrative_area_level_3", "political"),
        _component("Vila Velha", "Vila Velha", "administrative_area_level_2", "political"),
        _component("Espírito Santo", "ES", "administrative_area_level_1", "political"),
    ]
    return place


def _client(monkeypatch, place, redis=None):
    from tenacity import wait_none

    from brave.clients.places import RealPlacesClient

    monkeypatch.setenv("RUN_REAL_EXTERNALS", "true")
    monkeypatch.setattr(RealPlacesClient._get_place_once.retry, "wait", wait_none())
    client = RealPlacesClient(
        api_key="test-key", ibge_lookup={("vila velha", "ES"): "3205200"}, redis_client=redis
    )
    sdk = MagicMock()
    sdk.get_place = AsyncMock(return_value=place)
    client._client = sdk
    return client, sdk


async def test_essentials_mask_price_and_fields(monkeypatch, fake_redis):
    from brave.clients.places import USD_DETAILS_ESSENTIALS
    from brave.observability.cost_guard import daily_spend, places_lane, places_total_spend

    client, sdk = _client(monkeypatch, _place(), fake_redis)
    out = await client.place_essentials("ChIJixdqrlIWuAARlfIafNUX-qk", "ES")

    mask = dict(sdk.get_place.call_args.kwargs["metadata"])["x-goog-fieldmask"]
    assert mask == "id,location,types,formattedAddress,addressComponents"
    assert "displayName" not in mask and "primaryType" not in mask, "one Pro field bills Pro"
    assert daily_spend(fake_redis, places_lane("test-key")) == pytest.approx(USD_DETAILS_ESSENTIALS)
    assert places_total_spend(fake_redis, "test-key") == pytest.approx(0.005)
    assert out == {
        "place_id": "ChIJixdqrlIWuAARlfIafNUX-qk",
        "formatted_address": "R. José Mário Lopes, 262 - Praia da Costa, Vila Velha - ES, Brasil",
        "types": ["tourist_attraction", "point_of_interest"],
        "location": {"lat": -20.3263, "lng": -40.2775},
        "municipio_nome": "Vila Velha",
        "municipio_ibge": "3205200",
        "uf_short": "ES",
        "municipio_from_locality": False,
        "distrito_hint": "Ponta da Fruta",
    }


async def test_essentials_locality_fallback_and_unknown_uf(monkeypatch):
    place = _place(
        _component("Vila Velha", "Vila Velha", "locality", "political"),
        _component("Espírito Santo", "ES", "administrative_area_level_1", "political"),
    )
    client, _ = _client(monkeypatch, place)
    out = await client.place_essentials("ChIJx", "")  # refresh by place_id: no UF known

    assert out["municipio_nome"] == "Vila Velha"
    assert out["municipio_from_locality"] is True
    assert out["municipio_ibge"] == "3205200", "resolved against Google's own state"
    assert out["distrito_hint"] == ""


async def test_essentials_in_another_uf_gets_no_ibge(monkeypatch):
    client, _ = _client(monkeypatch, _place())
    out = await client.place_essentials("ChIJx", "BA")
    assert out["uf_short"] == "ES" and out["municipio_ibge"] == ""


async def test_essentials_cap_blocks_before_the_call(monkeypatch, fake_redis):
    from brave.observability.cost_guard import record_places_spend
    from brave.shared.exceptions import ProviderBalanceError

    record_places_spend(fake_redis, 299.998, "test-key")
    client, sdk = _client(monkeypatch, _place(), fake_redis)
    with pytest.raises(ProviderBalanceError):
        await client.place_essentials("ChIJx", "ES")
    assert sdk.get_place.await_count == 0


async def test_essentials_permission_denied_is_a_balance_wall(monkeypatch):
    from brave.shared.exceptions import ProviderBalanceError

    class PermissionDenied(Exception):  # noqa: N818 — matched by the SDK class name
        pass

    client, sdk = _client(monkeypatch, _place())
    sdk.get_place = AsyncMock(side_effect=PermissionDenied("SERVICE_DISABLED"))
    with pytest.raises(ProviderBalanceError):
        await client.place_essentials("ChIJx", "ES")


def _ids_client(monkeypatch, redis=None, **search_text):
    from tenacity import wait_none

    from brave.clients.places import RealPlacesClient

    client, sdk = _client(monkeypatch, _place(), redis)
    monkeypatch.setattr(RealPlacesClient._search_text_once.retry, "wait", wait_none())
    response = MagicMock()
    response.places = [MagicMock(id="P1"), MagicMock(id="P2")]
    sdk.search_text = AsyncMock(**(search_text or {"return_value": response}))
    return client, sdk


async def test_search_ids_mask_bias_and_no_spend(monkeypatch, fake_redis):
    """Text Search IDs Only is free: no budget check (a spent cap does not block it), no spend."""
    from brave.observability.cost_guard import places_total_spend, record_places_spend

    record_places_spend(fake_redis, 300.0, "test-key")  # cap fully spent
    client, sdk = _ids_client(monkeypatch, fake_redis)
    ids = await client.search_ids("Morro do Moreno, Vila Velha - ES", lat=-20.3, lng=-40.2, radius_m=5000)

    assert ids == ["P1", "P2"]
    request = sdk.search_text.call_args.args[0]
    assert dict(sdk.search_text.call_args.kwargs["metadata"])["x-goog-fieldmask"] == "places.id"
    assert request.max_result_count == 5 and request.text_query == "Morro do Moreno, Vila Velha - ES"
    circle = request.location_bias.circle
    assert (circle.center.latitude, circle.center.longitude, circle.radius) == (-20.3, -40.2, 5000)
    assert places_total_spend(fake_redis, "test-key") == pytest.approx(300.0)


async def test_search_ids_permission_denied_is_a_balance_wall(monkeypatch):
    from brave.shared.exceptions import ProviderBalanceError

    class PermissionDenied(Exception):  # noqa: N818 — matched by the SDK class name
        pass

    client, _ = _ids_client(monkeypatch, side_effect=PermissionDenied("SERVICE_DISABLED"))
    with pytest.raises(ProviderBalanceError):
        await client.search_ids("X - ES", lat=0.0, lng=0.0, radius_m=5000)
