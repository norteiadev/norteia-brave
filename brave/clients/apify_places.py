"""RealApifyPlacesClient — Apify ``compass~crawler-google-places`` for places_provider="hybrid".

One actor run resolves MANY places: a run costs ~45-60 s fixed whether it carries 1 or 6
search strings (measured 2026-10-21), so callers batch (HybridPlacesClient.prefetch) and
``lookup`` matches each dataset item back to its input by ``searchString`` / ``inputPlaceId``.
A search string with no hit simply has no item → None.

Input that works across municípios in one run (6/6 right places, no locationQuery):
``"{nome}, {municipio} - {uf}"`` or ``"place_id:ChIJ..."``. Paid actor filters
(categoryFilterWords, searchMatching, placeMinimumStars) are never sent.

LGPD: ``scrapeReviewsPersonalData: false`` is sent, but review objects still carry reviewer
ids/counts/photos — ``normalize_apify_item`` keeps only publishedAtDate, stars and text.

Budget: brave:cost:apify:* counters (cost_guard). The run's projected cost is checked and
RESERVED before the run (a run takes minutes; every run checked meanwhile must see it), then
settled to max(``chargedEventCounts``, the projection from the items returned) — the counts
can still be preliminary right after the finish. A run that fails, or that Brave loses track
of, keeps its reservation. The token travels only as the
Authorization header — never in a URL, a log line or an error message.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import structlog
from tenacity import RetryCallState, retry, retry_if_exception, stop_after_attempt

from brave.clients.tavily import _is_retryable, _wait
from brave.observability.cost_guard import (
    pre_dispatch_check_apify,
    record_apify_spend,
    settle_apify_spend,
)
from brave.shared.exceptions import ProviderBalanceError

logger = structlog.get_logger(__name__)

APIFY_API = "https://api.apify.com/v2"

# USD per charged event, FREE plan (BRONZE+ are cheaper — these over-count, which is the safe
# side for a cap). ≈ US$0.0065 per place with 1 review.
USD_APIFY_PLACE_SCRAPED = 0.004
USD_APIFY_PLACE_DETAILS = 0.002
USD_APIFY_REVIEW = 0.0005
USD_APIFY_ACTOR_START = 0.00005
_EVENT_PRICES = {
    "place-scraped": USD_APIFY_PLACE_SCRAPED,
    "place-details-scraped": USD_APIFY_PLACE_DETAILS,
    "review-scraped": USD_APIFY_REVIEW,
    "apify-actor-start": USD_APIFY_ACTOR_START,
}
_ACTOR_START_EVENTS = 4  # one per GB of the run's 4096 MB

_TERMINAL = frozenset({"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"})
_WAIT_FOR_FINISH_S = 60  # Apify's max for one synchronous wait
_MAX_POLLS = 15  # x 60 s → a run still going after ~15 min is aborted, not awaited
_RUN_TIMEOUT_S = _MAX_POLLS * _WAIT_FOR_FINISH_S  # Apify's own stop for a run Brave lost
# 402s that clear on their own (another run holds the plan's memory/slots): not a wall.
_BUSY_402 = frozenset({"actor-memory-limit-exceeded", "concurrent-runs-limit-exceeded"})
# No Retry-After on a busy 402, and the run holding the memory lasts 45-900 s: wait it out
# in 30 s steps for up to ~5 min (the 429 backoff would give up after ~4 s).
_BUSY_WAIT_S = 30
_BUSY_ATTEMPTS = 11

_KEPT_FIELDS = (
    "placeId", "title", "location", "address", "city", "state", "phone", "website",
    "permanentlyClosed", "temporarilyClosed", "openingHours", "categories", "categoryName",
    "price", "reviewsCount", "totalScore",
)


def apify_query(nome: str, municipio: str, uf: str) -> str:
    """The search string one place is looked up by (and matched back by)."""
    return f"{nome}, {municipio} - {uf}" if municipio else f"{nome} - {uf}"


def normalize_apify_item(item: dict) -> dict:
    """PII-free subset of one dataset item; reviews reduced to date, stars and text."""
    out = {k: item.get(k) for k in _KEPT_FIELDS}
    out["reviews"] = [
        {"publishedAtDate": r.get("publishedAtDate"), "stars": r.get("stars"), "text": r.get("text")}
        for r in item.get("reviews") or []
    ]
    return out


def apify_run_cost(counts: dict[str, int]) -> float:
    """USD of one run from its chargedEventCounts (unknown events priced 0)."""
    return sum(_EVENT_PRICES.get(event, 0.0) * n for event, n in counts.items())


def _busy(r: httpx.Response) -> bool:
    try:
        body = r.json()
    except ValueError:
        return False
    return isinstance(body, dict) and (body.get("error") or {}).get("type") in _BUSY_402


def _busy_exc(exc: BaseException | None) -> bool:
    return isinstance(exc, httpx.HTTPStatusError) and _busy(exc.response)


def _post_retryable(exc: BaseException) -> bool:
    """Retry starting a run only when it surely did not start: 429, a busy 402, or no
    connection. A 5xx or a read timeout may come AFTER Apify created the run — a retry would
    start (and pay) a second one the guard never sees."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or _busy(exc.response)
    return isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout))


def _start_wait(state: RetryCallState) -> float:
    return _BUSY_WAIT_S if _busy_exc(state.outcome.exception()) else _wait(state)


def _start_stop(state: RetryCallState) -> bool:
    busy = _busy_exc(state.outcome.exception())
    return state.attempt_number >= (_BUSY_ATTEMPTS if busy else 3)


def _never_started(exc: BaseException) -> bool:
    """POST /runs failed in a way that proves no run exists (any 4xx, or no connection)."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code < 500
    return isinstance(exc, (ProviderBalanceError, httpx.ConnectError, httpx.ConnectTimeout))


def _projected_cost(n_places: int, n_reviews: int) -> float:
    return apify_run_cost({
        "place-scraped": n_places,
        "place-details-scraped": n_places,
        "review-scraped": n_reviews,
        "apify-actor-start": _ACTOR_START_EVENTS,
    })


async def _send(http: httpx.AsyncClient, method: str, path: str, **kw: Any) -> Any:
    r = await http.request(method, f"{APIFY_API}{path}", **kw)
    if r.status_code in (401, 402, 403) and not _busy(r):
        # Bad/revoked token, plan usage limit, or account disabled: none fixes itself.
        raise ProviderBalanceError("apify", f"Apify HTTP {r.status_code}: {r.text[:200]}")
    r.raise_for_status()
    return r.json()


# Idempotent reads (and abort): 429 / 5xx / connection retried.
_call = retry(
    retry=retry_if_exception(_is_retryable), stop=stop_after_attempt(3), wait=_wait, reraise=True,
)(_send)
# Starting a run: see _post_retryable; a busy 402 waits longer (_BUSY_WAIT_S).
_start = retry(
    retry=retry_if_exception(_post_retryable), stop=_start_stop, wait=_start_wait, reraise=True,
)(_send)


class RealApifyPlacesClient:
    """Async Apify Google-Places crawler client. ``lookup(queries)`` → {query: item | None}.

    Guard: RuntimeError when AppConfig().run_real_externals is False or the token is empty.

    Args:
        token:         BRAVE_APIFY_API_KEY.
        actor_id:      AppConfig.apify_actor_id ("compass~crawler-google-places").
        redis_client:  Redis for the Apify cost guard. None → no budget (scripts).
        daily_usd_cap: AppConfig.apify_daily_usd_cap.
        total_usd_cap: AppConfig.apify_total_usd_cap.
        timeout_s:     httpx timeout — above the 60 s synchronous waitForFinish.
    """

    def __init__(
        self,
        token: str,
        *,
        actor_id: str,
        redis_client: Any = None,
        daily_usd_cap: float,
        total_usd_cap: float,
        timeout_s: float = 90.0,
    ) -> None:
        from brave.config.settings import AppConfig

        if not AppConfig().run_real_externals:
            raise RuntimeError(
                "RealApifyPlacesClient: run_real_externals=False — "
                "use FakeApifyClient in default test suite. "
                "Set RUN_REAL_EXTERNALS=true to enable real API calls."
            )
        if not token:
            raise RuntimeError(
                "RealApifyPlacesClient: token is empty — "
                "set BRAVE_APIFY_API_KEY environment variable."
            )
        self._token = token
        self._actor_id = actor_id
        self._redis_client = redis_client
        self._daily_usd_cap = daily_usd_cap
        self._total_usd_cap = total_usd_cap
        self._timeout_s = timeout_s

    def check_budget(self, usd: float) -> None:
        """Raise (CostGuardError / ProviderBalanceError) when ``usd`` more would cross a cap."""
        if self._redis_client is not None:
            pre_dispatch_check_apify(
                self._redis_client, usd,
                daily_cap_usd=self._daily_usd_cap, total_cap_usd=self._total_usd_cap,
            )

    async def lookup(
        self, queries: list[str], *, max_reviews: int, location: str | None = None
    ) -> dict[str, dict | None]:
        """ONE actor run for every (deduped) query → {query: normalize_apify_item | None}.

        ``location`` (Apify ``locationQuery``, one per run) bounds the search to that area:
        a "Nome - RO" with none landed in Rio Branco, Salvador, even Lisboa (RO pilot).
        """
        queries = list(dict.fromkeys(queries))
        if not queries:
            return {}
        projected = _projected_cost(len(queries), len(queries) * max_reviews)
        self.check_budget(projected)
        reserved_at = datetime.now(UTC)
        self._record(projected)
        charged = projected  # a run that fails or is lost keeps its reservation
        run: dict[str, Any] = {}

        body = {
            "searchStringsArray": queries,
            "maxCrawledPlacesPerSearch": 1,
            "language": "pt-BR",
            "maxReviews": max_reviews,
            "reviewsSort": "newest",
            "scrapeReviewsPersonalData": False,
            "maxImages": 0,
        }
        if location:
            body["locationQuery"] = location
        headers = {"Authorization": f"Bearer {self._token}"}
        try:
            async with httpx.AsyncClient(timeout=self._timeout_s, headers=headers) as http:
                try:
                    run = (await _start(
                        http, "POST", f"/acts/{self._actor_id}/runs",
                        params={"timeout": _RUN_TIMEOUT_S}, json=body,
                    ))["data"]
                except Exception as exc:
                    if _never_started(exc):
                        charged = 0.0
                    raise
                run_id = run["id"]
                try:
                    for _ in range(_MAX_POLLS + 1):
                        if run.get("status") in _TERMINAL:
                            break
                        run = (await _call(
                            http, "GET", f"/actor-runs/{run_id}",
                            params={"waitForFinish": _WAIT_FOR_FINISH_S},
                        ))["data"]
                    status = run.get("status")
                    if status != "SUCCEEDED":
                        logger.error("apify_run_failed", run_id=run_id, status=status)
                        raise RuntimeError(f"Apify run {run_id} ended {status}")
                    items = await _call(
                        http, "GET", f"/datasets/{run['defaultDatasetId']}/items",
                        params={"clean": "true"},
                    )
                finally:
                    if run.get("status") not in _TERMINAL:
                        # Still going (or unknown): stop it, else it scrapes and bills on.
                        await self._abort(http, run_id)
            charged = _projected_cost(
                len(items), sum(len(it.get("reviews") or []) for it in items)
            )
        finally:
            charged = max(charged, apify_run_cost(run.get("chargedEventCounts") or {}))
            if self._redis_client is not None:
                settle_apify_spend(self._redis_client, projected, charged, reserved_at)

        results: dict[str, dict | None] = dict.fromkeys(queries)
        for item in items:
            key = item.get("searchString")
            if key not in results and item.get("inputPlaceId"):
                key = f"place_id:{item['inputPlaceId']}"
            if key in results and results[key] is None:
                results[key] = normalize_apify_item(item)
        logger.info(
            "apify_run_ok", run_id=run_id, queries=len(queries),
            hits=sum(v is not None for v in results.values()), usd=round(charged, 5),
        )
        return results

    async def _abort(self, http: httpx.AsyncClient, run_id: str) -> None:
        try:
            await _call(http, "POST", f"/actor-runs/{run_id}/abort")
            logger.warning("apify_run_aborted", run_id=run_id)
        except Exception as exc:  # noqa: BLE001 — never mask the error that got us here
            logger.error("apify_run_abort_failed", run_id=run_id, error=type(exc).__name__)

    def _record(self, usd: float) -> None:
        if self._redis_client is not None and usd > 0:
            record_apify_spend(self._redis_client, usd)
