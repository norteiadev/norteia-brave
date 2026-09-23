"""In-package offline search client (production-safe) — the Null twin of RealParallelClient.

Used by the cascade copywriter when AppConfig.run_real_externals is False: every search
comes back empty, with no network call and no spend.
"""

from __future__ import annotations

from brave.clients.parallel import ParallelSearch


class NullSearchClient:
    """No-network search client: ``search(queries, objective)`` → an empty ParallelSearch."""

    async def search(self, queries: list[str], objective: str) -> ParallelSearch:
        return ParallelSearch(
            search_id=None,
            mode="null",
            objective=objective,
            queries=list(queries),
            results=[],
            usage=None,
            warnings=None,
            usd=0.0,
            latency_ms=0,
        )

    async def aclose(self) -> None:
        pass
