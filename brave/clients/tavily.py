"""RealTavilyClient — web search for the cascade copywriter (Tavily ``/search``).

The cascade replaces the server-side ``web_search`` inside the copywriter with a search the
lane runs ITSELF, so the context can be inspected before any model is called (the mention
gate, brave.lanes.atrativos.grounding). Measured on 50 real TripAdvisor atrativos
(docs/poc/gemini-viability.md §24): 98% of contexts mention the atrativo, 1,247 tokens each.

Shape of the returned context is the one §24 measured: per result, ``title``, ``url`` and the
extractive ``content`` snippet, blank-line separated. The URL stays — it is evidence (the slug
carries facts the snippet cuts, §22.4) and costs ~10 tokens.

Spend: $0.008 per basic search (pay-as-you-go credit). When a Redis client is given, every
search runs the daily USD cost guard BEFORE dispatch and records its spend after — the same
counter the LLM calls feed. Without it, switching the lane to the cascade would make the
recorded cost per description drop ~75% while the real bill did not: the search IS the bill.

Guard: raises RuntimeError when AppConfig().run_real_externals is False. Tests mock the
network with respx; there is no Null client because the cascade is only ever built under
run_real_externals (see brave.tasks.pipeline).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
import structlog
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from brave.observability.cost_guard import pre_dispatch_check, record_spend
from brave.shared.exceptions import raise_if_balance_wall

if TYPE_CHECKING:
    from brave.config.settings import LLMConfig

logger = structlog.get_logger(__name__)

TAVILY_SEARCH_URL = "https://api.tavily.com/search"
USD_PER_SEARCH: float = 0.008
_MAX_RESULTS = 5


# Measured (§25): a development key allows 100 RPM and answers the excess with 429 +
# ``retry-after: 60``. The old 2-10 s backoff ran out of attempts inside that minute, so every
# atrativo in flight failed in ~4.5 s (84 of 150 at concurrency 8) and each failure burned a
# descricao_attempts on the record. Honouring the header turns the wall into backpressure. 4
# attempts = at most 3 waits of 60 s, which fits the 300 s enrich_places time limit.
_MAX_ATTEMPTS = 4
_MAX_RETRY_AFTER_S = 60.0
_backoff = wait_exponential(multiplier=1, min=2, max=10)


def _wait(retry_state: Any) -> float:
    """Wait what the server asked for (capped), else exponential backoff."""
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    if isinstance(exc, httpx.HTTPStatusError):
        try:
            return min(float(exc.response.headers["retry-after"]), _MAX_RETRY_AFTER_S)
        except (KeyError, ValueError):
            pass
    return _backoff(retry_state)


def _is_retryable(exc: BaseException) -> bool:
    """429 / 5xx / connection errors are retryable. 432/433 (plan or credit limit) are not —
    retrying a quota wall only burns the backoff."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return isinstance(exc, (httpx.TimeoutException, httpx.ConnectError))


def format_results(results: list[dict[str, Any]]) -> str:
    """One block per result — title, URL, snippet — as it enters the prompt."""
    blocks = []
    for it in results:
        linhas = [it.get("title") or "", it.get("url") or "", it.get("content") or ""]
        blocks.append("\n".join(x for x in linhas if x))
    return "\n\n".join(b for b in blocks if b)


class RealTavilyClient:
    """Async Tavily search client. ``search(query)`` → prompt-ready context string.

    Args:
        api_key:      TAVILY_API_KEY (AppConfig.tavily_api_key).
        redis_client: Optional Redis for the daily USD cost guard. None → guard skipped.
        llm_config:   LLMConfig carrying usd_daily_budget — required with redis_client.
        http_client:  Optional httpx.AsyncClient (tests / measurement hooks).
    """

    def __init__(
        self,
        api_key: str,
        *,
        redis_client: Any = None,
        llm_config: LLMConfig | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        from brave.config.settings import AppConfig

        if not AppConfig().run_real_externals:
            raise RuntimeError(
                "RealTavilyClient: run_real_externals=False — "
                "set RUN_REAL_EXTERNALS=true to enable real searches."
            )
        if not api_key:
            raise RuntimeError("RealTavilyClient: TAVILY_API_KEY is empty.")
        if redis_client is not None and llm_config is None:
            raise ValueError("RealTavilyClient: redis_client needs llm_config (the budget).")
        self._api_key = api_key
        self._redis_client = redis_client
        self._llm_config = llm_config
        self._http = http_client or httpx.AsyncClient(timeout=30.0)

    @retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(_MAX_ATTEMPTS),
        wait=_wait,
        reraise=True,
    )
    async def _post(self, query: str) -> list[dict[str, Any]]:
        r = await self._http.post(
            TAVILY_SEARCH_URL,
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={"query": query, "search_depth": "basic", "max_results": _MAX_RESULTS},
        )
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise_if_balance_wall("tavily", status_code=exc.response.status_code)
            raise
        return r.json().get("results") or []

    async def search(self, query: str) -> str:
        """Run one basic search. Raises CostGuardError before dispatch when over budget."""
        if self._redis_client is not None:
            pre_dispatch_check(self._redis_client, self._llm_config)  # type: ignore[arg-type]
        results = await self._post(query)
        if self._redis_client is not None:
            record_spend(self._redis_client, USD_PER_SEARCH)
        logger.info("tavily_search_ok", results=len(results))
        return format_results(results)
