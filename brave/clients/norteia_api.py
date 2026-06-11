"""Real NorteiaApiClient — httpx + tenacity retry for Mar push (D-15, D-16, CORE-05).

Implements NorteiaApiClientProtocol (brave/clients/base.py).

Service-to-service auth: Bearer token in Authorization header (T-03-01).
Token is read from settings at instantiation; never logged (structlog omits
request headers by default and we do not manually log them here).

Retry policy (T-03-05):
  - 5xx responses: tenacity stop_after_attempt(3), wait_exponential(min=2, max=10)
  - 4xx: no retry — client error, raise immediately
  - Connection errors: retried (tenacity retry_if_exception_type)

Usage pattern:
    client = NorteiaApiClient(base_url=settings.norteia_api_url, service_token=token)
    async with client as c:
        result = await c.push_destination(payload)

Or inject http_client for testing:
    async with httpx.AsyncClient() as http_client:
        client = NorteiaApiClient(base_url=..., service_token=..., http_client=http_client)
        result = await client.push_destination(payload)
"""

from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)


def _is_5xx(exc: BaseException) -> bool:
    """Return True if exc is an httpx.HTTPStatusError for a 5xx response."""
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code >= 500


class NorteiaApiClient:
    """Real HTTP client for pushing Mar records to norteia-api.

    Implements NorteiaApiClientProtocol (structural typing — no isinstance checks).

    Constructor:
        base_url:     Base URL of the norteia-api (e.g. "https://api.norteia.app").
        service_token: Bearer token for the Authorization header (env var, never logged).
        http_client:  Optional injected httpx.AsyncClient (allows respx mocking in tests).
                      If None, a new client is created in __aenter__ and closed in __aexit__.

    Retry policy (T-03-05):
        5xx responses → stop_after_attempt(3), wait_exponential(min=2, max=10)
        4xx → raise immediately (client error, no retry)
        Connection errors → retry (transient network flap)
    """

    def __init__(
        self,
        base_url: str | Any,
        service_token: str,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = str(base_url).rstrip("/")
        self._service_token = service_token
        self._injected_client = http_client
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "NorteiaApiClient":
        if self._injected_client is not None:
            self._client = self._injected_client
        else:
            self._client = httpx.AsyncClient()
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._injected_client is None and self._client is not None:
            await self._client.aclose()
        self._client = None

    def _headers(self) -> dict[str, str]:
        """Build request headers with Bearer auth (T-03-01)."""
        return {
            "Authorization": f"Bearer {self._service_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """POST to {base_url}/{path} with retry on 5xx.

        Uses tenacity retry: up to 3 attempts, exponential backoff (min=2s, max=10s).
        Retries only on 5xx (T-03-05). Raises immediately on 4xx.
        """
        if self._client is None:
            raise RuntimeError(
                "NorteiaApiClient must be used as an async context manager. "
                "Use `async with NorteiaApiClient(...) as client:` or inject http_client."
            )

        client = self._client  # capture for closure below

        @retry(
            retry=retry_if_exception(_is_5xx),
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=2, max=10),
            reraise=True,
        )
        async def _attempt() -> dict[str, Any]:
            response = await client.post(
                f"{self._base_url}{path}",
                json=payload,
                headers=self._headers(),
            )
            response.raise_for_status()
            return response.json()

        return await _attempt()

    async def push_destination(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Push a canonical destination Mar record to norteia-api.

        Endpoint: POST /api/internal/territorial/destinations

        Args:
            payload: Mar push payload matching the Pact contract shape:
                {
                    "source": str,
                    "source_ref": str,          # idempotency key (D-15)
                    "entity_type": "destination",
                    "canonical": {"name": str, "uf": str, "municipio": str},
                    "reliability_score": float,
                    "score_version": str,
                    "provenance": {             # flat per-criterion floats (D-16)
                        "origem": float,
                        "completude": float,
                        "corroboracao": float,
                        "atualidade": float,
                        "validacao_humana": float,
                    }
                }

        Returns:
            Response dict from norteia-api: {"id": str, "source_ref": str}.

        Raises:
            httpx.HTTPStatusError: On 4xx (no retry) or 5xx after 3 attempts.
        """
        return await self._post("/api/internal/territorial/destinations", payload)

    async def push_attraction(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Push a canonical attraction Mar record to norteia-api.

        Endpoint: POST /api/internal/territorial/attractions

        Args:
            payload: Mar push payload matching the Pact contract shape (same
                     structure as push_destination but entity_type="attraction").

        Returns:
            Response dict from norteia-api: {"id": str, "source_ref": str}.

        Raises:
            httpx.HTTPStatusError: On 4xx (no retry) or 5xx after 3 attempts.
        """
        return await self._post("/api/internal/territorial/attractions", payload)
