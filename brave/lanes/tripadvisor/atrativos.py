"""TripAdvisorAtrativosIngest — TA-02.

This lane scrapes TripAdvisor attraction listings via the GraphQL hybrid
client (TripAdvisorClientProtocol) and ingests them into Nascente with
source='tripadvisor', entity_type='attraction', origem=65.0.

ToS WARNING: Systematic scraping violates TripAdvisor's Terms of Service
(Section 5, "Use of Site"). This module must NOT be used without operator
acknowledgement of the legal-risk posture documented in data/tripadvisor/README.

LGPD: Only aggregate review signals are stored — review_count, rating, and
most_recent_review_at. Author names, reviewer IDs, and review text are NEVER
extracted or persisted. TripAdvisorReviewSignals enforces extra="forbid" to
prevent drift toward PII fields.

OPERATOR GATE: This producer is NOT on the autonomous Celery beat. A sweep
requires RUN_REAL_EXTERNALS=1 and an explicit POST /api/v1/engine/start with
source="tripadvisor". See data/tripadvisor/README for the full operator
checklist (proxy setup, scraper dep group, LGPD acknowledgement).

NO WHATSAPP OUTREACH: TA attractions NEVER enter the WhatsApp outreach pipeline.
They are review-signal validated only (corroboracao + atualidade from aggregate
review data). Promotion to Mar requires a human steward's audited
promote_override action — there is no automated Mar push path for this lane.

Mirrors TripAdvisorDestinosIngest in structure. Adds parent destino linkage:
  - parent_rio_id: from destino_rio_map (dict[ibge_code, (rio_id, source_ref)])
  - parent_source_ref: from same map entry
  - parent_mar_id: only if the destino is already in Mar (optional)

D-18: This module imports only from brave.core, brave.clients, brave.config,
and brave.lanes.tripadvisor.*. It does NOT import from other lane modules.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy.orm import Session

from brave.config.settings import ScoreConfig, TripAdvisorConfig
from brave.core.nascente.service import store_raw
from brave.core.quarantine import quarantine_poison
from brave.core.rio.routing import process_nascente_record
from brave.lanes.tripadvisor import sweep_progress
from brave.lanes.tripadvisor.ibge import (
    IbgeMunicipio,
    resolve_municipio,
    resolve_municipio_national,
)
from brave.lanes.tripadvisor.schemas import TripAdvisorAtrativoPayload, TripAdvisorReviewSignals
from brave.lanes.tripadvisor.uf_names import state_name_to_uf
from brave.lanes.tripadvisor.scoring import (
    atualidade_from_recency,
    completude_from_fields,
    corroboracao_from_reviews,
)

if TYPE_CHECKING:
    from brave.clients.base import GeocoderClientProtocol, TripAdvisorClientProtocol


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TA_ATRATIVO_ORIGEM_VALUE = 65.0
# origem=65 (>Places 60, <gov 100 — firewall: TA never crosses 85 on origem alone).
# Source: CONTEXT.md TA-04.

logger = structlog.get_logger(__name__)
# Logging discipline (T-15-06-02 / T-12-04-01): the bulk methods log ONLY
# offset / counts / locationId / error-class — never name, address, cookies,
# user_agent, session_id, or proxy values.


# ---------------------------------------------------------------------------
# Lane implementation
# ---------------------------------------------------------------------------


class TripAdvisorAtrativosIngest:
    """TripAdvisor attractions ingestion lane — ingests atrativos into Nascente.

    Mirrors TripAdvisorDestinosIngest in class structure. Resolves the parent
    destino RioRecord from destino_rio_map (built by TripAdvisorDestinosIngest
    in the same sweep run). Quarantines with "parent_destino_absent" when no
    parent is found in the map (deliberately diverges from discovery_agent.py's
    Mar-only resolution — see CONTEXT.md TA-03).

    Args:
        ta_client:       TripAdvisorClientProtocol implementation (real or fake).
        session:         SQLAlchemy synchronous Session.
        config:          ScoreConfig with §7.6 weights and thresholds.
        ibge_records:    Pre-loaded IBGE municipality records (from load_ibge_csv).
        destino_rio_map: Optional mapping from ibge_code → (rio_id, source_ref) for
                         destinos produced in the same sweep. None or empty dict
                         causes "parent_destino_absent" quarantine per atrativo.
    """

    def __init__(
        self,
        ta_client: "TripAdvisorClientProtocol",
        session: Session,
        config: ScoreConfig,
        ibge_records: list[IbgeMunicipio],
        destino_rio_map: dict[str, tuple[uuid.UUID, str]] | None = None,
        geocoder: "GeocoderClientProtocol | None" = None,
        ta_config: TripAdvisorConfig | None = None,
    ) -> None:
        self._client = ta_client
        self._session = session
        self._config = config
        self._ibge_records = ibge_records
        self._destino_rio_map: dict[str, tuple[uuid.UUID, str]] = destino_rio_map or {}
        self._geocoder = geocoder
        self._ta_config = ta_config

    async def produce(self, uf: str, *, run_rio: bool = True) -> None:
        """Ingest one full UF sweep for TripAdvisor attractions.

        Fetches all TripAdvisor attractions for all known geoIds in the UF,
        validates each through TripAdvisorAtrativoPayload, resolves IBGE
        municipality, resolves parent destino from destino_rio_map, writes
        each to Nascente, then (when run_rio=True) triggers the Rio pipeline.

        Args:
            uf:      Two-letter Brazilian state code (e.g. "BA", "SP").
            run_rio: When True, trigger process_nascente_record after store_raw.
        """
        # Resolve geoId for this UF
        geo_id = await self._client.resolve_geo_id(uf)
        attractions = await self._client.fetch_attractions(geo_id)

        for entity in attractions:
            try:
                await self._ingest_one(uf, entity, run_rio=run_rio)
            except Exception as exc:  # noqa: BLE001
                location_id = str(entity.get("locationId", "unknown"))
                quarantine_poison(
                    session=self._session,
                    nascente_id=None,
                    task_name="brave.ta.atrativos.produce",
                    error=str(exc),
                    payload={"uf": uf, "locationId": location_id, "error": str(exc)},
                )

    async def _ingest_one(self, uf: str, entity: dict[str, Any], *, run_rio: bool) -> None:
        """Ingest a single TripAdvisor attraction entity."""
        location_id = str(entity.get("locationId", ""))
        name = str(entity.get("name", ""))
        category = str(entity.get("category", ""))
        lat = entity.get("lat")
        lng = entity.get("lng")

        # Build review signals (LGPD boundary)
        review_count = int(entity.get("review_count", 0))
        rating = float(entity.get("rating", 0.0))
        most_recent_dt: datetime | None = None
        # most_recent_review_at: not in AttractionsFusion listing card — None at Nascente (Phase 13 decision)

        review_signals = TripAdvisorReviewSignals(
            review_count=review_count,
            rating=rating,
            most_recent_review_at=most_recent_dt,
        )

        # Compute §7.6 criterion values
        corroboracao_value = corroboracao_from_reviews(review_count, rating)
        atualidade_value = atualidade_from_recency(most_recent_dt)

        # Resolve IBGE municipality (first attempt: card lat/lng, may be None for coordless cards)
        ibge_match = resolve_municipio(
            name,
            uf,
            self._ibge_records,
            candidate_lat=lat,
            candidate_lng=lng,
        )

        # TA-15: geo-enrichment via Nominatim — only when first attempt missed.
        # Promote geocoded lat/lng into working variables so completude and the
        # persisted payload carry real coordinates (WR-01 fix: previously these
        # were discarded and lat/lng remained None even after a successful geocode).
        if ibge_match is None and self._geocoder is not None:
            geo = await self._geocoder.geocode(location_id, name, uf)
            if geo is not None:
                lat = geo["lat"]
                lng = geo["lon"]
                ibge_match = resolve_municipio(
                    geo.get("municipio_name") or name,
                    uf,
                    self._ibge_records,
                    candidate_lat=lat,
                    candidate_lng=lng,
                    max_distance_km=50.0,
                )

        # TA-ftx: geo-linkage via d3d4987463b78a39 — single GraphQL query returns
        # cityName + stateName directly. Replaces the broken parents[0].localizedName
        # path (rmz-04) where that field is absent from live TA data.
        # Validated: 5 attractions / 2 cities (SPIKE-2 2026-06-30).
        # ToS/LGPD: aggregate geo only (cityName/stateName/geoIds), no PII.
        if ibge_match is None and self._ta_config is not None:
            try:
                loc_id_int = int(location_id) if location_id else None
            except (ValueError, TypeError):
                loc_id_int = None
            if loc_id_int is not None:
                if self._ta_config.page_throttle_seconds > 0:
                    await asyncio.sleep(self._ta_config.page_throttle_seconds)
                geo = await self._client.fetch_attraction_geo(loc_id_int)
                if geo is not None:
                    derived_uf = state_name_to_uf(geo["state_name"])
                    if derived_uf:
                        ibge_match = resolve_municipio(
                            geo["city_name"],
                            derived_uf,
                            self._ibge_records,
                        )

        # WR-01: the normalized AttractionsFusion card uses camelCase `locationId`
        # and carries no `uf`/`location_id`/`lat`/`lng`, so feeding the raw card to
        # completude_from_fields (which checks snake_case keys) would only ever match
        # 4/10 fields and silently cap completude at 40. Build a completude entity
        # that maps the card onto the keys _TA_COMPLETUDE_FIELDS expects.
        # Note: lat/lng here reflect geo-enriched coordinates when geocoding resolved.
        completude_entity = {
            **entity,
            "uf": uf,
            "location_id": location_id,
            "lat": lat,
            "lng": lng,
        }
        completude_value = completude_from_fields(completude_entity, cap=100)  # atrativo cap=100

        if ibge_match is None:
            quarantine_poison(
                session=self._session,
                nascente_id=None,
                task_name="brave.ta.atrativos.ibge_unmatched",
                error=f"ibge_unmatched: could not resolve '{name}' in UF={uf}",
                payload={"uf": uf, "locationId": location_id, "name": name},
            )
            return

        # Resolve parent destino from same-sweep map (TA-03)
        map_entry = self._destino_rio_map.get(ibge_match.ibge_code)
        if map_entry is None:
            # No parent destino RioRecord found in this sweep → quarantine
            quarantine_poison(
                session=self._session,
                nascente_id=None,
                task_name="brave.ta.atrativos.parent_destino_absent",
                error=(
                    f"parent_destino_absent: no destino RioRecord for "
                    f"ibge_code={ibge_match.ibge_code} (name='{name}', UF={uf})"
                ),
                payload={
                    "uf": uf,
                    "locationId": location_id,
                    "name": name,
                    "ibge_code": ibge_match.ibge_code,
                },
            )
            return

        parent_rio_id, parent_source_ref = map_entry

        # Validate through Pydantic payload model (LGPD enforcement at parse time)
        payload_model = TripAdvisorAtrativoPayload(
            name=name,
            uf=uf,
            location_id=location_id,
            lat=lat,
            lng=lng,
            review_signals=review_signals,
            origem_value=TA_ATRATIVO_ORIGEM_VALUE,
            completude_value=completude_value,
            corroboracao_value=corroboracao_value,
            atualidade_value=atualidade_value,
            validacao_humana_value=0.0,
            parent_rio_id=str(parent_rio_id),
            parent_source_ref=parent_source_ref,
        )

        source_ref = f"tripadvisor:attraction:{location_id}"

        payload: dict[str, Any] = {
            "name": payload_model.name,
            "uf": payload_model.uf,
            "locationId": location_id,
            "lat": payload_model.lat,
            "lng": payload_model.lng,
            "municipio_id": ibge_match.ibge_code,
            # §7.6 criterion *_value fields
            "origem_value": payload_model.origem_value,
            "completude_value": payload_model.completude_value,
            "corroboracao_value": payload_model.corroboracao_value,
            "atualidade_value": payload_model.atualidade_value,
            "validacao_humana_value": payload_model.validacao_humana_value,
            # Parent destino linkage (TA-02, TA-03)
            "parent_rio_id": payload_model.parent_rio_id,
            "parent_source_ref": payload_model.parent_source_ref,
            # Review signals (LGPD-aggregate only)
            "review_count": review_count,
            "rating": rating,
            # Category from AttractionsFusion listing card (primaryInfo.text)
            "category": category,
            # Canonical sub-dict for norteia-api contract
            "canonical": {
                "name": payload_model.name,
                "uf": uf,
                "municipio": ibge_match.nome,
                "ibge_code": ibge_match.ibge_code,
                "source": "tripadvisor",
            },
        }

        nascente = store_raw(
            session=self._session,
            source="tripadvisor",
            source_ref=source_ref,
            entity_type="attraction",
            uf=uf,
            payload=payload,
        )

        if run_rio:
            process_nascente_record(
                session=self._session,
                nascente=nascente,
                config=self._config,
            )

    # -----------------------------------------------------------------------
    # Bulk national ingest path (Phase 15, TA-12) — DISTINCT from _ingest_one.
    # -----------------------------------------------------------------------

    async def _ingest_one_bulk(self, entity: dict[str, Any], *, run_rio: bool) -> bool:
        """Ingest a single attraction WITHOUT a parent destino (bulk national path).

        Resolves the operator-locked A1 blocker: the all-Brazil bulk lane
        (geoId 294280) has no per-UF context and no parent destino. ``uf`` +
        município are DERIVED from the attraction's national geocode
        (``geocode_national``) + nearest-IBGE-seat resolution
        (``resolve_municipio_national``). The parent-destino gate of
        ``_ingest_one`` (the ``parent_destino_absent`` quarantine) is intentionally
        DROPPED here — bulk records land in Nascente parent-less and still pass
        §7.6 + DLQ (the canonical gate; CONTEXT A1). ``_ingest_one`` is left
        byte-for-byte unchanged.

        A card that cannot be geocoded OR has no IBGE seat within radius is
        quarantined as ``ibge_unmatched`` (no Nascente row) — never silently
        dropped. LGPD: only ``review_count`` / ``rating`` (+ ``most_recent_review_at``
        = None) reach review_signals (``TripAdvisorReviewSignals`` ``extra="forbid"``).

        Args:
            entity:  Normalized AttractionsFusion card dict (camelCase ``locationId``).
            run_rio: When True, trigger process_nascente_record after store_raw.

        Returns:
            True when a Nascente row was written; False when the card was
            quarantined as ``ibge_unmatched`` (so the caller can count it as a
            live error — the panel error counter must reflect unmatched cards).
        """
        location_id = str(entity.get("locationId", ""))
        name = str(entity.get("name", ""))
        category = str(entity.get("category", ""))

        # Build review signals (LGPD boundary — aggregate only)
        review_count = int(entity.get("review_count", 0))
        rating = float(entity.get("rating", 0.0))
        most_recent_dt: datetime | None = None
        # most_recent_review_at: not in AttractionsFusion listing card — None at Nascente.

        review_signals = TripAdvisorReviewSignals(
            review_count=review_count,
            rating=rating,
            most_recent_review_at=most_recent_dt,
        )

        # Compute §7.6 criterion values
        corroboracao_value = corroboracao_from_reviews(review_count, rating)
        atualidade_value = atualidade_from_recency(most_recent_dt)

        # Derive coordinates + município nationally (no UF input). The bulk lane
        # has no per-UF context — the only signal is the geocoded lat/lng, which
        # resolve_municipio_national maps to the nearest IBGE seat across ALL states.
        lat: float | None = None
        lng: float | None = None
        ibge_match: IbgeMunicipio | None = None
        if self._geocoder is not None:
            geo = await self._geocoder.geocode_national(location_id, name)
            if geo is not None:
                lat = geo["lat"]
                lng = geo["lon"]
                ibge_match = resolve_municipio_national(
                    lat,
                    lng,
                    self._ibge_records,
                    max_distance_km=50.0,
                )

        if ibge_match is None:
            # No geocode OR no IBGE seat within radius → quarantine (never dropped).
            # NO parent_destino_absent path exists in the bulk lane (gate dropped).
            quarantine_poison(
                session=self._session,
                nascente_id=None,
                task_name="brave.ta.atrativos.ibge_unmatched",
                error=f"ibge_unmatched: could not geo-resolve attraction locationId={location_id}",
                # LGPD: locationId only — never name/address.
                payload={"locationId": location_id},
            )
            return False

        # Derive UF from the matched IBGE record — NOT from any input arg.
        uf = ibge_match.uf

        # WR-01: map the camelCase card onto the snake_case keys completude expects
        # (uf, location_id, lat, lng) — lat/lng reflect the geocoded coordinates.
        completude_entity = {
            **entity,
            "uf": uf,
            "location_id": location_id,
            "lat": lat,
            "lng": lng,
        }
        completude_value = completude_from_fields(completude_entity, cap=100)

        # Validate through Pydantic payload model (LGPD enforcement at parse time).
        # Bulk path: parent linkage is deferred — parents are None (schema default).
        payload_model = TripAdvisorAtrativoPayload(
            name=name,
            uf=uf,
            location_id=location_id,
            lat=lat,
            lng=lng,
            review_signals=review_signals,
            origem_value=TA_ATRATIVO_ORIGEM_VALUE,
            completude_value=completude_value,
            corroboracao_value=corroboracao_value,
            atualidade_value=atualidade_value,
            validacao_humana_value=0.0,
            parent_rio_id=None,
            parent_source_ref=None,
        )

        source_ref = f"tripadvisor:attraction:{location_id}"

        payload: dict[str, Any] = {
            "name": payload_model.name,
            "uf": payload_model.uf,
            "locationId": location_id,
            "lat": payload_model.lat,
            "lng": payload_model.lng,
            "municipio_id": ibge_match.ibge_code,
            # §7.6 criterion *_value fields
            "origem_value": payload_model.origem_value,
            "completude_value": payload_model.completude_value,
            "corroboracao_value": payload_model.corroboracao_value,
            "atualidade_value": payload_model.atualidade_value,
            "validacao_humana_value": payload_model.validacao_humana_value,
            # Parent destino linkage deferred in the bulk lane (None).
            "parent_rio_id": payload_model.parent_rio_id,
            "parent_source_ref": payload_model.parent_source_ref,
            # Review signals (LGPD-aggregate only)
            "review_count": review_count,
            "rating": rating,
            # Category from AttractionsFusion listing card (primaryInfo.text)
            "category": category,
            # Canonical sub-dict for norteia-api contract
            "canonical": {
                "name": payload_model.name,
                "uf": uf,
                "municipio": ibge_match.nome,
                "ibge_code": ibge_match.ibge_code,
                "source": "tripadvisor",
            },
        }

        nascente = store_raw(
            session=self._session,
            source="tripadvisor",
            source_ref=source_ref,
            entity_type="attraction",
            uf=uf,
            payload=payload,
        )

        if run_rio:
            process_nascente_record(
                session=self._session,
                nascente=nascente,
                config=self._config,
            )
        return True

    async def produce_paginated(
        self,
        geo_id: int,
        start_page: int,
        max_pages: int,
        redis: Any,
        *,
        run_rio: bool = True,
    ) -> None:
        """Drive the paginated HTML-SSR client and bulk-ingest each page (Phase 15).

        Streams ``(offset, cards)`` tuples from ``fetch_attractions_paginated``,
        ingests every card via ``_ingest_one_bulk`` (parent-less national path),
        COMMITS once PER PAGE (Pitfall 3 — a mid-run 403 leaves durable records +
        an accurate resume point), then records progress + the live error counter.

        Ordering: the per-page ``commit()`` happens BEFORE ``record_page`` so
        ``last_completed_offset`` only ever advances past durable records.

        Error counter: per-card ingest failures — both raised exceptions AND
        ``ibge_unmatched`` quarantines (the common case: cards carry no lat/lng) —
        increment the live panel ``error_count`` via ``sweep_progress.record_error``.

        ``SessionExpiredError`` raised by the client iterator propagates OUT (the
        task layer, 15-07, handles fail-fast + needs_bootstrap) — it is NOT
        swallowed here.

        Args:
            geo_id:     TripAdvisor integer geoId (294280 = all Brazil).
            start_page: 1-based resume page (page 1 = offset 0, page 2 = offset 30).
            max_pages:  Cap on pages to fetch this run.
            redis:      Sync Redis client for the live progress hash (fakeredis-safe).
            run_rio:    When True, trigger the Rio pipeline per ingested card.
        """
        async for offset, cards in self._client.fetch_attractions_paginated(
            geo_id, start_page, max_pages
        ):
            ingested = 0
            errors = 0
            for card in cards:
                try:
                    wrote_row = await self._ingest_one_bulk(card, run_rio=run_rio)
                except Exception as exc:  # noqa: BLE001
                    wrote_row = False
                    location_id = str(card.get("locationId", "unknown"))
                    quarantine_poison(
                        session=self._session,
                        nascente_id=None,
                        task_name="brave.ta.atrativos.produce_paginated",
                        error=str(exc),
                        # LGPD: offset + locationId + error-class only — never name/address.
                        payload={"offset": offset, "locationId": location_id, "error": str(exc)},
                    )
                if wrote_row:
                    ingested += 1
                else:
                    # ibge_unmatched (returned False) OR a raised failure → live error.
                    errors += 1
                    sweep_progress.record_error(redis)
            # Commit BEFORE record_page so last_completed_offset never points at
            # rolled-back data (Pitfall 3 resume integrity).
            self._session.commit()
            sweep_progress.record_page(redis, offset, ingested)
            logger.info(
                "ta_bulk_page_ingested",
                offset=offset,
                ingested=ingested,
                errors=errors,
            )
