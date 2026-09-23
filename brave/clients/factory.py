"""The ONE seam that decides real vs offline external clients (D-18).

``clients_for(app_config)`` returns a ``Clients`` bag that builds each adapter lazily, on
first access: the real adapter when ``run_real_externals`` is on, else its Null twin. It
decides ONLY which adapter (externals switch, keys, Gemini pricing, Parallel mode) —
business flags (places_enrichment_enabled, description flags, …) stay in the tasks.

Tests swap ``brave.tasks.pipeline.clients_for`` for a ``Clients`` built with explicit
fakes: ``Clients(places=FakePlacesClient(), llm=FakeLLMClient())`` — an explicit adapter
always wins over lazy building.

``Clients`` is an async context manager scoped to ONE event loop (one ``asyncio.run``):
entering holds the persistent HTTP connection of every adapter already built (TripAdvisor,
Nominatim — one TLS/proxy handshake per sweep, not per request); exiting closes everything
it built.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from brave.config.settings import AppConfig


class Clients:
    """Lazily-built external adapters for one task run (see module docstring).

    Args:
        app_config:  Env-built AppConfig: run_real_externals + every key. None → AppConfig().
        effective:   Overlay config (load_effective_config) for the cascade flag. None →
                     app_config.
        ibge_lookup: Zero-arg loader of the name→IBGE map for the real Places client; only
                     called when that client is built.
        places, llm, search, tripadvisor, geocoder, whatsapp, norteia_api, batch:
                     Explicit adapters (tests). Returned as-is, never closed.
    """

    def __init__(
        self,
        app_config: AppConfig | None = None,
        effective: AppConfig | None = None,
        *,
        ibge_lookup: Callable[[], dict[tuple[str, str], str]] | None = None,
        places: Any = None,
        llm: Any = None,
        search: Any = None,
        tripadvisor: Any = None,
        geocoder: Any = None,
        whatsapp: Any = None,
        norteia_api: Any = None,
        batch: Any = None,
    ) -> None:
        self._cfg = app_config if app_config is not None else AppConfig()
        self._effective = effective if effective is not None else self._cfg
        self._ibge_lookup = ibge_lookup
        self._given: dict[str, Any] = {
            k: v
            for k, v in {
                "places": places, "llm": llm, "search": search, "tripadvisor": tripadvisor,
                "geocoder": geocoder, "whatsapp": whatsapp, "norteia_api": norteia_api,
                "batch": batch,
            }.items()
            if v is not None
        }
        self._cache: dict[str, Any] = {}
        self._built: list[Any] = []  # everything this bag constructed, in build order
        self._redis_client: Any = None

    # -- helpers ---------------------------------------------------------------------

    @property
    def _real(self) -> bool:
        return bool(self._cfg.run_real_externals)

    def _redis(self) -> Any:
        if self._redis_client is None:
            import redis as _redis_lib  # noqa: PLC0415

            self._redis_client = _redis_lib.from_url(
                os.environ.get("BRAVE_DB_REDIS_URL", "redis://localhost:6379/0")
            )
        return self._redis_client

    def _get(self, name: str, build: Callable[[], Any]) -> Any:
        if name in self._given:
            return self._given[name]
        if name not in self._cache:
            client = build()
            self._cache[name] = client
            self._built.append(client)
        return self._cache[name]

    # -- adapters --------------------------------------------------------------------

    @property
    def places(self) -> Any:
        def build() -> Any:
            if not self._real:
                from brave.clients.null_places import NullPlacesClient  # noqa: PLC0415

                return NullPlacesClient()
            from brave.clients.places import RealPlacesClient  # noqa: PLC0415

            return RealPlacesClient(
                api_key=os.environ.get("BRAVE_PLACES_API_KEY", ""),
                ibge_lookup=self._ibge_lookup() if self._ibge_lookup else None,
            )

        return self._get("places", build)

    def llm(self, lane: str, session: Any = None) -> Any:
        """A fresh LLM client per call (lane + session are per caller); Null offline."""
        if "llm" in self._given:
            return self._given["llm"]
        if not self._real:
            from brave.clients.null_llm import NullLLMClient  # noqa: PLC0415

            return NullLLMClient()
        from brave.clients.llm import RealLLMClient  # noqa: PLC0415

        client = RealLLMClient(
            config=self._cfg.llm, redis_client=self._redis(), session=session, lane=lane
        )
        self._built.append(client)
        return client

    def check_search(self) -> str | None:
        """Why the cascade search client can't be built, or None — builds nothing.

        The same validations as ``search()``: a Gemini-direct cascade writer needs
        BRAVE_LLM_GEMINI_API_KEY and a price (else generate() fails per atrativo and burns
        one descricao_attempt each), and Parallel needs its key and a known mode.
        """
        if "search" in self._given or not self._effective.atrativo_description_cascade_enabled:
            return None
        if not self._real:
            return None
        from brave.clients.llm import gemini_is_priced  # noqa: PLC0415
        from brave.clients.parallel import USD_PER_REQUEST  # noqa: PLC0415

        model = self._cfg.atrativo_cascade_model
        if model.startswith("gemini-"):
            if not self._cfg.llm.gemini_api_key:
                return f"cascade model {model!r} needs BRAVE_LLM_GEMINI_API_KEY"
            if not gemini_is_priced(model):
                return f"cascade model {model!r} has no Gemini price"
        if not self._cfg.parallel_api_key:
            return "PARALLEL_API_KEY is empty"
        if self._cfg.parallel_search_mode not in USD_PER_REQUEST:
            return f"unknown Parallel mode {self._cfg.parallel_search_mode!r}"
        return None

    def search(self) -> Any:
        """Parallel client for the cascade copywriter; None when the cascade flag is off
        (web_search mode). Raises RuntimeError when check_search() finds a reason."""
        if "search" in self._given:
            return self._given["search"]
        if not self._effective.atrativo_description_cascade_enabled:
            return None

        def build() -> Any:
            if not self._real:
                from brave.clients.null_search import NullSearchClient  # noqa: PLC0415

                return NullSearchClient()
            reason = self.check_search()
            if reason is not None:
                raise RuntimeError(reason)
            from brave.clients.parallel import RealParallelClient  # noqa: PLC0415

            return RealParallelClient(
                self._cfg.parallel_api_key,
                mode=self._cfg.parallel_search_mode,
                redis_client=self._redis(),
                llm_config=self._cfg.llm,
            )

        return self._get("search", build)

    @property
    def tripadvisor(self) -> Any:
        def build() -> Any:
            if not self._real:
                from brave.clients.null_tripadvisor import NullTripAdvisorClient  # noqa: PLC0415

                return NullTripAdvisorClient()
            from brave.config.settings import TripAdvisorConfig  # noqa: PLC0415
            from brave.lanes.tripadvisor.client import TripAdvisorClient  # noqa: PLC0415

            return TripAdvisorClient(config=TripAdvisorConfig(), redis=self._redis())

        return self._get("tripadvisor", build)

    @property
    def geocoder(self) -> Any:
        def build() -> Any:
            if not self._real:
                from brave.clients.null_nominatim import NullGeocoderClient  # noqa: PLC0415

                return NullGeocoderClient()
            from brave.clients.nominatim import NominatimGeocoderClient  # noqa: PLC0415

            return NominatimGeocoderClient(config=self._cfg.nominatim, redis=self._redis())

        return self._get("geocoder", build)

    @property
    def whatsapp(self) -> Any:
        def build() -> Any:
            if not self._real:
                from brave.clients.null_whatsapp import NullWhatsAppClient  # noqa: PLC0415

                return NullWhatsAppClient()
            from brave.clients.whatsapp import TwilioWhatsAppClient  # noqa: PLC0415

            wa = self._cfg.whatsapp
            return TwilioWhatsAppClient(
                account_sid=wa.twilio_account_sid,
                auth_token=wa.twilio_auth_token,
                from_number=wa.from_number,
                messaging_service_sid=wa.messaging_service_sid or None,
            )

        return self._get("whatsapp", build)

    @property
    def norteia_api(self) -> Any:
        def build() -> Any:
            if not self._real:
                from brave.clients.null_norteia_api import NullNorteiaApiClient  # noqa: PLC0415

                return NullNorteiaApiClient()
            from brave.clients.norteia_api import NorteiaApiClient  # noqa: PLC0415

            return NorteiaApiClient(
                base_url=os.environ.get("BRAVE_NORTEIA_API_URL", ""),
                service_token=os.environ.get("BRAVE_NORTEIA_API_SERVICE_TOKEN", ""),
                redis=self._redis(),
            )

        return self._get("norteia_api", build)

    @property
    def batch(self) -> Any:
        """Sync anthropic.Anthropic for the Message Batches API. It has no Null twin:
        raises RuntimeError while externals are off."""

        def build() -> Any:
            if not self._real:
                raise RuntimeError("Clients.batch: run_real_externals=False — no offline batch client.")
            from anthropic import Anthropic  # noqa: PLC0415

            return Anthropic(api_key=self._cfg.llm.anthropic_api_key)

        return self._get("batch", build)

    # -- lifecycle -------------------------------------------------------------------

    async def __aenter__(self) -> Clients:
        for client in self._built:
            if hasattr(client, "__aenter__"):
                await client.__aenter__()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        built, self._built, self._cache = self._built, [], {}
        for client in built:
            if hasattr(client, "__aexit__"):
                await client.__aexit__(None, None, None)
            elif hasattr(client, "aclose"):
                await client.aclose()


def clients_for(
    app_config: AppConfig | None = None,
    effective: AppConfig | None = None,
    *,
    ibge_lookup: Callable[[], dict[tuple[str, str], str]] | None = None,
) -> Clients:
    """The external clients for one task run (see ``Clients``)."""
    return Clients(app_config, effective, ibge_lookup=ibge_lookup)
