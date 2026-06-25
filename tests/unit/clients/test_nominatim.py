"""Unit tests for NominatimGeocoderClient and NullGeocoderClient (TA-14).

Tests verify all TA-14 behaviors without real network calls:
  - Guard raises RuntimeError when run_real_externals=False
  - geocode sends User-Agent + addressdetails=1 + countrycodes=br (respx)
  - Address precedence: municipality → city → town → village → county
  - Redis cache hit on 2nd call → no 2nd httpx request (respx count==1)
  - Rate limit ≥1 req/s enforced (mock clock / asyncio.sleep asserted)
  - NullGeocoderClient.geocode returns None, no network
  - LGPD: result has only lat/lon/osm_id/municipio_name (no street/PII)

Node IDs must match 14-VALIDATION.md exactly:
  test_guard_raises, test_request_params, test_address_precedence,
  test_cache_by_location_id, test_rate_limit, test_null_returns_none,
  test_lgpd_no_pii

All tests are 100% offline: respx mocks httpx; fakeredis mocks Redis.
asyncio_mode = "auto" (pyproject.toml) — no @pytest.mark.asyncio needed.
"""

from __future__ import annotations

import json

import fakeredis
import httpx
import pytest
import respx

from brave.config.settings import NominatimConfig


# ---------------------------------------------------------------------------
# TA-14: Guard test (top-level function — node ID: test_guard_raises)
# ---------------------------------------------------------------------------


def test_guard_raises() -> None:
    """NominatimGeocoderClient raises RuntimeError when run_real_externals=False.

    Node ID: tests/unit/clients/test_nominatim.py::test_guard_raises
    """
    from brave.clients.nominatim import NominatimGeocoderClient

    redis = fakeredis.FakeRedis()
    config = NominatimConfig()
    # RUN_REAL_EXTERNALS is not set in CI — AppConfig().run_real_externals is False
    with pytest.raises(RuntimeError, match="run_real_externals=False"):
        NominatimGeocoderClient(config=config, redis=redis)


# ---------------------------------------------------------------------------
# TA-14: Request params test (top-level — node ID: test_request_params)
# ---------------------------------------------------------------------------


async def test_request_params(monkeypatch: pytest.MonkeyPatch) -> None:
    """geocode sends User-Agent + addressdetails=1 + countrycodes=br (respx captures).

    Node ID: tests/unit/clients/test_nominatim.py::test_request_params
    """
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
                        "lat": "-19.0469",
                        "lon": "-43.4256",
                        "osm_id": 123,
                        "address": {
                            "municipality": "Conceição do Mato Dentro",
                            "state": "Minas Gerais",
                            "country_code": "br",
                        },
                    }
                ],
            )

        # Analog: test_client.py lines 381-384 (respx.get + side_effect)
        respx.get("https://nominatim.openstreetmap.org/search").mock(side_effect=capture)

        client = NominatimGeocoderClient(config=config, redis=redis)
        result = await client.geocode("312332", "Cachoeira do Tabuleiro", "MG")

    assert result is not None
    assert result["municipio_name"] == "Conceição do Mato Dentro"
    assert captured_request is not None
    assert "User-Agent" in captured_request.headers
    assert captured_request.url.params["addressdetails"] == "1"
    assert captured_request.url.params["countrycodes"] == "br"


# ---------------------------------------------------------------------------
# TA-14: Address precedence test (top-level — node ID: test_address_precedence)
# ---------------------------------------------------------------------------


async def test_address_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    """Address precedence: municipality → city → town → village → county.

    Node ID: tests/unit/clients/test_nominatim.py::test_address_precedence
    """
    monkeypatch.setenv("RUN_REAL_EXTERNALS", "true")

    from brave.clients.nominatim import NominatimGeocoderClient

    # --- town wins when municipality/city absent ---
    redis = fakeredis.FakeRedis()
    config = NominatimConfig()

    with respx.mock:
        respx.get("https://nominatim.openstreetmap.org/search").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "lat": "-19.0",
                        "lon": "-43.0",
                        "osm_id": 456,
                        "address": {
                            "town": "Tabuleiro Town",
                            "state": "Minas Gerais",
                            "country_code": "br",
                        },
                    }
                ],
            )
        )
        client = NominatimGeocoderClient(config=config, redis=redis)
        result = await client.geocode("111", "SomeAttraction", "MG")

    assert result is not None
    assert result["municipio_name"] == "Tabuleiro Town"

    # --- county wins when municipality/city/town/village all absent ---
    redis2 = fakeredis.FakeRedis()
    with respx.mock:
        respx.get("https://nominatim.openstreetmap.org/search").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "lat": "-20.0",
                        "lon": "-44.0",
                        "osm_id": 789,
                        "address": {
                            "county": "County X",
                            "state": "Minas Gerais",
                            "country_code": "br",
                        },
                    }
                ],
            )
        )
        client2 = NominatimGeocoderClient(config=config, redis=redis2)
        result2 = await client2.geocode("222", "OtherAttraction", "MG")

    assert result2 is not None
    assert result2["municipio_name"] == "County X"


# ---------------------------------------------------------------------------
# TA-14: Cache test (top-level — node ID: test_cache_by_location_id)
# ---------------------------------------------------------------------------


async def test_cache_by_location_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """Second geocode call hits Redis cache — respx call count == 1.

    Node ID: tests/unit/clients/test_nominatim.py::test_cache_by_location_id
    """
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
                        "lat": "-19.0469",
                        "lon": "-43.4256",
                        "osm_id": 123,
                        "address": {"municipality": "Conceição do Mato Dentro"},
                    }
                ],
            )
        )
        client = NominatimGeocoderClient(config=config, redis=redis)
        await client.geocode("312332", "Cachoeira do Tabuleiro", "MG")
        await client.geocode("312332", "Cachoeira do Tabuleiro", "MG")  # cache hit

    assert route.call_count == 1, "Second call must hit cache, not Nominatim"


# ---------------------------------------------------------------------------
# TA-14: Rate limit test (top-level — node ID: test_rate_limit)
# ---------------------------------------------------------------------------


async def test_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rate limit ≥1 req/s enforced: asyncio.sleep fires when last_request_ts is recent.

    Node ID: tests/unit/clients/test_nominatim.py::test_rate_limit
    """
    monkeypatch.setenv("RUN_REAL_EXTERNALS", "true")

    import asyncio
    import time

    from brave.clients.nominatim import NominatimGeocoderClient

    redis = fakeredis.FakeRedis()
    config = NominatimConfig(min_request_interval=1.1)

    sleep_calls: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    # Patch time.monotonic to simulate last request was 0.5s ago
    now = time.monotonic()
    call_count = 0

    def fake_monotonic() -> float:
        nonlocal call_count
        call_count += 1
        # First call: returns (now - 0.5) so elapsed = 0.5 < 1.1
        # Second call (after sleep): returns now so _last_request_ts is updated
        if call_count <= 2:
            return now - 0.5
        return now

    monkeypatch.setattr("brave.clients.nominatim.time.monotonic", fake_monotonic)
    monkeypatch.setattr("brave.clients.nominatim.asyncio.sleep", fake_sleep)

    with respx.mock:
        respx.get("https://nominatim.openstreetmap.org/search").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "lat": "-19.0",
                        "lon": "-43.0",
                        "osm_id": 1,
                        "address": {"municipality": "TestCity"},
                    }
                ],
            )
        )
        client = NominatimGeocoderClient(config=config, redis=redis)
        # Set last_request_ts to simulate a recent request (0.5s ago)
        client._last_request_ts = now - 0.5 + 0.6  # elapsed = 0.5 < 1.1
        # Override monotonic so elapsed calculation yields < min_interval
        import brave.clients.nominatim as nom_module

        nom_module.time.monotonic = lambda: now  # type: ignore[attr-defined]
        client._last_request_ts = now - 0.5  # elapsed will be 0.5 < 1.1
        await client.geocode("999", "SomePlace", "MG")

    assert len(sleep_calls) >= 1, "asyncio.sleep must be called when elapsed < min_request_interval"
    sleep_val = sleep_calls[0]
    assert sleep_val > 0, f"sleep value must be > 0, got {sleep_val}"
    assert sleep_val < 1.2, f"sleep value must be < 1.2s (min_interval=1.1 - elapsed~0.5), got {sleep_val}"


# ---------------------------------------------------------------------------
# TA-14: Null client test (top-level — node ID: test_null_returns_none)
# ---------------------------------------------------------------------------


async def test_null_returns_none() -> None:
    """NullGeocoderClient.geocode returns None, no network I/O.

    Node ID: tests/unit/clients/test_nominatim.py::test_null_returns_none
    """
    from brave.clients.null_nominatim import NullGeocoderClient

    client = NullGeocoderClient()
    result = await client.geocode("312332", "Cachoeira do Tabuleiro", "MG")
    assert result is None


# ---------------------------------------------------------------------------
# TA-14: LGPD no-PII test (top-level — node ID: test_lgpd_no_pii)
# ---------------------------------------------------------------------------


async def test_lgpd_no_pii(monkeypatch: pytest.MonkeyPatch) -> None:
    """LGPD: result dict has exactly {lat, lon, osm_id, municipio_name} — no PII.

    Node ID: tests/unit/clients/test_nominatim.py::test_lgpd_no_pii
    """
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
                        "lat": "-19.0469",
                        "lon": "-43.4256",
                        "osm_id": 123,
                        "display_name": "Cachoeira do Tabuleiro, Conceição do Mato Dentro, MG, Brazil",
                        "address": {
                            "municipality": "Conceição do Mato Dentro",
                            "state": "Minas Gerais",
                            "country_code": "br",
                            "postcode": "39170-000",
                            "road": "Estrada da Cachoeira",
                        },
                    }
                ],
            )
        )
        client = NominatimGeocoderClient(config=config, redis=redis)
        result = await client.geocode("312332", "Cachoeira do Tabuleiro", "MG")

    assert result is not None
    assert set(result.keys()) == {"lat", "lon", "osm_id", "municipio_name"}, (
        f"Result must have exactly {{lat, lon, osm_id, municipio_name}}; "
        f"got keys: {set(result.keys())}"
    )
    # Explicitly verify no PII keys leaked
    assert "display_name" not in result
    assert "street" not in result
    assert "postcode" not in result
    assert "address" not in result
    assert "road" not in result


# ---------------------------------------------------------------------------
# Negative cache test
# ---------------------------------------------------------------------------


async def test_negative_result_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty Nominatim response → None returned + __no_match sentinel in Redis.

    Prevents repeated queries for the same location_id.
    """
    monkeypatch.setenv("RUN_REAL_EXTERNALS", "true")

    from brave.clients.nominatim import NOMINATIM_CACHE_KEY_PREFIX, NominatimGeocoderClient

    redis = fakeredis.FakeRedis()
    config = NominatimConfig()

    with respx.mock:
        respx.get("https://nominatim.openstreetmap.org/search").mock(
            return_value=httpx.Response(200, json=[])
        )
        client = NominatimGeocoderClient(config=config, redis=redis)
        result = await client.geocode("99999", "Unknown Place", "MG")

    assert result is None

    # Verify __no_match sentinel was cached
    cached_raw = redis.get(f"{NOMINATIM_CACHE_KEY_PREFIX}99999")
    assert cached_raw is not None
    cached = json.loads(cached_raw)
    assert cached.get("__no_match") is True


# ---------------------------------------------------------------------------
# NullGeocoderClient protocol compliance
# ---------------------------------------------------------------------------


class TestNullGeocoderClient:
    """Additional tests for NullGeocoderClient."""

    def test_protocol_compliance(self) -> None:
        """Structural typing assertion — must not raise."""
        from brave.clients.null_nominatim import _check_protocol_compliance

        _check_protocol_compliance()


# ---------------------------------------------------------------------------
# FakeGeocoderClient tests
# ---------------------------------------------------------------------------


class TestFakeGeocoderClient:
    """Tests for FakeGeocoderClient call recording and fixture return."""

    async def test_records_calls(self) -> None:
        """geocode calls are recorded in geocode_calls list."""
        from tests.fakes.fake_nominatim import FakeGeocoderClient

        fake = FakeGeocoderClient()
        await fake.geocode("123", "Some Attraction", "BA")
        await fake.geocode("456", "Another Attraction", "SP")

        assert len(fake.geocode_calls) == 2
        assert fake.geocode_calls[0] == {"location_id": "123", "name": "Some Attraction", "uf": "BA"}
        assert fake.geocode_calls[1] == {"location_id": "456", "name": "Another Attraction", "uf": "SP"}

    async def test_returns_fixture_result(self) -> None:
        """geocode returns fixture_results[location_id] or None if not present."""
        from tests.fakes.fake_nominatim import FakeGeocoderClient

        fixture = {
            "312332": {
                "lat": -19.047,
                "lon": -43.426,
                "osm_id": 123,
                "municipio_name": "Conceição do Mato Dentro",
            }
        }
        fake = FakeGeocoderClient(fixture_results=fixture)

        result = await fake.geocode("312332", "Cachoeira do Tabuleiro", "MG")
        assert result == fixture["312332"]

        result_none = await fake.geocode("999999", "Unknown", "MG")
        assert result_none is None

    def test_protocol_compliance(self) -> None:
        """Structural typing assertion — must not raise."""
        from tests.fakes.fake_nominatim import _check_protocol_compliance

        _check_protocol_compliance()
