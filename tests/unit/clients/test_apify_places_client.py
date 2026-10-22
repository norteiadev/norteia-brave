"""Offline unit tests for RealApifyPlacesClient (places_provider="hybrid").

respx mocks the Apify endpoints (POST /acts/{actor}/runs, GET /actor-runs/{id},
GET /datasets/{id}/items, POST /actor-runs/{id}/abort); fakeredis holds the Apify cost
counters. No network.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

ACTOR = "compass~crawler-google-places"
RUNS_URL = f"https://api.apify.com/v2/acts/{ACTOR}/runs"
RUN_URL = "https://api.apify.com/v2/actor-runs/run1"
ABORT_URL = f"{RUN_URL}/abort"
ITEMS_URL = "https://api.apify.com/v2/datasets/ds1/items"
TOKEN = "apify_api_test_token"

Q_NAME = "Convento da Penha, Vila Velha - ES"
Q_ID = "place_id:ChIJixdqrlIWuAARlfIafNUX-qk"
Q_MISS = "Lugar Inexistente, Vila Velha - ES"

# Real item shape (2026-10-21) trimmed: reviewer fields come even with
# scrapeReviewsPersonalData=false — the client must drop them.
ITEM_NAME = {
    "searchString": Q_NAME,
    "placeId": "ChIJNz-RikkWuAARkw_BYhNBxaI",
    "title": "Convento da Penha",
    "location": {"lat": -20.33, "lng": -40.28},
    "address": "R. Vasco Coutinho, s/n - Prainha, Vila Velha - ES",
    "city": "Vila Velha",
    "state": "Espírito Santo",
    "phone": "+55 27 3329-0420",
    "website": "https://conventodapenha.org.br/",
    "permanentlyClosed": False,
    "temporarilyClosed": False,
    "openingHours": [{"day": "quarta-feira", "hours": "05:30 to 19:00"}],
    "categories": ["Convento"],
    "categoryName": "Convento",
    "price": None,
    "reviewsCount": 30128,
    "totalScore": 4.8,
    "imageUrl": "https://example.invalid/img.jpg",
    "reviews": [{
        "publishedAtDate": "2026-10-21T10:00:00.000Z",
        "stars": 5,
        "text": "Lindo!",
        "name": "Fulano de Tal",
        "reviewerId": "123",
        "reviewerUrl": "https://example.invalid/r/123",
        "reviewerPhotoUrl": "https://example.invalid/p.jpg",
        "reviewerNumberOfReviews": 40,
        "isLocalGuide": True,
        "reviewId": "abc",
        "reviewImageUrls": ["https://example.invalid/x.jpg"],
        "likesCount": 2,
        "reviewContext": {"Visitou em": "Fim de semana"},
    }],
}
# A place_id input is matched by inputPlaceId even if searchString were not echoed verbatim.
ITEM_ID = {
    "searchString": "something else",
    "inputPlaceId": "ChIJixdqrlIWuAARlfIafNUX-qk",
    "placeId": "ChIJixdqrlIWuAARlfIafNUX-qk",
    "title": "Morro do Moreno",
    "openingHours": [{"day": "quarta-feira", "hours": "Atendimento 24 horas"}],
    "permanentlyClosed": False,
    "temporarilyClosed": True,
    "reviews": [],
}

COUNTS = {"place-scraped": 2, "place-details-scraped": 2, "review-scraped": 1, "apify-actor-start": 4}
COUNTS_USD = 2 * 0.004 + 2 * 0.002 + 1 * 0.0005 + 4 * 0.00005
ONE_PLACE_USD = 0.004 + 0.002 + 0.0005 + 4 * 0.00005  # projection: 1 query, 1 review


def _run(status="SUCCEEDED", counts=None):
    return {"data": {
        "id": "run1", "status": status, "defaultDatasetId": "ds1",
        "chargedEventCounts": COUNTS if counts is None else counts,
    }}


def _client(monkeypatch, redis=None, *, daily=5.0, total=5.0):
    from tenacity import wait_none

    from brave.clients import apify_places
    from brave.clients.apify_places import RealApifyPlacesClient

    monkeypatch.setenv("RUN_REAL_EXTERNALS", "true")
    monkeypatch.setattr(apify_places._call.retry, "wait", wait_none())
    monkeypatch.setattr(apify_places._start.retry, "wait", wait_none())
    return RealApifyPlacesClient(
        TOKEN, actor_id=ACTOR, redis_client=redis, daily_usd_cap=daily, total_usd_cap=total
    )


def _assert_token_only_in_headers() -> None:
    """Every request (start, polls, dataset, abort): Bearer header, never the URL."""
    assert respx.calls
    for call in respx.calls:
        assert call.request.headers["Authorization"] == f"Bearer {TOKEN}"
        assert TOKEN not in str(call.request.url)


def test_guard_raises_without_real_externals_or_token(monkeypatch):
    from brave.clients.apify_places import RealApifyPlacesClient

    monkeypatch.delenv("RUN_REAL_EXTERNALS", raising=False)
    with pytest.raises(RuntimeError, match="run_real_externals=False"):
        RealApifyPlacesClient(TOKEN, actor_id=ACTOR, daily_usd_cap=5, total_usd_cap=5)
    monkeypatch.setenv("RUN_REAL_EXTERNALS", "true")
    with pytest.raises(RuntimeError, match="BRAVE_APIFY_API_KEY"):
        RealApifyPlacesClient("", actor_id=ACTOR, daily_usd_cap=5, total_usd_cap=5)


@respx.mock
async def test_lookup_one_run_keyed_back_pii_stripped_cost_recorded(monkeypatch, fake_redis):
    from brave.observability.cost_guard import apify_daily_spend, apify_total_spend, daily_spend

    runs = respx.post(RUNS_URL).mock(return_value=httpx.Response(201, json=_run()))
    respx.get(ITEMS_URL).mock(return_value=httpx.Response(200, json=[ITEM_NAME, ITEM_ID]))

    out = await _client(monkeypatch, fake_redis).lookup(
        [Q_NAME, Q_ID, Q_MISS, Q_NAME], max_reviews=1
    )

    assert runs.call_count == 1, "one actor run for every query"
    _assert_token_only_in_headers()
    req = runs.calls[0].request
    # Started without a held connection (a gateway 5xx there is ambiguous), Apify-side timeout.
    assert "waitForFinish" not in req.url.params
    assert req.url.params["timeout"] == "900"
    body = json.loads(req.content)
    assert body["searchStringsArray"] == [Q_NAME, Q_ID, Q_MISS], "deduped, order kept"
    assert body["scrapeReviewsPersonalData"] is False
    assert body["maxReviews"] == 1 and body["reviewsSort"] == "newest"
    assert body["maxCrawledPlacesPerSearch"] == 1 and body["maxImages"] == 0
    assert not {"categoryFilterWords", "searchMatching", "placeMinimumStars"} & body.keys()

    assert set(out) == {Q_NAME, Q_ID, Q_MISS}
    assert out[Q_MISS] is None, "no item for the search → conclusive no-hit"
    assert out[Q_ID]["title"] == "Morro do Moreno" and out[Q_ID]["temporarilyClosed"] is True
    name = out[Q_NAME]
    assert name["placeId"] == "ChIJNz-RikkWuAARkw_BYhNBxaI"
    assert name["phone"] == "+55 27 3329-0420"
    assert name["openingHours"] == [{"day": "quarta-feira", "hours": "05:30 to 19:00"}]
    assert name["reviews"] == [
        {"publishedAtDate": "2026-10-21T10:00:00.000Z", "stars": 5, "text": "Lindo!"}
    ], "LGPD: only date, stars and text survive"
    assert "imageUrl" not in name

    assert apify_daily_spend(fake_redis) == pytest.approx(COUNTS_USD)
    assert apify_total_spend(fake_redis) == pytest.approx(COUNTS_USD)
    assert daily_spend(fake_redis) == 0.0, "Apify must not consume the LLM budget"


async def test_empty_lookup_makes_no_call(monkeypatch):
    with respx.mock(assert_all_called=False) as mock:
        assert await _client(monkeypatch).lookup([], max_reviews=1) == {}
        assert not mock.calls


@respx.mock
async def test_non_terminal_run_is_polled_until_done(monkeypatch):
    respx.post(RUNS_URL).mock(return_value=httpx.Response(201, json=_run(status="RUNNING")))
    poll = respx.get(RUN_URL).mock(side_effect=[
        httpx.Response(200, json=_run(status="RUNNING")),
        httpx.Response(200, json=_run()),
    ])
    respx.get(ITEMS_URL).mock(return_value=httpx.Response(200, json=[ITEM_NAME]))

    out = await _client(monkeypatch).lookup([Q_NAME], max_reviews=1)
    assert poll.call_count == 2
    assert poll.calls[0].request.url.params["waitForFinish"] == "60", "Apify's max"
    assert out[Q_NAME]["title"] == "Convento da Penha"
    _assert_token_only_in_headers()


@respx.mock
async def test_run_still_going_after_the_polls_is_aborted_and_keeps_its_reservation(
    monkeypatch, fake_redis
):
    from brave.clients import apify_places
    from brave.observability.cost_guard import apify_total_spend

    respx.post(RUNS_URL).mock(return_value=httpx.Response(201, json=_run(status="RUNNING")))
    respx.get(RUN_URL).mock(return_value=httpx.Response(
        200, json=_run(status="RUNNING", counts={"apify-actor-start": 4})
    ))
    abort = respx.post(ABORT_URL).mock(return_value=httpx.Response(200, json=_run("ABORTING")))
    client = _client(monkeypatch, fake_redis)
    monkeypatch.setattr(apify_places, "_MAX_POLLS", 2)

    with pytest.raises(RuntimeError, match="RUNNING"):
        await client.lookup([Q_NAME], max_reviews=1)
    assert abort.call_count == 1, "a run Brave stops waiting for must not bill on"
    assert apify_total_spend(fake_redis) == pytest.approx(ONE_PLACE_USD)
    _assert_token_only_in_headers()


@respx.mock
async def test_dataset_read_failure_aborts_nothing_and_keeps_the_reservation(
    monkeypatch, fake_redis
):
    from brave.observability.cost_guard import apify_total_spend

    respx.post(RUNS_URL).mock(return_value=httpx.Response(201, json=_run()))
    respx.get(ITEMS_URL).mock(return_value=httpx.Response(503))
    abort = respx.post(ABORT_URL)

    with pytest.raises(httpx.HTTPStatusError):
        await _client(monkeypatch, fake_redis).lookup([Q_NAME], max_reviews=1)
    assert not abort.called  # it SUCCEEDED: nothing left running
    assert apify_total_spend(fake_redis) == pytest.approx(max(ONE_PLACE_USD, COUNTS_USD))


@respx.mock
async def test_ambiguous_start_failure_is_not_retried_and_keeps_the_reservation(
    monkeypatch, fake_redis
):
    """A gateway 504 may come after Apify created the run: a retry would start a 2nd one."""
    from brave.observability.cost_guard import apify_total_spend

    runs = respx.post(RUNS_URL).mock(return_value=httpx.Response(504))
    with pytest.raises(httpx.HTTPStatusError):
        await _client(monkeypatch, fake_redis).lookup([Q_NAME], max_reviews=1)
    assert runs.call_count == 1
    assert apify_total_spend(fake_redis) == pytest.approx(ONE_PLACE_USD)


@respx.mock
async def test_the_reservation_is_visible_while_the_run_goes(monkeypatch, fake_redis):
    from brave.observability.cost_guard import apify_daily_spend, apify_total_spend

    seen = []

    def start(request):
        seen.append((apify_daily_spend(fake_redis), apify_total_spend(fake_redis)))
        return httpx.Response(201, json=_run(counts={}))

    respx.post(RUNS_URL).mock(side_effect=start)
    respx.get(ITEMS_URL).mock(return_value=httpx.Response(200, json=[ITEM_NAME, ITEM_ID]))
    await _client(monkeypatch, fake_redis).lookup([Q_NAME, Q_ID, Q_MISS], max_reviews=1)

    three = 3 * (0.004 + 0.002 + 0.0005) + 4 * 0.00005
    assert seen == [(pytest.approx(three), pytest.approx(three))], "reserved before the run"
    # Settled to the 2 items found: the miss's share is given back.
    assert apify_daily_spend(fake_redis) == pytest.approx(COUNTS_USD)
    assert apify_total_spend(fake_redis) == pytest.approx(COUNTS_USD)


@pytest.mark.parametrize("status", [401, 402, 403])
@respx.mock
async def test_auth_or_billing_wall_is_provider_balance(monkeypatch, fake_redis, status):
    from brave.observability.cost_guard import apify_total_spend
    from brave.shared.exceptions import ProviderBalanceError

    runs = respx.post(RUNS_URL).mock(
        return_value=httpx.Response(status, json={"error": {"type": "not-enough-usage"}})
    )
    with pytest.raises(ProviderBalanceError) as exc:
        await _client(monkeypatch, fake_redis).lookup([Q_NAME], max_reviews=1)
    assert exc.value.provider == "apify"
    assert runs.call_count == 1, "a wall is not retried"
    assert apify_total_spend(fake_redis) == 0.0, "no run started: reservation given back"


@pytest.mark.parametrize("kind", ["actor-memory-limit-exceeded", "concurrent-runs-limit-exceeded"])
@respx.mock
async def test_busy_402_is_retried_not_a_balance_wall(monkeypatch, kind):
    runs = respx.post(RUNS_URL).mock(side_effect=[
        httpx.Response(402, json={"error": {"type": kind}}), httpx.Response(201, json=_run()),
    ])
    respx.get(ITEMS_URL).mock(return_value=httpx.Response(200, json=[ITEM_NAME]))

    out = await _client(monkeypatch).lookup([Q_NAME], max_reviews=1)
    assert runs.call_count == 2 and out[Q_NAME] is not None


@respx.mock
async def test_busy_402_outlasts_the_429_backoff(monkeypatch):
    """The run holding the plan's memory lasts 45-900 s and a busy 402 has no Retry-After:
    3 tries in ~4 s (the 429 backoff) give up long before it frees — 30 s steps, ~5 min."""
    from types import SimpleNamespace

    from brave.clients import apify_places

    busy = httpx.Response(402, json={"error": {"type": "actor-memory-limit-exceeded"}})
    runs = respx.post(RUNS_URL).mock(side_effect=[busy] * 5 + [httpx.Response(201, json=_run())])
    respx.get(ITEMS_URL).mock(return_value=httpx.Response(200, json=[ITEM_NAME]))
    out = await _client(monkeypatch).lookup([Q_NAME], max_reviews=1)
    assert runs.call_count == 6 and out[Q_NAME] is not None

    exc = httpx.HTTPStatusError("busy", request=httpx.Request("POST", RUNS_URL), response=busy)
    state = SimpleNamespace(outcome=SimpleNamespace(exception=lambda: exc), attempt_number=3)
    assert apify_places._start_wait(state) == 30
    assert apify_places._start_stop(state) is False


@respx.mock
async def test_429_is_retried_then_succeeds(monkeypatch):
    runs = respx.post(RUNS_URL).mock(side_effect=[
        httpx.Response(429), httpx.Response(201, json=_run()),
    ])
    respx.get(ITEMS_URL).mock(return_value=httpx.Response(200, json=[ITEM_NAME]))

    out = await _client(monkeypatch).lookup([Q_NAME], max_reviews=1)
    assert runs.call_count == 2
    assert out[Q_NAME] is not None


@respx.mock
async def test_failed_run_raises_and_keeps_its_reservation(monkeypatch, fake_redis):
    from brave.observability.cost_guard import apify_total_spend

    respx.post(RUNS_URL).mock(return_value=httpx.Response(
        201, json=_run(status="FAILED", counts={"apify-actor-start": 4})
    ))
    items = respx.get(ITEMS_URL)

    with pytest.raises(RuntimeError, match="FAILED"):
        await _client(monkeypatch, fake_redis).lookup([Q_NAME], max_reviews=1)
    assert not items.called
    # Its counts may still be preliminary: the projection stands.
    assert apify_total_spend(fake_redis) == pytest.approx(ONE_PLACE_USD)


@respx.mock
async def test_preliminary_counts_are_floored_at_the_items_projection(monkeypatch, fake_redis):
    from brave.observability.cost_guard import apify_total_spend

    respx.post(RUNS_URL).mock(return_value=httpx.Response(
        201, json=_run(counts={"apify-actor-start": 4})  # places not counted yet
    ))
    respx.get(ITEMS_URL).mock(return_value=httpx.Response(200, json=[ITEM_NAME, ITEM_ID]))

    await _client(monkeypatch, fake_redis).lookup([Q_NAME, Q_ID, Q_MISS], max_reviews=1)
    # 2 items found (not 3 queries), 1 review among them, 4 start events.
    assert apify_total_spend(fake_redis) == pytest.approx(COUNTS_USD)


@respx.mock
async def test_counts_above_the_projection_are_what_is_recorded(monkeypatch, fake_redis):
    from brave.observability.cost_guard import apify_total_spend

    counts = {**COUNTS, "review-scraped": 41}
    respx.post(RUNS_URL).mock(return_value=httpx.Response(201, json=_run(counts=counts)))
    respx.get(ITEMS_URL).mock(return_value=httpx.Response(200, json=[ITEM_NAME, ITEM_ID]))

    await _client(monkeypatch, fake_redis).lookup([Q_NAME, Q_ID], max_reviews=1)
    assert apify_total_spend(fake_redis) == pytest.approx(COUNTS_USD + 40 * 0.0005)


@respx.mock
async def test_projected_cost_over_cap_blocks_the_run(monkeypatch, fake_redis):
    from brave.observability.cost_guard import record_apify_spend
    from brave.shared.exceptions import CostGuardError, ProviderBalanceError

    runs = respx.post(RUNS_URL)
    # 10 places x (0.004 + 0.002) + 10 reviews x 0.0005 + start = 0.0652 > the 0.06 left.
    record_apify_spend(fake_redis, 0.94)
    queries = [f"Lugar {i} - ES" for i in range(10)]
    with pytest.raises(CostGuardError):
        await _client(monkeypatch, fake_redis, daily=1.0, total=50.0).lookup(queries, max_reviews=1)
    with pytest.raises(ProviderBalanceError):
        await _client(monkeypatch, fake_redis, daily=50.0, total=1.0).lookup(queries, max_reviews=1)
    assert not runs.called
