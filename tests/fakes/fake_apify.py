"""Fake Apify Google-Places client for offline tests (RealApifyPlacesClient's surface).

    fake = FakeApifyClient({"Morro do Moreno, Vila Velha - ES": item, "X - ES": None})
    found = await fake.lookup(["Morro do Moreno, Vila Velha - ES"], max_reviews=1)

``fixture`` maps an exact query string → normalized item (``normalize_apify_item``
shape) or None (no hit). Unknown queries are a no-hit too.
"""

from typing import Any


class FakeApifyClient:
    def __init__(
        self,
        fixture: dict[str, dict[str, Any] | None] | None = None,
        raise_exc: BaseException | None = None,
    ) -> None:
        self._fixture = fixture or {}
        self._raise_exc = raise_exc
        self.lookup_calls: list[dict[str, Any]] = []  # {"queries", "max_reviews"[, "location"]} per run
        self.check_budget_calls: list[float] = []

    async def lookup(
        self, queries: list[str], *, max_reviews: int, location: str | None = None
    ) -> dict[str, dict[str, Any] | None]:
        if not queries:
            return {}
        call: dict[str, Any] = {"queries": list(queries), "max_reviews": max_reviews}
        if location is not None:
            call["location"] = location
        self.lookup_calls.append(call)
        if self._raise_exc is not None:
            raise self._raise_exc
        return {q: self._fixture.get(q) for q in dict.fromkeys(queries)}

    def check_budget(self, usd: float) -> None:
        self.check_budget_calls.append(usd)
