"""Client Protocol boundary definitions for all 8 external systems (D-09, D-18).

Every external system sits behind a typed typing.Protocol interface.
Production code accepts these protocol types; tests inject fakes from tests/fakes/.

Protocols use structural typing — no isinstance() checks anywhere.
Runtime-checkable is intentionally False: Protocol is the static boundary,
not a runtime check.

Seven protocols (CORE-11 + TA-01 + TA-14):
  1. LLMClientProtocol          — LLM extraction (OpenRouter/DeepSeek + Anthropic)
  2. NorteiaApiClientProtocol   — Mar push to norteia-api
  3. PlacesClientProtocol       — Google Places (New API) search/details
  4. OTAClientProtocol          — OTA price check (ticketed attractions)
  5. WhatsAppClientProtocol     — WhatsApp Business API template messages
  6. TripAdvisorClientProtocol  — TripAdvisor GraphQL hybrid scraper (Phase 11)
  7. GeocoderClientProtocol     — OpenStreetMap Nominatim forward-geocoder (Phase 14, TA-14)
"""

from collections.abc import AsyncIterator
from typing import Any, Protocol


class LLMClientProtocol(Protocol):
    """LLM client for extraction (DeepSeek/instructor) and generation (Sonnet) (D-08, D-09).

    Two methods:
      extract() — structured extraction via instructor Mode.Tools (DeepSeek).
      generate() — free-form text generation (Sonnet PT-BR conversation, D-08).

    Every call must log to llm_generations and check the USD cost guard (D-20).
    """

    async def extract(
        self,
        prompt: str,
        schema: type,
        mode: str = "tools",
    ) -> Any:
        """Extract structured data from a prompt using the given Pydantic schema.

        Uses instructor + Mode.Tools (DeepSeek) by default. 2nd-layer Pydantic
        validation enforced by caller (ConversationExtractionResult).

        Args:
            prompt: Instruction + context to send to the LLM.
            schema: Pydantic model class to validate the response against.
            mode: instructor mode string ("tools" | "json" | "md_json").

        Returns:
            An instance of `schema` with the extracted data.
        """
        ...

    async def generate(
        self,
        messages: list[dict[str, Any]],
        model: str = "claude-sonnet-4-5",
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> str:
        """Generate a free-form text response (Sonnet PT-BR conversation, D-08).

        Used by WhatsAppAgent ask_followup_node and TourismCopywriter.

        Args:
            messages: Conversation history list [{role, content}].
            model:    Model identifier (default: claude-sonnet-4-5).
            system:   Optional system prompt.
            tools:    Optional tool defs (e.g. server-side web_search).

        Returns:
            Generated text response string.
        """
        ...


class NorteiaApiClientProtocol(Protocol):
    """Client for pushing canonical records to norteia-api (D-15, D-16).

    Push is idempotent by source_ref (norteia-api upserts on canonical key).
    Shape verified by Pact consumer test in tests/contract/.
    """

    async def push_destination(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Push a canonical destination Mar record to norteia-api.

        Args:
            payload: Mar push payload matching the Pact contract shape.

        Returns:
            Response dict from norteia-api (at minimum: {"id": ..., "source_ref": ...}).
        """
        ...

    async def push_attraction(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Push a canonical attraction Mar record to norteia-api.

        Args:
            payload: Mar push payload matching the Pact contract shape.

        Returns:
            Response dict from norteia-api (at minimum: {"id": ..., "source_ref": ...}).
        """
        ...


class PlacesClientProtocol(Protocol):
    """Google Places (New API) client — Discovery and Signal agents (Phase 3).

    Uses the New Places API (google-maps-places 0.9.x) for business_status,
    weekday_text, reviews[].publishTime fields required by SignalAgent.
    """

    async def text_search(self, query: str, uf: str) -> list[dict[str, Any]]:
        """Search for places matching a text query within a UF.

        Args:
            query: Text search string (e.g. "praias em Porto Seguro").
            uf: Two-letter state code to restrict results.

        Returns:
            List of place dicts (place_id, name, formatted_address, ...).
        """
        ...

    async def place_details(self, place_id: str) -> dict[str, Any]:
        """Fetch full details for a Google Place.

        Args:
            place_id: Google Places place_id (persist for caching per D-17).

        Returns:
            Full place detail dict including business_status, weekday_text, reviews.
        """
        ...


class OTAClientProtocol(Protocol):
    """OTA (Online Travel Agency) price check client — optional signal (Phase 3).

    Used only for ticketed attractions; best-effort corroboration signal.
    """

    async def price_check(self, place_id: str) -> dict[str, Any] | None:
        """Check if an attraction has OTA pricing data.

        Args:
            place_id: Internal or OTA-specific place identifier.

        Returns:
            Price data dict, or None if the attraction has no OTA listing.
        """
        ...


class WhatsAppClientProtocol(Protocol):
    """WhatsApp Business API client — outreach messages (Phase 3).

    Uses approved templates only (BSP compliance).
    Human gate must approve who to contact before any automated outreach.
    """

    async def send_template(
        self,
        to: str,
        template: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """Send an approved WhatsApp template message.

        Args:
            to: Recipient phone number in E.164 format.
            template: Approved template name (BSP-approved).
            params: Template parameters dict.

        Returns:
            Delivery status dict from BSP (message_sid, status, ...).
        """
        ...


class TripAdvisorClientProtocol(Protocol):
    """TripAdvisor GraphQL hybrid scraper client (Phase 11, TA-01).

    Acquisition seam: Playwright bootstraps DataDome session → cookies injected
    into httpx → persisted-query POST to TripAdvisor's GraphQL endpoint.
    Consumers accept TripAdvisorClientProtocol; production code uses
    TripAdvisorClient (real) or NullTripAdvisorClient (offline/CI).
    """

    async def fetch_destinations(self, uf: str) -> list[dict[str, Any]]:
        """Fetch TripAdvisor destinations (GEO entities) for a Brazilian UF.

        Args:
            uf: Two-letter Brazilian state code (e.g. "BA").

        Returns:
            List of location dicts from the GraphQL response
            (at minimum: locationId, name, latitude, longitude).
        """
        ...

    async def fetch_attractions(
        self, geo_id: int, max_pages: int | None = None
    ) -> list[dict[str, Any]]:
        """Fetch TripAdvisor attractions (ATTRACTION entities) for a geoId.

        Phase 13: uses the AttractionsFusion listing query (qid a5cb7fa004b5e4b5)
        with the real request.routeParameters variables shape. Returns normalized
        dicts with keys: name, locationId, rating, review_count, category.

        Args:
            geo_id: TripAdvisor integer geoId for the state/city.
            max_pages: Cap on pages to fetch. None (default) fetches a single page
                (pagination gap: AttractionsFusion page/offset param unconfirmed).

        Returns:
            List of attraction dicts with keys: name, locationId, rating,
            review_count, category.
        """
        ...

    def fetch_attractions_paginated(
        self, geo_id: int, start_page: int = 1, max_pages: int = 334
    ) -> AsyncIterator[tuple[int, list[dict[str, Any]]]]:
        """Stream TripAdvisor attractions page-by-page over the HTML SSR transport.

        Phase 15: paginates the all-Brazil AttractionsFusion listing (geoId 294280)
        across its 334 pages. The GraphQL listing query (qid a5cb7fa004b5e4b5) cannot
        paginate — the persisted query rejects any offset/oa field — so each page is
        fetched as the HTML SSR variant
        ``Attractions-g{geo_id}-...-oa{offset}-Brazil.html`` (offset = (page-1)*30) and
        the embedded card JSON island is recovered and fed to the existing
        ``_parse_attractions_page`` (no new parser, no DOM walker).

        Yields one ``(offset, parsed_cards)`` tuple per HTML SSR page, where ``offset``
        is the ``-oa{N}-`` path offset and ``parsed_cards`` is the same normalized
        attraction-dict list shape ``fetch_attractions`` returns. Async-iterator so the
        caller can commit + record progress per page (resume-from-offset) rather than
        buffering the whole sweep.

        Args:
            geo_id: TripAdvisor integer geoId (294280 = all Brazil).
            start_page: 1-based page to start from (resume-from-offset support;
                page 1 = offset 0, page 2 = offset 30, ...).
            max_pages: Cap on pages to fetch (default 334 = the full TA display cap).

        Yields:
            ``(offset, cards)`` tuples — one per page; ``offset`` is ``(page-1)*30``,
            ``cards`` is a list of attraction dicts with keys: name, locationId,
            rating, review_count, category.
        """
        ...

    def fetch_attractions_paginated_gql(
        self, geo_id: int, start_page: int = 1, max_pages: int = 334
    ) -> AsyncIterator[tuple[int, list[dict[str, Any]]]]:
        """Stream AttractionsFusion attractions page-by-page over GraphQL.

        POC-confirmed replacement for the single-page ``fetch_attractions`` AND the
        HTML-SSR ``fetch_attractions_paginated``: the AttractionsFusion persisted
        query (qid ``79aaeeb847e55e58``) paginates via ``request.routeParameters.pagee``
        (offset = (page-1)*30; page 1 sends ``pagee="0"`` explicitly). Each page is
        parsed by the existing FlexCard parser (LGPD aggregate-only) and yielded as
        an ``(offset, cards)`` tuple. Stops when a page yields 0 cards; clamped to the
        334-page / oa9990 cap; 403/429 → SessionExpiredError.

        Args:
            geo_id: TripAdvisor integer geoId (294280 = all Brazil). MUST be int.
            start_page: 1-based page to start from (resume support).
            max_pages: Pages to attempt from start_page (default 334 = full cap).

        Yields:
            ``(offset, cards)`` tuples — ``offset = (page-1)*30``, ``cards`` a list of
            attraction dicts with keys: name, locationId, rating, review_count, category.
        """
        ...

    async def fetch_recent_review(self, location_id: int) -> dict | None:
        """Fetch review recency for one attraction (qid ef1a9f94012220d3).

        Powers ``atualidade_from_recency`` plus a precise corroboração count/rating.
        Reviews are newest-first (SERVER_DETERMINED), so ``reviews[0]`` is newest.

        LGPD: reads ONLY ``totalCount`` + ``reviews[0].publishedDate`` +
        ``reviews[0].rating`` — never review text/title/username/userProfile/photos.

        Args:
            location_id: TripAdvisor integer locationId.

        Returns:
            ``{"review_count": int, "rating": float | None, "most_recent_review_at":
            datetime}`` on success, or ``None`` on empty reviews / any parse error.

        Raises:
            SessionMissingError: When no session is in Redis (real client).
            SessionExpiredError: On 403 or 429 HTTP status (real client).
        """
        ...

    async def resolve_geo_id(self, uf: str) -> int:
        """Resolve a Brazilian UF code to its TripAdvisor integer geoId.

        Delegates to geo.resolve_geo_id (Redis cache → seed JSON fallback).

        Args:
            uf: Two-letter Brazilian state code.

        Returns:
            TripAdvisor integer geoId, or 0 for the null/offline stub.

        Raises:
            ValueError: When UF is unknown and no cache/seed entry exists (real client).
        """
        ...

    async def fetch_attraction_detail(self, location_id: int) -> dict | None:
        """Fetch the detail record (parents[] geo hierarchy) for one attraction.

        Args:
            location_id: TripAdvisor integer locationId.

        Returns:
            First location dict from the GraphQL response (includes parents[]),
            or None when the response is empty or malformed.

        Raises:
            SessionMissingError: When no session is in Redis (real client).
            SessionExpiredError: On 403 or 429 HTTP status (real client).
        """
        ...

    async def fetch_attraction_geo(self, location_id: int) -> dict | None:
        """Fetch parent municipality geo data for one attraction (qid d3d4987463b78a39).

        Returns a normalized dict {location_id:int, city_name:str, state_name:str,
        city_geo_id:int, state_geo_id:int} or None when response is empty, malformed,
        or countryId != 294280 (non-Brazil).

        ToS/LGPD: aggregate geo only (cityName/stateName/geoIds), no PII.

        Raises:
            SessionMissingError: When no session is in Redis.
            SessionExpiredError: On 403 or 429 HTTP status.
        """
        ...


class GeocoderClientProtocol(Protocol):
    """OpenStreetMap Nominatim forward-geocoder (Phase 14, TA-14).

    LGPD: returns ONLY lat/lon + OSM place id + the município-level
    address sub-field needed for IBGE matching. No address PII is returned
    or stored (decision #8, 14-CONTEXT.md).
    """

    async def geocode(
        self, location_id: str, name: str, uf: str
    ) -> dict[str, Any] | None:
        """Forward-geocode `name + UF + Brazil` → geo dict or None.

        Returns on hit: {"lat": float, "lon": float, "osm_id": int | None,
            "municipio_name": str | None}  # first of address.municipality
            |city|town|village|county precedence chain.
        Returns None when Nominatim returns no results.
        Caches by location_id in Redis (one Nominatim call per attraction per 30d).
        """
        ...

    async def geocode_national(
        self, location_id: str, name: str
    ) -> dict[str, Any] | None:
        """Forward-geocode `name + Brazil` (no UF) → geo dict or None (Phase 15).

        The all-Brazil bulk attractions lane (geoId 294280) has no per-UF context —
        UF is derived downstream from the geocoded município/IBGE code, not supplied
        as input. This national variant queries ``"{name}, Brazil"`` instead of
        ``"{name}, {uf}, Brazil"`` and otherwise honours the same Redis cache and
        LGPD-safe return contract as ``geocode``.

        LGPD (decision #8, 14-CONTEXT.md): returns ONLY the same 4 keys —
        ``{"lat": float, "lon": float, "osm_id": int | None, "municipio_name": str | None}``.
        Never ``display_name``, street, or any address PII.

        Args:
            location_id: TripAdvisor location id (Redis cache key).
            name: Attraction name (national query is ``"{name}, Brazil"``).

        Returns:
            On hit: ``{"lat": float, "lon": float, "osm_id": int | None,
            "municipio_name": str | None}`` (município from the
            municipality|city|town|village|county precedence chain).
            ``None`` when Nominatim returns no results.
        """
        ...
