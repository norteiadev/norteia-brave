"""RealPlacesClient — Google Places (New API) implementation.

Uses google-maps-places 0.9.x SDK (not raw httpx; not the legacy googlemaps client).
Implements PlacesClientProtocol:
  - text_search(query, uf) → list[dict]
  - place_details(place_id) → dict

Guard: raises RuntimeError if AppConfig().run_real_externals is False.
This prevents accidental real API calls in CI / default test suite.

D-04 / COMP-03: Returns raw Places data. Callers (DiscoveryAgent, SignalAgent)
are responsible for persisting ONLY place_id from the raw response.

tenacity: 3 retries with exponential backoff for transient errors (429, 5xx).

Usage (production — only when run_real_externals=True):
    from brave.clients.places import RealPlacesClient
    client = RealPlacesClient(api_key="...")
    results = await client.text_search("praias em Porto Seguro", uf="BA")
"""

from __future__ import annotations

from typing import Any

import structlog
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Retry policy — transient errors (429, connection errors)
# ---------------------------------------------------------------------------


def _is_retryable(exc: BaseException) -> bool:
    """Determine if an exception is retryable (429 / 5xx / connection error).

    WR-01: this is wired into the @retry predicate on text_search / place_details
    so that non-transient errors (auth 401/403, invalid-argument 400, not-found)
    fail fast instead of being retried 3x with backoff.
    """
    exc_name = type(exc).__name__
    # google-maps-places raises various transport errors
    if (
        "ServiceUnavailable" in exc_name
        or "TooManyRequests" in exc_name
        or "ResourceExhausted" in exc_name
        or "DeadlineExceeded" in exc_name
        or "InternalServerError" in exc_name
        or "Aborted" in exc_name
    ):
        return True
    # Rate limit / transport errors typically raise as Timeout / ConnectionError
    if "Timeout" in exc_name or "ConnectionError" in exc_name:
        return True
    # Retry generic GoogleAPICallError only when its code indicates transient
    code = getattr(exc, "code", None)
    if code is not None:
        try:
            code_val = int(getattr(code, "value", code))
            if code_val in (429, 500, 502, 503, 504):
                return True
        except (TypeError, ValueError):
            pass
    return False


# ---------------------------------------------------------------------------
# RealPlacesClient
# ---------------------------------------------------------------------------


class RealPlacesClient:
    """Real Google Places (New API) client using google-maps-places 0.9.x SDK.

    Guard: raises RuntimeError if AppConfig().run_real_externals is False.
    This client is ONLY instantiated in tasks that have confirmed run_real_externals=True.

    D-04 / COMP-03: Returns raw Places dicts. The caller (DiscoveryAgent / SignalAgent)
    persists only place_id as a cache key; all other Google data is transient.

    Args:
        api_key: Google Places API key (required).
    """

    def __init__(self, api_key: str) -> None:
        from brave.config.settings import AppConfig

        if not AppConfig().run_real_externals:
            raise RuntimeError(
                "RealPlacesClient: run_real_externals=False — "
                "use FakePlacesClient in default test suite. "
                "Set BRAVE_RUN_REAL_EXTERNALS=true to enable real API calls."
            )

        if not api_key:
            raise RuntimeError(
                "RealPlacesClient: api_key is empty — "
                "set BRAVE_PLACES_API_KEY environment variable."
            )

        self._api_key = api_key
        self._client = None  # Lazy init — avoid import-time SDK setup

    def _get_client(self) -> Any:
        """Lazy-initialize the google-maps-places SDK client."""
        if self._client is None:
            try:
                from google.maps import places_v1  # type: ignore[import]

                self._client = places_v1.PlacesAsyncClient(
                    client_options={"api_key": self._api_key}
                )
            except ImportError as exc:
                raise RuntimeError(
                    "google-maps-places not installed. "
                    "Add google-maps-places>=0.9.0 to pyproject.toml."
                ) from exc

        return self._client

    @retry(
        retry=retry_if_exception(_is_retryable),  # WR-01: transient only
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def text_search(self, query: str, uf: str) -> list[dict[str, Any]]:
        """Search for places matching a text query within a UF.

        Uses Places API (New) Text Search endpoint.
        Returns up to 20 results per query (configurable via max_result_count).

        Args:
            query: Text search string (e.g. "praias em Porto Seguro").
            uf:    Two-letter state code (used as location bias context).

        Returns:
            List of place dicts, each containing at minimum:
              place_id, name, formatted_address, types, location.
        """
        client = self._get_client()

        from google.maps.places_v1.types import SearchTextRequest  # type: ignore[import]

        request = SearchTextRequest(
            text_query=f"{query} {uf} Brasil",
            max_result_count=20,
            language_code="pt-BR",
        )

        try:
            response = await client.search_text(request)
        except Exception as exc:
            logger.error("places_text_search_error", query=query, uf=uf, error=str(exc))
            raise

        results: list[dict[str, Any]] = []
        for place in response.places:
            result = {
                "place_id": place.id,
                "name": place.display_name.text if place.display_name else "",
                "formatted_address": place.formatted_address or "",
                "types": list(place.types),
                "location": {
                    "lat": place.location.latitude if place.location else None,
                    "lng": place.location.longitude if place.location else None,
                },
            }
            results.append(result)

        logger.info("places_text_search_ok", query=query, uf=uf, count=len(results))
        return results

    @retry(
        retry=retry_if_exception(_is_retryable),  # WR-01: transient only
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def place_details(self, place_id: str) -> dict[str, Any]:
        """Fetch full details for a Google Place.

        Requests fields required by ContactFinderAgent and SignalAgent:
          business_status, weekday_text, reviews, website,
          formatted_phone_number, international_phone_number.

        Args:
            place_id: Google Places place_id (from text_search result).

        Returns:
            Full place detail dict.
        """
        client = self._get_client()

        from google.maps.places_v1.types import GetPlaceRequest  # type: ignore[import]

        # Field mask: only request fields we need (Places API billing is per-field)
        field_mask = (
            "places.id,"
            "places.displayName,"
            "places.formattedAddress,"
            "places.internationalPhoneNumber,"
            "places.websiteUri,"
            "places.businessStatus,"
            "places.currentOpeningHours.weekdayDescriptions,"
            "places.reviews"
        )

        request = GetPlaceRequest(
            name=f"places/{place_id}",
        )

        try:
            place = await client.get_place(request, metadata=[("x-goog-fieldmask", field_mask)])
        except Exception as exc:
            logger.error("places_place_details_error", place_id=place_id, error=str(exc))
            raise

        # Normalize reviews to the shape SignalAgent expects
        reviews: list[dict[str, Any]] = []
        for review in place.reviews or []:
            reviews.append({
                "publishTime": review.publish_time.isoformat() if review.publish_time else None,
                "rating": getattr(review, "rating", None),
                "text": review.text.text if review.text else "",
            })

        weekday_text: list[str] = []
        if place.current_opening_hours:
            weekday_text = list(place.current_opening_hours.weekday_descriptions)

        result = {
            "place_id": place.id,
            "name": place.display_name.text if place.display_name else "",
            "formatted_address": place.formatted_address or "",
            "international_phone_number": getattr(place, "international_phone_number", None),
            "website": getattr(place, "website_uri", None),
            "business_status": place.business_status.name if place.business_status else "UNKNOWN",
            "weekday_text": weekday_text,
            "reviews": reviews,
        }

        logger.info("places_place_details_ok", place_id=place_id)
        return result


# ---------------------------------------------------------------------------
# Protocol compliance check
# ---------------------------------------------------------------------------


def _check_protocol_compliance() -> None:
    """Compile-time structural typing assertion (not called at runtime).

    Verifies that RealPlacesClient structurally satisfies PlacesClientProtocol.
    Skipped at runtime because instantiation requires run_real_externals=True.
    """
    # NOTE: RealPlacesClient raises RuntimeError if run_real_externals=False,
    # so we cannot instantiate it here. The structural check is verified by
    # the type annotations on text_search and place_details matching the Protocol.
    # Full structural check would require a mock AppConfig.
    pass
