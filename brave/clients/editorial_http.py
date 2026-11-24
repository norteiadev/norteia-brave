"""Blog / KML fetcher for the editorial lane (viagensecaminhos.com + Google My Maps KML).

Async httpx with tenacity retry. The site is slow (~75 s per page), so the timeout is 180 s;
the concurrency cap (3) lives in the crawler, not here. 404/410 → None ("does not exist");
429/5xx and transport errors are retried, anything else raises.
"""

from __future__ import annotations

import httpx
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential

TIMEOUT_S = 180.0
_USER_AGENT = "territorial-research-bot/1.0 (respects robots.txt)"  # no company name (project rule)


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return isinstance(exc, httpx.TransportError)


class RealEditorialFetcher:
    def __init__(
        self, *, timeout_s: float = TIMEOUT_S, attempts: int = 3, backoff_s: float = 2.0
    ) -> None:
        self._attempts, self._backoff_s = attempts, backoff_s
        self._client = httpx.AsyncClient(
            timeout=timeout_s, follow_redirects=True, headers={"User-Agent": _USER_AGENT}
        )

    async def get_text(self, url: str) -> str | None:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self._attempts),
            wait=wait_exponential(multiplier=self._backoff_s, max=30),
            retry=retry_if_exception(_retryable),
            reraise=True,
        ):
            with attempt:
                resp = await self._client.get(url)
                if resp.status_code in (404, 410):
                    return None
                resp.raise_for_status()
                return resp.text
        return None  # unreachable: reraise=True

    async def aclose(self) -> None:
        await self._client.aclose()
