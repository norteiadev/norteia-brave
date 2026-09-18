"""RealParallelClient — web search for the cascade copywriter (Parallel ``/v1/search``).

Replaces Tavily in the cascade (docs/poc/gemini-viability.md §29). Measured on the same 140
TripAdvisor atrativos: the lane's two ``cascade_queries`` in ONE request (``search_queries`` is
a list, billed as one ``sku_search``), mode ``turbo`` → 135/140 approved descriptions, 98% of
the facts the DeepSeek-chosen queries got and 15% more than Tavily, search p95 0.8 s, $0.001
per atrativo (Tavily: 2 requests, $0.016).

The response is returned WHOLE (``ParallelSearch.results`` is the raw API list): the lane
persists it in ``atrativo_buscas`` so descriptions can be regenerated later with another model
without paying the search again. ``fontes()`` renders the prompt context the way §29 measured.

Spend and guard mirror RealTavilyClient: daily USD cost guard before dispatch, spend recorded
after, RuntimeError when AppConfig().run_real_externals is False. Retries reuse Tavily's policy
(429 / 5xx / connection, honouring ``retry-after``).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx
import structlog
from tenacity import retry, retry_if_exception, stop_after_attempt

from brave.clients.tavily import _MAX_ATTEMPTS, _is_retryable, _wait
from brave.observability.cost_guard import pre_dispatch_check, record_spend

if TYPE_CHECKING:
    from brave.config.settings import LLMConfig

logger = structlog.get_logger(__name__)

PARALLEL_SEARCH_URL = "https://api.parallel.ai/v1/search"
# USD per request, 10 results included; each extra result costs USD_PER_EXTRA_RESULT
# (docs.parallel.ai/getting-started/pricing, 2026-09-14).
USD_PER_REQUEST: dict[str, float] = {"turbo": 0.001, "fast": 0.001, "basic": 0.005, "advanced": 0.005}
USD_PER_EXTRA_RESULT: float = 0.001
_INCLUDED_RESULTS = 10


def search_cost(mode: str, n_results: int) -> float:
    return USD_PER_REQUEST[mode] + USD_PER_EXTRA_RESULT * max(0, n_results - _INCLUDED_RESULTS)


@dataclass(frozen=True)
class ParallelSearch:
    """One paid search, as the API returned it plus what the lane needs to persist it."""

    search_id: str | None
    mode: str
    objective: str
    queries: list[str]
    results: list[dict[str, Any]]
    usage: Any
    warnings: Any
    usd: float
    latency_ms: int

    def fontes(self) -> str:
        """Prompt context: ``[title] url`` then the excerpts, one block per result."""
        return "\n\n".join(
            f"[{r.get('title') or ''}] {r.get('url') or ''}\n" + "\n".join(r.get("excerpts") or [])
            for r in self.results
        )


class RealParallelClient:
    """Async Parallel search client. ``search(queries, objective)`` → ParallelSearch.

    Args:
        api_key:      PARALLEL_API_KEY (AppConfig.parallel_api_key).
        mode:         turbo | fast | basic | advanced (§29 measured turbo best).
        redis_client: Optional Redis for the daily USD cost guard. None → guard skipped.
        llm_config:   LLMConfig carrying usd_daily_budget — required with redis_client.
        http_client:  Optional httpx.AsyncClient (tests / measurement hooks).
    """

    def __init__(
        self,
        api_key: str,
        *,
        mode: str = "turbo",
        redis_client: Any = None,
        llm_config: LLMConfig | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        from brave.config.settings import AppConfig

        if not AppConfig().run_real_externals:
            raise RuntimeError(
                "RealParallelClient: run_real_externals=False — "
                "set RUN_REAL_EXTERNALS=true to enable real searches."
            )
        if not api_key:
            raise RuntimeError("RealParallelClient: PARALLEL_API_KEY is empty.")
        if mode not in USD_PER_REQUEST:
            raise ValueError(f"RealParallelClient: unknown mode {mode!r}.")
        if redis_client is not None and llm_config is None:
            raise ValueError("RealParallelClient: redis_client needs llm_config (the budget).")
        self._api_key = api_key
        self._mode = mode
        self._redis_client = redis_client
        self._llm_config = llm_config
        self._http = http_client or httpx.AsyncClient(timeout=30.0)

    @retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(_MAX_ATTEMPTS),
        wait=_wait,
        reraise=True,
    )
    async def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        r = await self._http.post(PARALLEL_SEARCH_URL, headers={"x-api-key": self._api_key}, json=body)
        r.raise_for_status()
        return r.json()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def search(self, queries: list[str], objective: str) -> ParallelSearch:
        """Run one search carrying every query. Raises CostGuardError before dispatch."""
        if self._redis_client is not None:
            pre_dispatch_check(self._redis_client, self._llm_config)  # type: ignore[arg-type]
        t = time.perf_counter()
        d = await self._post({"objective": objective, "search_queries": queries, "mode": self._mode})
        results = d.get("results") or []
        busca = ParallelSearch(
            search_id=d.get("search_id"),
            mode=self._mode,
            objective=objective,
            queries=list(queries),
            results=results,
            usage=d.get("usage"),
            warnings=d.get("warnings"),
            usd=search_cost(self._mode, len(results)),
            latency_ms=round((time.perf_counter() - t) * 1000),
        )
        if self._redis_client is not None:
            record_spend(self._redis_client, busca.usd)
        logger.info("parallel_search_ok", results=len(results), latency_ms=busca.latency_ms)
        return busca
