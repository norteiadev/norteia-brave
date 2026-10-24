"""TA client + Nominatim resilience: listing end-of-list (F03), session TTL slide (F11),
Nominatim UF/global slot (F02/F07). 100% offline (respx + fakeredis)."""

from __future__ import annotations

import json

import fakeredis
import httpx
import pytest
import respx

_GQL = "https://www.tripadvisor.com.br/data/graphql/ids"
_STUB = {"cookies": {"datadome": "abc"}, "user_agent": "UA", "session_id": "sid"}


def _client(redis=None):
    from brave.config.settings import TripAdvisorConfig
    from brave.domains.tripadvisor.client import TripAdvisorClient

    config = TripAdvisorConfig(
        page_throttle_seconds=0.0,
        attractions_transient_max_retries=2,
        attractions_transient_retry_sleep_seconds=0.0,
    )
    client = TripAdvisorClient(config=config, redis=redis or fakeredis.FakeRedis())
    client._get_session = lambda: dict(_STUB)  # type: ignore[method-assign]
    return client


def _card(detail_id: int) -> dict:
    return {
        "__typename": "WebPresentation_SingleFlexCardSection",
        "singleFlexCardContent": {
            "cardTitle": {"text": f"A{detail_id}"},
            "cardLink": {"webRoute": {"typedParams": {"detailId": detail_id}}},
            "bubbleRating": {"rating": 4.5, "reviewCount": 10},
            "primaryInfo": {"text": "Nature"},
        },
    }


def _page(sections: list, **result) -> list:
    return [{"data": {"Result": [{"sections": sections, **result}]}}]


_SOFT_FAIL = _page([], status={"success": False}, totalResults=0)


def _pagee(request) -> str:
    return json.loads(request.content)[0]["variables"]["request"]["routeParameters"]["pagee"]


async def _drain(client, max_pages: int = 5) -> list[int]:
    return [
        offset
        async for offset, _ in client.fetch_attractions_paginated_gql(
            294280, start_page=1, max_pages=max_pages
        )
    ]


@pytest.mark.asyncio
async def test_listing_retries_a_soft_failed_page_instead_of_ending() -> None:
    calls: list[str] = []
    replies = {
        "0": [_page([_card(1)])],
        "30": [_SOFT_FAIL, _page([_card(2)])],  # transient soft-fail, then OK
        "60": [_page([_card(3)])],
        "90": [_page([])],
    }

    def handler(request):
        pagee = _pagee(request)
        calls.append(pagee)
        return httpx.Response(200, json=replies[pagee].pop(0))

    with respx.mock:
        respx.post(_GQL).mock(side_effect=handler)
        offsets = await _drain(_client())

    assert offsets == [0, 30, 60]  # page 3 is still fetched
    assert calls == ["0", "30", "30", "60", "90"]


@pytest.mark.asyncio
async def test_listing_persistent_soft_fail_raises_transient_not_done() -> None:
    from brave.shared.exceptions import TransientError

    def handler(request):
        return httpx.Response(200, json=_page([_card(1)]) if _pagee(request) == "0" else _SOFT_FAIL)

    with respx.mock:
        respx.post(_GQL).mock(side_effect=handler)
        with pytest.raises(TransientError):
            await _drain(_client())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [[{"errors": [{"message": "PersistedQueryNotFound"}]}], [{"data": {}}]],
    ids=["errors-envelope", "no-Result"],
)
async def test_listing_systemic_failure_raises_schema_error(body) -> None:
    from brave.shared.exceptions import SourceSchemaError

    with respx.mock:
        respx.post(_GQL).mock(return_value=httpx.Response(200, json=body))
        with pytest.raises(SourceSchemaError):
            await _drain(_client())


@pytest.mark.asyncio
async def test_listing_stops_at_total_results_without_an_extra_request() -> None:
    calls: list[str] = []

    def handler(request):
        calls.append(_pagee(request))
        return httpx.Response(200, json=_page([_card(len(calls))], totalResults=60))

    with respx.mock:
        respx.post(_GQL).mock(side_effect=handler)
        offsets = await _drain(_client())

    assert offsets == [0, 30] and calls == ["0", "30"]


@pytest.mark.asyncio
async def test_geo_errors_envelope_raises_schema_error() -> None:
    from brave.shared.exceptions import SourceSchemaError

    with respx.mock:
        respx.post(_GQL).mock(
            return_value=httpx.Response(200, json=[{"errors": [{"message": "Nope"}]}])
        )
        with pytest.raises(SourceSchemaError):
            await _client().fetch_attraction_geo(1)


@pytest.mark.asyncio
@pytest.mark.parametrize("loc", [{"cityName": "Bonito"}, {"countryId": "294280"}])
async def test_geo_without_an_integer_country_id_raises_schema_error(loc) -> None:
    """A renamed/retyped countryId is a shape change, not "abroad": None stamped every
    record backfilled with the bulk's provisional UF (C1)."""
    from brave.shared.exceptions import SourceSchemaError

    with respx.mock:
        respx.post(_GQL).mock(
            return_value=httpx.Response(200, json=[{"data": {"gtmData": {"locationData": loc}}}])
        )
        with pytest.raises(SourceSchemaError):
            await _client().fetch_attraction_geo(1)


@pytest.mark.asyncio
async def test_every_2xx_slides_the_session_ttl_even_without_set_cookie() -> None:
    """F11: the TTL only slid when TA rotated a cookie — a long sweep outlived its key."""
    from brave.domains.tripadvisor.client import BRAVE_TA_SESSION_KEY

    redis = fakeredis.FakeRedis()
    redis.setex(BRAVE_TA_SESSION_KEY, 5, json.dumps(_STUB))
    client = _client(redis)

    with respx.mock:
        non_br = [{"data": {"gtmData": {"locationData": {"countryId": 1}}}}]
        respx.post(_GQL).mock(return_value=httpx.Response(200, json=non_br))
        await client.fetch_attraction_geo(1)

    assert redis.ttl(BRAVE_TA_SESSION_KEY) > 5


def test_empty_cookie_writeback_slides_ttl_but_never_resurrects() -> None:
    from brave.config.settings import TripAdvisorConfig
    from brave.domains.tripadvisor.client import BRAVE_TA_SESSION_KEY
    from brave.domains.tripadvisor.session import persist_rotated_cookies

    redis = fakeredis.FakeRedis()
    persist_rotated_cookies(redis, {}, TripAdvisorConfig())
    assert redis.get(BRAVE_TA_SESSION_KEY) is None  # absent key stays absent


# ---------------------------------------------------------------------------
# Nominatim
# ---------------------------------------------------------------------------

_NOMINATIM = "https://nominatim.openstreetmap.org/search"
_HIT = [{"lat": "-15.4", "lon": "-55.7", "osm_id": 1,
         "address": {"town": "Chapada dos Guimarães", "ISO3166-2-lvl4": "BR-MT"}}]


@pytest.mark.asyncio
async def test_geocode_national_returns_uf_and_refetches_legacy_cache(monkeypatch) -> None:
    monkeypatch.setenv("RUN_REAL_EXTERNALS", "true")
    from brave.clients.nominatim import NominatimGeocoderClient
    from brave.config.settings import NominatimConfig

    redis = fakeredis.FakeRedis()
    # Cached before "uf" existed → must be re-fetched, not trusted.
    redis.set("brave:geo:natl:9", json.dumps({"lat": 1.0, "lon": 2.0, "osm_id": 1,
                                               "municipio_name": "X"}))
    client = NominatimGeocoderClient(config=NominatimConfig(min_request_interval=0.0), redis=redis)
    with respx.mock:
        route = respx.get(_NOMINATIM).mock(return_value=httpx.Response(200, json=_HIT))
        first = await client.geocode_national("9", "Cachoeira Véu de Noiva")
        second = await client.geocode_national("9", "Cachoeira Véu de Noiva")

    assert first["uf"] == "MT" and second == first
    assert route.call_count == 1  # the new entry (with uf) is cached


@pytest.mark.asyncio
async def test_nominatim_slot_is_shared_across_client_instances(monkeypatch) -> None:
    """F07: two workers (two instances, one Redis) must not both fire inside the interval."""
    monkeypatch.setenv("RUN_REAL_EXTERNALS", "true")
    import brave.clients.nominatim as nom
    from brave.config.settings import NominatimConfig

    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        nom_redis.delete(nom.NOMINATIM_SLOT_KEY)  # the interval elapses while we wait

    monkeypatch.setattr(nom.asyncio, "sleep", fake_sleep)
    nom_redis = fakeredis.FakeRedis()
    config = NominatimConfig(min_request_interval=1.1)
    worker_a = nom.NominatimGeocoderClient(config=config, redis=nom_redis)
    worker_b = nom.NominatimGeocoderClient(config=config, redis=nom_redis)
    with respx.mock:
        respx.get(_NOMINATIM).mock(return_value=httpx.Response(200, json=_HIT))
        await worker_a.geocode("1", "A", "MT")
        assert sleeps == []
        await worker_b.geocode("2", "B", "MT")  # fresh instance: local gate is empty

    assert len(sleeps) == 1 and 0 < sleeps[0] <= 1.1


# ---------------------------------------------------------------------------
# _send: 5xx retry, network outage wait, session probe (24/10 PR sweep)
# ---------------------------------------------------------------------------

_PAGE = "https://www.tripadvisor.com.br/Attraction_Review-g1-d7-Reviews.html"
_LD_OK = (
    '<html><script type="application/ld+json">'
    + json.dumps({"@type": "LocalBusiness", "address": {"addressLocality": "Curitiba"}})
    + "</script></html>"
)


def _no_sleep(monkeypatch) -> list[float]:
    import brave.domains.tripadvisor.client as client_mod

    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(client_mod.asyncio, "sleep", fake_sleep)
    return sleeps


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [500, 502, 503, 504])
async def test_5xx_is_retried_inside_the_client(monkeypatch, status) -> None:
    """A transient TA 5xx (503 on one PR card, 24/10) must not quarantine the card."""
    sleeps = _no_sleep(monkeypatch)
    with respx.mock:
        route = respx.get(_PAGE).mock(
            side_effect=[httpx.Response(status), httpx.Response(200, text=_LD_OK)]
        )
        page = await _client().fetch_attraction_address(7)

    assert page["city_name"] == "Curitiba"
    assert route.call_count == 2 and sleeps == [1.0]


@pytest.mark.asyncio
async def test_5xx_that_persists_still_raises(monkeypatch) -> None:
    _no_sleep(monkeypatch)
    with respx.mock:
        route = respx.get(_PAGE).mock(return_value=httpx.Response(503))
        with pytest.raises(httpx.HTTPStatusError):
            await _client().fetch_attraction_address(7)

    assert route.call_count == 3  # 1 + 2 retries


@pytest.mark.asyncio
async def test_outage_waits_for_the_network_and_retries_the_same_request(monkeypatch) -> None:
    """Internet down mid-sweep: the request waits (polling) and resumes where it stopped,
    instead of quarantining every card; the session key keeps its TTL meanwhile."""
    import brave.domains.tripadvisor.client as client_mod

    sleeps = _no_sleep(monkeypatch)
    online = iter([False, False, True])  # down at the failure, down at one poll, then back
    monkeypatch.setattr(client_mod, "is_online", lambda: next(online))
    redis = fakeredis.FakeRedis()
    redis.set(client_mod.BRAVE_TA_SESSION_KEY, "{}", ex=5)
    seen_down: list[bool] = []

    def back(request):
        seen_down.append(redis.exists(client_mod.NETWORK_DOWN_KEY) == 1)
        return httpx.Response(200, text=_LD_OK)

    with respx.mock:
        route = respx.get(_PAGE).mock(side_effect=[httpx.ConnectError("down"), back])
        page = await _client(redis).fetch_attraction_address(7)

    assert page["city_name"] == "Curitiba" and route.call_count == 2
    assert len(sleeps) == 2  # two polls while offline, no retry backoff spent
    assert seen_down == [False]  # the flag is cleared once the network is back
    assert redis.ttl(client_mod.BRAVE_TA_SESSION_KEY) > 5  # slid while we waited


@pytest.mark.asyncio
async def test_transport_error_while_online_is_retried_then_raised(monkeypatch) -> None:
    """A connection error with the internet up is this request's: backoff, then raise."""
    import brave.domains.tripadvisor.client as client_mod

    sleeps = _no_sleep(monkeypatch)
    monkeypatch.setattr(client_mod, "is_online", lambda: True)
    with respx.mock:
        route = respx.get(_PAGE).mock(side_effect=httpx.ConnectError("reset"))
        with pytest.raises(httpx.ConnectError):
            await _client().fetch_attraction_address(7)

    assert route.call_count == 3 and sleeps == [1.0, 2.0]


@pytest.mark.asyncio
async def test_probe_session_checks_listing_and_attraction_page() -> None:
    """The listing kept answering 200 while DataDome 403'd the attraction pages (24/10):
    the probe must fail on the page and say which surface failed."""
    from brave.domains.tripadvisor.client import _PROBE_LOCATION_ID, SessionExpiredError

    page = f"https://www.tripadvisor.com.br/Attraction_Review-g1-d{_PROBE_LOCATION_ID}-Reviews.html"
    with respx.mock:
        listing = respx.post(_GQL).mock(
            return_value=httpx.Response(200, json=_page([_card(1)], totalResults=1))
        )
        respx.get(page).mock(return_value=httpx.Response(403))
        with pytest.raises(SessionExpiredError) as exc_info:
            await _client().probe_session()

    assert listing.call_count == 1
    assert "ta_probe:página" in exc_info.value.__notes__
