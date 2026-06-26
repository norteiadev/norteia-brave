"""Offline tests for the national geo primitives (Phase 15, TA-12).

Two reuse-friendly primitives the bulk all-Brazil attractions lane (15-06) needs
to derive uf + município from a geocoded card WITHOUT a per-UF input or a parent
destino:

  1. NominatimGeocoderClient.geocode_national — national forward-geocode
     ("{name}, Brazil"), same 4-key LGPD-safe result shape as geocode, reusing the
     existing Redis cache + rate-limit + retry pattern (distinct cache namespace).
  2. resolve_municipio_national — pure haversine over ALL IBGE records (no UF
     filter), returning the nearest IbgeMunicipio (which carries .uf + .ibge_code).

All tests are 100% offline: respx mocks httpx; fakeredis mocks Redis. No test hits
Nominatim unless RUN_REAL_EXTERNALS=1 (which is unset in CI).
asyncio_mode = "auto" (pyproject.toml) — no @pytest.mark.asyncio needed.
"""

from __future__ import annotations

import json

import fakeredis
import httpx
import pytest
import respx

from brave.config.settings import NominatimConfig
from brave.lanes.tripadvisor.ibge import IbgeMunicipio


# ---------------------------------------------------------------------------
# Task 1: NominatimGeocoderClient.geocode_national (respx + fakeredis)
# ---------------------------------------------------------------------------


async def test_geocode_national_returns_four_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """geocode_national returns the 4-key LGPD-safe dict; query is "{name}, Brazil" (no UF)."""
    monkeypatch.setenv("RUN_REAL_EXTERNALS", "true")

    from brave.clients.nominatim import NominatimGeocoderClient

    redis = fakeredis.FakeRedis()
    config = NominatimConfig()

    captured_request = None

    with respx.mock:

        def capture(request: httpx.Request) -> httpx.Response:
            nonlocal captured_request
            captured_request = request
            return httpx.Response(
                200,
                json=[
                    {
                        "lat": "-20.1234",
                        "lon": "-44.2056",
                        "osm_id": 555,
                        "address": {
                            "municipality": "Brumadinho",
                            "state": "Minas Gerais",
                            "country_code": "br",
                        },
                    }
                ],
            )

        respx.get("https://nominatim.openstreetmap.org/search").mock(side_effect=capture)

        client = NominatimGeocoderClient(config=config, redis=redis)
        result = await client.geocode_national("loc1", "Instituto Inhotim")

    assert result is not None
    assert result["municipio_name"] == "Brumadinho"
    assert result["lat"] == pytest.approx(-20.1234)
    assert result["lon"] == pytest.approx(-44.2056)
    assert result["osm_id"] == 555
    # National query: "{name}, Brazil" with NO UF segment.
    assert captured_request is not None
    assert captured_request.url.params["q"] == "Instituto Inhotim, Brazil"
    assert captured_request.url.params["countrycodes"] == "br"
    assert captured_request.url.params["addressdetails"] == "1"


async def test_geocode_national_caches_second_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Second geocode_national call hits the Redis cache — respx call count == 1."""
    monkeypatch.setenv("RUN_REAL_EXTERNALS", "true")

    from brave.clients.nominatim import NominatimGeocoderClient

    redis = fakeredis.FakeRedis()
    config = NominatimConfig()

    with respx.mock:
        route = respx.get("https://nominatim.openstreetmap.org/search").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "lat": "-20.1234",
                        "lon": "-44.2056",
                        "osm_id": 555,
                        "address": {"municipality": "Brumadinho"},
                    }
                ],
            )
        )
        client = NominatimGeocoderClient(config=config, redis=redis)
        await client.geocode_national("loc1", "Instituto Inhotim")
        await client.geocode_national("loc1", "Instituto Inhotim")  # cache hit

    assert route.call_count == 1, "Second national call must hit cache, not Nominatim"


async def test_geocode_national_cache_key_namespaced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """National cache key is namespaced — it never collides with the per-UF geocode key."""
    monkeypatch.setenv("RUN_REAL_EXTERNALS", "true")

    from brave.clients.nominatim import (
        NOMINATIM_CACHE_KEY_PREFIX,
        NominatimGeocoderClient,
    )

    redis = fakeredis.FakeRedis()
    config = NominatimConfig()

    with respx.mock:
        respx.get("https://nominatim.openstreetmap.org/search").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "lat": "-20.1234",
                        "lon": "-44.2056",
                        "osm_id": 555,
                        "address": {"municipality": "Brumadinho"},
                    }
                ],
            )
        )
        client = NominatimGeocoderClient(config=config, redis=redis)
        await client.geocode_national("loc1", "Instituto Inhotim")

    # The national key is populated; the per-UF key for the same location is NOT.
    natl_key = f"{NOMINATIM_CACHE_KEY_PREFIX}natl:loc1"
    per_uf_key = f"{NOMINATIM_CACHE_KEY_PREFIX}loc1"
    assert redis.get(natl_key) is not None
    assert redis.get(per_uf_key) is None


async def test_geocode_national_negative_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty Nominatim response → None returned + __no_match sentinel in the national key."""
    monkeypatch.setenv("RUN_REAL_EXTERNALS", "true")

    from brave.clients.nominatim import (
        NOMINATIM_CACHE_KEY_PREFIX,
        NominatimGeocoderClient,
    )

    redis = fakeredis.FakeRedis()
    config = NominatimConfig()

    with respx.mock:
        respx.get("https://nominatim.openstreetmap.org/search").mock(
            return_value=httpx.Response(200, json=[])
        )
        client = NominatimGeocoderClient(config=config, redis=redis)
        result = await client.geocode_national("99999", "Unknown Place")

    assert result is None
    cached_raw = redis.get(f"{NOMINATIM_CACHE_KEY_PREFIX}natl:99999")
    assert cached_raw is not None
    assert json.loads(cached_raw).get("__no_match") is True


async def test_geocode_national_lgpd_no_pii(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LGPD: result has exactly {lat, lon, osm_id, municipio_name} — no display_name/street/PII."""
    monkeypatch.setenv("RUN_REAL_EXTERNALS", "true")

    from brave.clients.nominatim import NominatimGeocoderClient

    redis = fakeredis.FakeRedis()
    config = NominatimConfig()

    with respx.mock:
        respx.get("https://nominatim.openstreetmap.org/search").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "lat": "-20.1234",
                        "lon": "-44.2056",
                        "osm_id": 555,
                        "display_name": "Instituto Inhotim, Brumadinho, MG, Brazil",
                        "address": {
                            "municipality": "Brumadinho",
                            "state": "Minas Gerais",
                            "country_code": "br",
                            "postcode": "35460-000",
                            "road": "Rua B",
                        },
                    }
                ],
            )
        )
        client = NominatimGeocoderClient(config=config, redis=redis)
        result = await client.geocode_national("loc1", "Instituto Inhotim")

    assert result is not None
    assert set(result.keys()) == {"lat", "lon", "osm_id", "municipio_name"}
    assert "display_name" not in result
    assert "address" not in result
    assert "road" not in result
    assert "postcode" not in result


async def test_geocode_national_address_precedence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """National geocode reuses the municipality→city→town→village→county precedence."""
    monkeypatch.setenv("RUN_REAL_EXTERNALS", "true")

    from brave.clients.nominatim import NominatimGeocoderClient

    redis = fakeredis.FakeRedis()
    config = NominatimConfig()

    with respx.mock:
        respx.get("https://nominatim.openstreetmap.org/search").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "lat": "-9.0",
                        "lon": "-40.0",
                        "osm_id": 777,
                        "address": {
                            "county": "County Only",
                            "state": "Bahia",
                            "country_code": "br",
                        },
                    }
                ],
            )
        )
        client = NominatimGeocoderClient(config=config, redis=redis)
        result = await client.geocode_national("loc2", "Some Remote Place")

    assert result is not None
    assert result["municipio_name"] == "County Only"


# ---------------------------------------------------------------------------
# Task 2: resolve_municipio_national (pure haversine over all IBGE records)
# ---------------------------------------------------------------------------


def _make_records() -> list[IbgeMunicipio]:
    """Small cross-state IBGE seat list (mirrors test_ibge.py fixture shape)."""
    rows = [
        ("3550308", "São Paulo", "SP", -23.5505, -46.6333),
        ("2927408", "Salvador", "BA", -12.9714, -38.5014),
        ("3304557", "Rio de Janeiro", "RJ", -22.9068, -43.1729),
        ("3109006", "Brumadinho", "MG", -20.1436, -44.2008),
        ("1100205", "Porto Velho", "RO", -8.7612, -63.9004),
    ]
    return [
        IbgeMunicipio(ibge_code=c, nome=n, uf=u, lat=lat, lng=lng)
        for c, n, u, lat, lng in rows
    ]


def test_resolve_national_nearest_seat_with_uf() -> None:
    """A coordinate near a known seat resolves to that record (with correct .uf + .ibge_code).

    No per-UF input: the resolver derives uf from the nearest seat over ALL records.
    """
    from brave.lanes.tripadvisor.ibge import resolve_municipio_national

    records = _make_records()
    # ~2 km from Brumadinho (MG) seat — but UF is NOT supplied.
    result = resolve_municipio_national(-20.1436, -44.1900, records)
    assert result is not None
    assert result.ibge_code == "3109006"
    assert result.uf == "MG"  # derived from coordinates, not input


def test_resolve_national_picks_global_minimum() -> None:
    """Resolver scans ALL records (no UF filter) and returns the global nearest seat."""
    from brave.lanes.tripadvisor.ibge import resolve_municipio_national

    records = _make_records()
    # Very close to Salvador (BA).
    result = resolve_municipio_national(-12.98, -38.51, records)
    assert result is not None
    assert result.ibge_code == "2927408"
    assert result.uf == "BA"


def test_resolve_national_ocean_returns_none() -> None:
    """A coordinate far (>50 km) from every seat (mid-Atlantic) returns None."""
    from brave.lanes.tripadvisor.ibge import resolve_municipio_national

    records = _make_records()
    result = resolve_municipio_national(-15.0, -25.0, records)
    assert result is None


def test_resolve_national_none_coords_returns_none() -> None:
    """None candidate coordinates return None (no derivation possible)."""
    from brave.lanes.tripadvisor.ibge import resolve_municipio_national

    records = _make_records()
    assert resolve_municipio_national(None, -44.0, records) is None
    assert resolve_municipio_national(-20.0, None, records) is None
    assert resolve_municipio_national(None, None, records) is None


def test_resolve_national_default_max_distance_is_50() -> None:
    """Default max_distance_km is the relaxed 50.0 radius (natural attractions sit 15-25 km out)."""
    import inspect

    from brave.lanes.tripadvisor.ibge import resolve_municipio_national

    sig = inspect.signature(resolve_municipio_national)
    assert sig.parameters["max_distance_km"].default == 50.0
