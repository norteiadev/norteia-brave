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

import unicodedata
from datetime import timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

import structlog
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Field mask constants (D-01)
# Different prefix rules for search_text vs get_place — Places API (New):
#   search_text response = SearchTextResponse.places[] → "places." prefix required
#   get_place   response = bare Place object         → NO "places." prefix
# Source: RESEARCH.md RQ-1 (verified from installed async_client.py + official docs)
# ---------------------------------------------------------------------------

_TEXT_SEARCH_FIELD_MASK = (
    "places.id,"
    "places.displayName,"
    "places.formattedAddress,"
    "places.types,"
    "places.location,"
    "places.addressComponents"
)

_GET_PLACE_FIELD_MASK = (
    "id,"
    "displayName,"
    "formattedAddress,"
    "types,"
    "location,"
    "addressComponents,"
    "businessStatus,"
    "regularOpeningHours,"
    "reviews,"
    "internationalPhoneNumber,"
    "websiteUri"
)


# ---------------------------------------------------------------------------
# Municipality extraction helpers (D-02)
# ---------------------------------------------------------------------------


def _normalize_name(name: str) -> str:
    """Normalize a municipality name for lookup: strip accents, lowercase, strip whitespace.

    Uses unicodedata NFD decomposition + ASCII encode to strip diacritical marks.
    Example: "São Paulo" → "sao paulo", "Ilhéus" → "ilheus".
    """
    nfd = unicodedata.normalize("NFD", name.lower().strip())
    return nfd.encode("ascii", "ignore").decode()


def _extract_municipio_from_components(address_components: Any) -> tuple[str, str]:
    """Return (municipio_nome, uf_short) from Places API addressComponents.

    Types:
      "administrative_area_level_2" → município name (long_text)
      "administrative_area_level_1" → state abbreviation (short_text = "BA", "RJ", ...)

    Returns ("", "") if components are missing or do not contain the expected types.
    Source: RESEARCH.md RQ-2 (verified from installed place.py AddressComponent class)
    """
    municipio_nome = ""
    uf_short = ""
    for comp in address_components:
        types = list(comp.types)
        if "administrative_area_level_2" in types:
            municipio_nome = comp.long_text
        elif "administrative_area_level_1" in types:
            uf_short = comp.short_text  # "BA", "RJ", etc.
    return municipio_nome, uf_short


def _extract_distrito_from_components(address_components: Any) -> str:
    """Return the distrito name (long_text) from Places API addressComponents.

    Type:
      "administrative_area_level_3" → distrito name (long_text)
        e.g. "Arraial d'Ajuda" (distrito of município Porto Seguro/BA)

    DTB has no GPS, so this NAME text is the only distrito signal Places returns.
    Callers name-match it against the município's distritos (resolve_distrito).

    Returns "" if components are missing or do not contain the expected type.
    """
    for comp in address_components:
        if "administrative_area_level_3" in list(comp.types):
            return comp.long_text
    return ""


def build_mtur_ibge_lookup(rows: list[dict]) -> dict[tuple[str, str], str]:
    """Build {(normalized_name, UF): ibge_code} lookup dict from município rows.

    Used to resolve municipality name → IBGE code in-process. The Places API has
    no IBGE field; this is the only resolution path. Kept as the pure dict-builder
    behind ``load_municipio_name_ibge_lookup`` (which feeds it rows from the
    ``municipios`` reference table).

    Args:
        rows: List of municipality dicts, each with "name", "uf", and
              "ibge_code" keys.

    Returns:
        Dict mapping (normalized_municipality_name, "BA") → "2927408" (IBGE code).
    """
    lookup: dict[tuple[str, str], str] = {}
    for row in rows:
        name = row.get("name", "")
        uf = row.get("uf", "").upper()
        ibge = row.get("ibge_code", "")
        if name and uf and ibge:
            lookup[(_normalize_name(name), uf)] = ibge
    return lookup


def load_municipio_name_ibge_lookup(session: "Session") -> dict[tuple[str, str], str]:
    """Build {(normalized_name, UF): ibge_code} from the ``municipios`` reference table.

    Repoints ``build_mtur_ibge_lookup`` (which read the retired mtur CSV) at the
    seeded DB table. Same lookup-dict shape, so ``RealPlacesClient`` consumes it
    unchanged: the Places API has no IBGE field, so a normalized name+UF → IBGE
    lookup is the only resolution path for ``municipio_ibge``.

    Args:
        session: SQLAlchemy synchronous Session.

    Returns:
        Dict mapping (normalized_municipality_name, "BA") → "2927408" (IBGE code).
    """
    from brave.core.models import Municipio

    lookup: dict[tuple[str, str], str] = {}
    for nome, uf, ibge_code in session.query(
        Municipio.nome, Municipio.uf, Municipio.ibge_code
    ).all():
        if nome and uf and ibge_code:
            lookup[(_normalize_name(nome), uf.upper())] = ibge_code
    return lookup


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

    def __init__(
        self,
        api_key: str,
        ibge_lookup: dict[tuple[str, str], str] | None = None,
    ) -> None:
        from brave.config.settings import AppConfig

        if not AppConfig().run_real_externals:
            raise RuntimeError(
                "RealPlacesClient: run_real_externals=False — "
                "use FakePlacesClient in default test suite. "
                "Set RUN_REAL_EXTERNALS=true to enable real API calls."
            )

        if not api_key:
            raise RuntimeError(
                "RealPlacesClient: api_key is empty — "
                "set BRAVE_PLACES_API_KEY environment variable."
            )

        self._api_key = api_key
        self._client = None  # Lazy init — avoid import-time SDK setup
        # D-02: in-process name→IBGE lookup built from loaded Mtur table
        self._ibge_lookup: dict[tuple[str, str], str] = ibge_lookup or {}

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
            response = await client.search_text(
                request,
                metadata=[("x-goog-fieldmask", _TEXT_SEARCH_FIELD_MASK)],
            )
        except Exception as exc:
            logger.error("places_text_search_error", query=query, uf=uf, error=str(exc))
            raise

        results: list[dict[str, Any]] = []
        for place in response.places:
            # D-02: extract municipio from addressComponents and resolve IBGE
            municipio_nome, _uf_short = _extract_municipio_from_components(
                place.address_components or []
            )
            ibge_key = (_normalize_name(municipio_nome), uf.upper())
            municipio_ibge = self._ibge_lookup.get(ibge_key, "")
            # distrito name text (admin_area_level_3) — name-matched downstream
            distrito_hint = _extract_distrito_from_components(
                place.address_components or []
            )

            result = {
                "place_id": place.id,
                "name": place.display_name.text if place.display_name else "",
                "formatted_address": place.formatted_address or "",
                "types": list(place.types),
                "location": {
                    "lat": place.location.latitude if place.location else None,
                    "lng": place.location.longitude if place.location else None,
                },
                "municipio_nome": municipio_nome,
                "municipio_ibge": municipio_ibge,
                "distrito_hint": distrito_hint,
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

        # D-01 fix: use _GET_PLACE_FIELD_MASK (no "places." prefix for get_place)
        # The old inline field_mask had "places.id, places.displayName, ..." which is
        # WRONG for get_place — it returns a bare Place, not SearchTextResponse.places[].
        request = GetPlaceRequest(
            name=f"places/{place_id}",
        )

        try:
            place = await client.get_place(
                request,
                metadata=[("x-goog-fieldmask", _GET_PLACE_FIELD_MASK)],
            )
        except Exception as exc:
            logger.error("places_place_details_error", place_id=place_id, error=str(exc))
            raise

        # Normalize reviews to the shape SignalAgent expects
        # D-01 fix: review.publish_time is a proto Timestamp — use ToDatetime() for safe
        # conversion; bare .isoformat() raises AttributeError in some proto-plus versions.
        reviews: list[dict[str, Any]] = []
        for review in place.reviews or []:
            if review.publish_time:
                try:
                    publish_time_str = review.publish_time.ToDatetime(
                        tzinfo=timezone.utc
                    ).isoformat()
                except AttributeError:
                    # Fallback: proto-plus may have auto-converted to datetime already
                    publish_time_str = review.publish_time.isoformat()
            else:
                publish_time_str = None
            reviews.append({
                "publishTime": publish_time_str,
                "rating": getattr(review, "rating", None),
                "text": review.text.text if review.text else "",
            })

        # D-01 fix: use regular_opening_hours (stable schedule, field 21)
        # instead of current_opening_hours (field 46, reflects current-week exceptions)
        weekday_text: list[str] = []
        if place.regular_opening_hours:
            weekday_text = list(place.regular_opening_hours.weekday_descriptions)

        # distrito name text (admin_area_level_3) — name-matched downstream
        distrito_hint = _extract_distrito_from_components(place.address_components or [])

        # Precise point coordinates (field 'location' is in _GET_PLACE_FIELD_MASK).
        # Google's lat/lng are more precise than TA's — PlacesEnrichmentAgent adopts them.
        location: dict[str, float] | None = None
        if place.location:
            location = {"lat": place.location.latitude, "lng": place.location.longitude}

        result = {
            "place_id": place.id,
            "name": place.display_name.text if place.display_name else "",
            "formatted_address": place.formatted_address or "",
            "international_phone_number": getattr(place, "international_phone_number", None),
            "website": getattr(place, "website_uri", None),
            "business_status": place.business_status.name if place.business_status else "UNKNOWN",
            "weekday_text": weekday_text,
            "reviews": reviews,
            "location": location,
            "distrito_hint": distrito_hint,
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
