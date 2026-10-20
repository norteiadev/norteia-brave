"""RealPlacesClient — Google Places (New API) implementation.

Uses google-maps-places 0.9.x SDK (not raw httpx; not the legacy googlemaps client).
Implements PlacesClientProtocol:
  - text_search(query, uf) → list[dict]
  - place_details(place_id, *, include_reviews=True) → dict

Guard: raises RuntimeError if AppConfig().run_real_externals is False.
This prevents accidental real API calls in CI / default test suite.

D-04 / COMP-03: Returns raw Places data. Callers (DiscoveryAgent, SignalAgent)
are responsible for persisting ONLY place_id from the raw response.

tenacity: 3 retries with exponential backoff for transient errors (429, 5xx).

Budget (C4): with a redis_client, every paid call is checked against the API key's
daily cap (AppConfig.places_daily_usd_cap → CostGuardError) and total credit cap
(AppConfig.places_total_usd_cap → ProviderBalanceError) BEFORE dispatch — no call made —
and recorded by SKU on that key's counters after it succeeds (see cost_guard).

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

from brave.observability.cost_guard import pre_dispatch_check_places, record_places_spend
from brave.shared.exceptions import ProviderBalanceError

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

# Place Details SKU is billed by the HIGHEST tier field in the mask:
#   Enterprise ($20/1k)              — regularOpeningHours, internationalPhoneNumber,
#                                      websiteUri, priceLevel
#   Enterprise + Atmosphere ($25/1k) — + reviews (reviews ALONE triggers the bump)
# Callers that already know a record's review recency (e.g. from TripAdvisor) can ask
# for the reviews-free mask and save the Atmosphere surcharge.
_GET_PLACE_FIELD_MASK_NO_REVIEWS = (
    "id,"
    "displayName,"
    "formattedAddress,"
    "types,"
    "primaryType,"
    "location,"
    "addressComponents,"
    "businessStatus,"
    "regularOpeningHours,"
    "internationalPhoneNumber,"
    "websiteUri,"
    "priceLevel"
)

_GET_PLACE_FIELD_MASK = _GET_PLACE_FIELD_MASK_NO_REVIEWS + ",reviews"

# Approximate USD per call by SKU (source: Google Maps Platform pricing, validated 2026-10-14).
# Text Search bills Pro because its mask asks for location/types/addressComponents; Details
# bills by the highest-tier field (see the masks above). Feeds the Places daily cost guard.
USD_TEXT_SEARCH_PRO: float = 0.032
USD_DETAILS_ENTERPRISE: float = 0.020
USD_DETAILS_ENTERPRISE_ATMOSPHERE: float = 0.025


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
      "locality"                    → município name when level_2 is absent (long_text)
      "administrative_area_level_1" → state abbreviation (short_text = "BA", "RJ", ...)

    Google omits level_2 on some results (AL 10/2026: "Praia da Sereia" came with only
    locality=Maceió + level_1). locality is only the fallback: it can name a distrito
    (Trancoso of Porto Seguro), which fails the IBGE name lookup — or resolves to ANOTHER
    município of the same name, which locate() catches by the result's coords.

    Returns ("", "") if components are missing or do not contain the expected types.
    Source: RESEARCH.md RQ-2 (verified from installed place.py AddressComponent class)
    """
    municipio_nome = ""
    locality = ""
    uf_short = ""
    for comp in address_components:
        types = list(comp.types)
        if "administrative_area_level_2" in types:
            municipio_nome = comp.long_text
        elif "administrative_area_level_1" in types:
            uf_short = comp.short_text  # "BA", "RJ", etc.
        elif "locality" in types:
            locality = comp.long_text
    return municipio_nome or locality, uf_short


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


def _raise_if_places_balance_wall(exc: Exception) -> None:
    """Classify a final (post-retry) Places SDK exception as a billing wall.

    ResourceExhausted covers BOTH a per-minute rate burst and the daily quota/billing wall —
    the two are not distinguishable by exception type. It stays retryable in ``_is_retryable``
    (tenacity backoff absorbs a minute burst). Called only by the public methods, OUTSIDE the
    @retry'd SDK call — classifying inside it raised ProviderBalanceError on the FIRST 429,
    which the retry predicate rejects, so a single burst paused the whole engine.
    Once retries are exhausted Google's message names the limit ("... per minute" vs
    "... per day"): a minute limit is still transient and leaves as the SDK error (the caller
    keeps the record unmarked for a later pass); any other quota is treated as the wall.
    Any 403 PermissionDenied / 401 Unauthenticated is a provider wall too — billing off, but
    also the typical swapped-key mistakes (Places API (New) not enabled on the new project /
    SERVICE_DISABLED, API_KEY_INVALID, a referer/IP-restricted key). None of them fixes itself:
    swallowed per record, a whole backfill would run to "done" with Places silently skipped.
    """
    exc_name = type(exc).__name__
    if "ResourceExhausted" in exc_name and "per minute" not in str(exc).lower():
        raise ProviderBalanceError("google_places", str(exc))
    if "PermissionDenied" in exc_name or "Unauthenticated" in exc_name:
        raise ProviderBalanceError("google_places", str(exc))


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
        redis_client: Redis for the Places cost guard. None → no budget (scripts).
        daily_usd_cap: AppConfig.places_daily_usd_cap — the key's daily cap.
        total_usd_cap: AppConfig.places_total_usd_cap — the key's credit (no reset).
    """

    def __init__(
        self,
        api_key: str,
        ibge_lookup: dict[tuple[str, str], str] | None = None,
        *,
        redis_client: Any = None,
        daily_usd_cap: float = 300.0,
        total_usd_cap: float = 300.0,
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
        self._redis_client = redis_client
        self._daily_usd_cap = daily_usd_cap
        self._total_usd_cap = total_usd_cap

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

    def check_budget(self, usd: float) -> None:
        """Raise (CostGuardError / ProviderBalanceError) when ``usd`` more would cross a cap.

        Public so a caller about to chain several paid calls (Text Search → Details) can
        check their SUM first instead of paying the first and stopping at the second.
        """
        if self._redis_client is not None:
            pre_dispatch_check_places(
                self._redis_client, self._daily_usd_cap, usd,
                api_key=self._api_key, total_cap_usd=self._total_usd_cap,
            )

    def _record_spend(self, usd: float) -> None:
        if self._redis_client is not None:
            record_places_spend(self._redis_client, usd, self._api_key)

    # The raw SDK calls carry the retry; the public methods classify only what survives it.
    @retry(
        retry=retry_if_exception(_is_retryable),  # WR-01: transient only
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def _search_text_once(self, request: Any) -> Any:
        return await self._get_client().search_text(
            request, metadata=[("x-goog-fieldmask", _TEXT_SEARCH_FIELD_MASK)]
        )

    @retry(
        retry=retry_if_exception(_is_retryable),  # WR-01: transient only
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def _get_place_once(self, request: Any, field_mask: str) -> Any:
        return await self._get_client().get_place(
            request, metadata=[("x-goog-fieldmask", field_mask)]
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
              place_id, name, formatted_address, types, location. Places that Google
              puts in ANOTHER state are dropped; ``municipio_ibge`` "" means the
              município name did not resolve within the UF.
        """
        from google.maps.places_v1.types import SearchTextRequest  # type: ignore[import]

        request = SearchTextRequest(
            text_query=f"{query} {uf} Brasil",
            max_result_count=20,
            language_code="pt-BR",
        )

        self.check_budget(USD_TEXT_SEARCH_PRO)
        try:
            response = await self._search_text_once(request)
        except Exception as exc:
            logger.error("places_text_search_error", query=query, uf=uf, error=str(exc))
            _raise_if_places_balance_wall(exc)
            raise
        self._record_spend(USD_TEXT_SEARCH_PRO)

        results: list[dict[str, Any]] = []
        for place in response.places:
            # D-02: extract municipio from addressComponents and resolve IBGE
            municipio_nome, uf_short = _extract_municipio_from_components(
                place.address_components or []
            )
            # The query names the UF only as a hint: Places still returns namesakes in other
            # states. Drop those here, for every caller — municipio_ibge resolves against the
            # REQUESTED UF, so an out-of-UF place in a homonym município (Bom Jesus/RS for
            # uf=PI) would otherwise get the PI code. No level_1 component → kept (fail open).
            uf_short = (uf_short or "").upper()
            if len(uf_short) == 2 and uf_short != uf.upper():
                continue
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
                # Read by locate()'s coords check (PlacesEnrichmentAgent._place_by_coords):
                # "" = no state component; True = the name came from locality, not level_2.
                "uf_short": uf_short,
                "municipio_from_locality": bool(municipio_nome)
                and not any(
                    "administrative_area_level_2" in c.types
                    for c in place.address_components or []
                ),
                "distrito_hint": distrito_hint,
            }
            results.append(result)

        logger.info("places_text_search_ok", query=query, uf=uf, count=len(results))
        return results

    async def place_details(
        self, place_id: str, *, include_reviews: bool = True
    ) -> dict[str, Any]:
        """Fetch full details for a Google Place.

        Requests fields required by ContactFinderAgent and SignalAgent:
          business_status, weekday_text, reviews, website,
          international_phone_number, price_level, types.

        Args:
            place_id: Google Places place_id (from text_search result).
            include_reviews: False drops ``reviews`` from the field mask — reviews alone
                push the call from Enterprise ($20/1k) to Enterprise + Atmosphere
                ($25/1k). The returned dict keeps its shape with ``reviews == []``.

        Returns:
            Full place detail dict.
        """
        from google.maps.places_v1.types import GetPlaceRequest  # type: ignore[import]

        # D-01 fix: use _GET_PLACE_FIELD_MASK (no "places." prefix for get_place)
        # The old inline field_mask had "places.id, places.displayName, ..." which is
        # WRONG for get_place — it returns a bare Place, not SearchTextResponse.places[].
        # pt-BR like text_search: displayName, address and weekdayDescriptions come back in
        # Portuguese (the descarte log and the description context show them as-is).
        # opening_hours.to_hours_map parses both locales.
        request = GetPlaceRequest(
            name=f"places/{place_id}",
            language_code="pt-BR",
        )
        field_mask = (
            _GET_PLACE_FIELD_MASK if include_reviews else _GET_PLACE_FIELD_MASK_NO_REVIEWS
        )

        usd = USD_DETAILS_ENTERPRISE_ATMOSPHERE if include_reviews else USD_DETAILS_ENTERPRISE
        self.check_budget(usd)
        try:
            place = await self._get_place_once(request, field_mask)
        except Exception as exc:
            logger.error("places_place_details_error", place_id=place_id, error=str(exc))
            _raise_if_places_balance_wall(exc)
            raise
        self._record_spend(usd)

        # Normalize reviews to the shape SignalAgent expects
        # D-01 fix: review.publish_time is a proto Timestamp — use ToDatetime() for safe
        # conversion; bare .isoformat() raises AttributeError in some proto-plus versions.
        # include_reviews=False: reviews were not requested — keep the key, empty list.
        reviews: list[dict[str, Any]] = []
        for review in (place.reviews or []) if include_reviews else ():
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

        # priceLevel: enum PRICE_LEVEL_* → persisted as a structured field (never in prose).
        price_level: str | None = None
        if getattr(place, "price_level", None):
            _pl = place.price_level
            price_level = _pl.name if hasattr(_pl, "name") else str(_pl)
            if price_level in ("PRICE_LEVEL_UNSPECIFIED", "0"):
                price_level = None

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
            "price_level": price_level,
            "types": list(place.types or []),
            # Pro-tier field: free here, the mask already bills Enterprise.
            "primary_type": getattr(place, "primary_type", "") or "",
        }

        logger.info(
            "places_place_details_ok", place_id=place_id, include_reviews=include_reviews
        )
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
