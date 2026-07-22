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
review data). Promotion to Mar requires a human steward's audited transition
through the reliability gate — there is no automated Mar push path for this lane.

Mirrors TripAdvisorDestinosIngest in structure. Adds parent destino linkage:
  - parent_rio_id: from destino_rio_map (dict[ibge_code, (rio_id, source_ref)])
  - parent_source_ref: from same map entry
  - parent_mar_id: only if the destino is already in Mar (optional)

D-18: This module imports only from brave.core, brave.clients, brave.config,
and brave.domains.tripadvisor.*. It does NOT import from other lane modules.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy.orm import Session

from brave.config.settings import ScoreConfig, TripAdvisorConfig
from brave.core import engine as collection_engine
from brave.core.models import whatsapp_candidate_from_phone
from brave.core.nascente.service import store_raw
from brave.core.quarantine import quarantine_poison
from brave.core.rio.routing import process_nascente_record
from brave.domains.tripadvisor import sweep_progress
from brave.domains.tripadvisor.ibge import (
    IbgeMunicipio,
    resolve_municipio,
    resolve_municipio_national,
)
from brave.domains.tripadvisor.schemas import TripAdvisorAtrativoPayload, TripAdvisorReviewSignals
from brave.domains.tripadvisor.scoring import (
    atualidade_from_recency,
    completude_from_fields,
    corroboracao_from_reviews,
)
from brave.domains.tripadvisor.uf_names import state_name_to_uf
from brave.observability.record_events import record_event_once
from brave.shared.destino import ensure_destino

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
    destino RioRecord from destino_rio_map (built by sweep_tripadvisor
    from authoritative destination RioRecords already in Rio — Mtur/IBGE, origem=100).
    When the parent destino is absent from the map, it is CREATED on demand from the
    resolved IBGE município (source="ibge", origem=100) via _ensure_destino and the
    atrativo is linked to it (destino-first) — there is NO parent_destino_absent
    quarantine. The synthesized destino is cached in destino_rio_map for the rest of
    the sweep (diverges from discovery_agent.py's Mar-only resolution — see
    CONTEXT.md TA-03).

    Args:
        ta_client:       TripAdvisorClientProtocol implementation (real or fake).
        session:         SQLAlchemy synchronous Session.
        config:          ScoreConfig with reliability weights and thresholds.
        ibge_records:    Pre-loaded IBGE municipality records (from load_ibge_csv).
        destino_rio_map: Optional mapping from ibge_code → (rio_id, source_ref) for
                         destinos produced in the same sweep. None or empty dict is
                         fine — missing parents are auto-created via _ensure_destino
                         and cached back into the map.
    """

    def __init__(
        self,
        ta_client: TripAdvisorClientProtocol,
        session: Session,
        config: ScoreConfig,
        ibge_records: list[IbgeMunicipio],
        destino_rio_map: dict[str, tuple[uuid.UUID, str]] | None = None,
        geocoder: GeocoderClientProtocol | None = None,
        ta_config: TripAdvisorConfig | None = None,
        places_agent: Any | None = None,
    ) -> None:
        self._client = ta_client
        self._session = session
        self._config = config
        self._ibge_records = ibge_records
        self._destino_rio_map: dict[str, tuple[uuid.UUID, str]] = destino_rio_map or {}
        self._geocoder = geocoder
        self._ta_config = ta_config
        # PlacesEnrichmentAgent (description + distrito + hours/contact/price + liveness),
        # run INLINE per record after Rio routing — like the other completude steps. None
        # (unit tests / bulk path) → no enrichment, record just routes on its TA floor.
        self._places_agent = places_agent

    async def produce(
        self,
        uf: str,
        *,
        run_rio: bool = True,
        enrich_reviews: bool = False,
        redis: Any | None = None,
        max_per_uf: int | None = None,
    ) -> list[str]:
        """Ingest one full UF sweep for TripAdvisor attractions.

        Paginates ALL TripAdvisor attractions for the UF's geoId via the GraphQL
        paginated listing (``fetch_attractions_paginated_gql`` — replaces the
        deprecated single-page ``fetch_attractions`` and the fragile HTML-SSR path),
        validates each card through TripAdvisorAtrativoPayload, resolves IBGE
        municipality, resolves parent destino from destino_rio_map, writes each to
        Nascente, then (when run_rio=True) triggers the Rio pipeline.

        Commit granularity depends on ``enrich_reviews``:
            - enrich_reviews=False (bulk / Nascente-only): PER-PAGE commit — each yielded
              page's rows are committed once at page end (live kanban, mirrors
              produce_paginated). This is the historical behavior and is unchanged.
            - enrich_reviews=True (per-UF enrich path): PER-ATRATIVO commit — every
              SUCCESSFUL _ingest_one is committed immediately for durability, and every
              failure is isolated (rollback → cache eviction → poison → commit) so one
              poison card neither loses prior atrativos' work nor corrupts the
              destino_rio_map cache.

        Args:
            uf:             Two-letter Brazilian state code (e.g. "BA", "SP").
            run_rio:        When True, trigger process_nascente_record after store_raw.
            enrich_reviews: When True, each card with a numeric locationId triggers ONE
                            ``fetch_recent_review`` call so ``most_recent_review_at`` is
                            populated and atualidade lifts the reliability score (per-UF path).
                            Off (today's Nascente behavior) for bulk / Nascente-only.
            max_per_uf:     Optional operator test-run cap — stop after N attractions have
                            been PROCESSED for this UF (successes + poison both count) and
                            break pagination so no further pages are fetched. ``None``
                            (default) = full sweep, behavior unchanged.

        Returns:
            The RioRecord ids (str) of every atrativo successfully ingested into Rio
            this sweep (empty when run_rio=False). The task layer dispatches description
            enrichment for these — the TA lane's only path to descricao_editorial.
        """
        # Resolve geoId for this UF, then paginate the GraphQL listing. Every page
        # yields (offset, cards); _ingest_one per card keeps the per-entity
        # try/quarantine so a single poison card never aborts the sweep.
        geo_id = await self._client.resolve_geo_id(uf)

        # Operator test-run throttle: count attractions PROCESSED (success + poison)
        # and stop once the cap is hit. None = uncapped (full sweep).
        processed = 0
        # Rio ids of atrativos ingested this sweep — returned for description enrichment.
        ingested_ids: list[str] = []

        async for _offset, cards in self._client.fetch_attractions_paginated_gql(geo_id):
            # Mid-sweep pause/off/stop: this producer was fanned out per UF and keeps
            # paginating after the orchestrator's dispatch loop broke. Poll the engine
            # BEFORE ingesting each page so a Motor Pausado/Desligado stops new inserts
            # (prior pages already committed). Gate skipped when redis is None (unit
            # tests call produce() directly) — behavior then unchanged.
            if redis is not None and collection_engine.should_halt_producer(redis):
                logger.info("ta_atrativos_producer_halt", uf=uf, offset=_offset)
                break
            reached_cap = False
            for entity in cards:
                # Snapshot the destino cache keys BEFORE the attempt so that, on the
                # enrich path, a rollback can evict any destino cached by _ensure_destino
                # during THIS (now discarded) iteration — otherwise the map would point
                # the next same-município atrativo at a parent rio whose rows no longer
                # exist. Snapshot only when enrich_reviews (per-atrativo rollback path).
                keys_before = set(self._destino_rio_map) if enrich_reviews else None
                try:
                    _rio_id = await self._ingest_one(
                        uf, entity, run_rio=run_rio, enrich_reviews=enrich_reviews
                    )
                    if _rio_id is not None:
                        ingested_ids.append(_rio_id)
                    if enrich_reviews:
                        # Per-atrativo durability: persist this atrativo's rows now.
                        self._session.commit()
                except Exception as exc:  # noqa: BLE001
                    if enrich_reviews:
                        # Isolate the failed atrativo: discard its partial writes and
                        # clear any aborted-transaction state before writing poison.
                        self._session.rollback()
                        # Cache coherence: evict destinos cached this iteration but now
                        # rolled back, so the next same-município atrativo re-creates them.
                        for k in set(self._destino_rio_map) - keys_before:  # type: ignore[operator]
                            del self._destino_rio_map[k]
                    # Unified locationId fallback (str(... or "")) so this failure
                    # source_ref matches the success path's default and record_event_once
                    # dedups correctly (no "unknown" vs "" identity split).
                    location_id = str(entity.get("locationId") or "")
                    quarantine_poison(
                        session=self._session,
                        nascente_id=None,
                        task_name="brave.ta.atrativos.produce",
                        error=str(exc),
                        payload={"uf": uf, "locationId": location_id, "error": str(exc)},
                    )
                    # Terminal Log-tab event alongside the poison chip so the Falha
                    # card carries a stable identity (source_ref) + the error. Idempotent
                    # (record_event_once) so a persistently-failing card does not re-emit
                    # its terminal event every sweep. LGPD: locationId + error string
                    # only — never name/address/PII.
                    record_event_once(
                        self._session,
                        source="tripadvisor",
                        source_ref=f"tripadvisor:attraction:{location_id}",
                        stage="quarantined",
                        status="fail",
                        message=str(exc),
                        entity_type="attraction",
                        uf=uf,
                        data={"locationId": location_id, "error": str(exc)},
                    )
                    if enrich_reviews:
                        # Persist the poison row independently (durable per-atrativo).
                        self._session.commit()
                # Count this attraction as processed (success + poison both count) and
                # trip the operator cap once N have been handled for this UF.
                processed += 1
                if max_per_uf is not None and processed >= max_per_uf:
                    reached_cap = True
                    break
            if not enrich_reviews:
                # Live kanban: commit each page's ingested rows immediately so they become
                # visible in the /painel board WHILE the sweep is still running (mirrors
                # produce_paginated's per-page commit). Without this the per-UF producer
                # committed only once at the very end and nothing showed mid-processing.
                self._session.commit()
            if reached_cap:
                # Operator test-run cap hit: commit the partial page above (already done
                # for the non-enrich path; per-atrativo commits cover enrich) then stop
                # paginating — no further TripAdvisor pages are fetched for this UF.
                logger.info(
                    "ta_atrativos_producer_cap_reached",
                    uf=uf,
                    processed=processed,
                    max_per_uf=max_per_uf,
                )
                break

        return ingested_ids

    def _ensure_destino(self, ibge_match: IbgeMunicipio) -> tuple[uuid.UUID, str]:
        """Create the parent destino for an IBGE município on demand (destino-first).

        Replaces the old ``parent_destino_absent`` quarantine: when a TA per-UF
        atrativo's parent destino is missing from ``destino_rio_map``, this
        synthesizes an authoritative IBGE destino (source="ibge", origem=100) so the
        atrativo can be linked immediately rather than dropped.

        Idempotent + safe to call repeatedly for the same município: ``store_raw``
        dedups by (source, source_ref, content_hash) and ``process_nascente_record``
        is idempotent. The caller caches the result in ``destino_rio_map`` for the
        rest of the sweep.

        Args:
            ibge_match: The resolved IBGE municipality record.

        Returns:
            (rio_id, source_ref) for the created (or existing) destino RioRecord.
        """
        # Delegate to the shared helper (brave.shared.destino.ensure_destino),
        # which returns (parent_rio_id, parent_source_ref, parent_mar_id | None).
        # TA keeps its historical two-tuple external contract — callers cache
        # (rio_id, source_ref) into destino_rio_map — so drop the optional Mar id.
        parent_rio_id, parent_source_ref, _parent_mar_id = ensure_destino(
            self._session,
            self._config,
            ibge_code=ibge_match.ibge_code,
            nome=ibge_match.nome,
            uf=ibge_match.uf,
        )
        return (parent_rio_id, parent_source_ref)

    async def _ingest_one(
        self,
        uf: str,
        entity: dict[str, Any],
        *,
        run_rio: bool,
        enrich_reviews: bool = False,
    ) -> str | None:
        """Ingest a single TripAdvisor attraction entity.

        When ``enrich_reviews`` is True and the card carries a numeric locationId,
        one ``fetch_recent_review`` call fills ``most_recent_review_at`` (LGPD-aggregate
        only: totalCount + newest publishedDate + rating) so ``atualidade_from_recency``
        lifts the reliability score; ``review_count``/``rating`` are overridden from that
        precise container when present. Off → today's behavior (recency None).

        Returns the created/updated atrativo's RioRecord id (str) when ``run_rio`` is
        True, else None — the caller (produce) collects these so the task layer can
        dispatch description enrichment for this sweep's atrativos (TA lane never enters
        the Places FSM chain, so this is its ONLY description-enrichment entry point).
        """
        location_id = str(entity.get("locationId", ""))
        name = str(entity.get("name", ""))
        category = str(entity.get("category", ""))
        lat = entity.get("lat")
        lng = entity.get("lng")

        # Universal drawer/Log key — exists from the first stage (before any row) and
        # is shared by every success AND the terminal quarantine event below.
        source_ref = f"tripadvisor:attraction:{location_id}"

        # Success-stage Log events are BUFFERED here and flushed by store_raw ONLY when a
        # NEW Nascente row is created (behind the content_hash early-return), so a re-sweep
        # of an already-ingested card does not re-emit them. Each entry is record_event(...)
        # kwargs MINUS session/nascente_id. Terminal quarantine events stay direct (below).
        timeline: list[dict[str, Any]] = []

        # Stage 1 — card synced from TripAdvisor. LGPD: name is public-geo.
        timeline.append(
            {
                "source": "tripadvisor",
                "source_ref": source_ref,
                "stage": "tripadvisor_synced",
                "status": "ok",
                "message": name,
                "entity_type": "attraction",
                "uf": uf,
            }
        )

        # Build review signals (LGPD boundary)
        review_count = int(entity.get("review_count", 0))
        rating = float(entity.get("rating", 0.0))
        most_recent_dt: datetime | None = None
        # most_recent_review_at: NOT in the AttractionsFusion listing card. Off by
        # default (bulk / Nascente-only) → None. Under enrich_reviews (per-UF sweep),
        # fetch_recent_review supplies the newest review date (+ precise count/rating).

        if enrich_reviews:
            try:
                loc_id_int = int(location_id) if location_id else None
            except (ValueError, TypeError):
                loc_id_int = None
            if loc_id_int is not None:
                # Mirror the geo-enrichment throttle: pace review calls when configured.
                if self._ta_config is not None and self._ta_config.page_throttle_seconds > 0:
                    await asyncio.sleep(self._ta_config.page_throttle_seconds)
                recency = await self._client.fetch_recent_review(loc_id_int)
                if recency is not None:
                    most_recent_dt = recency.get("most_recent_review_at")
                    # Prefer the precise review container over the card's aggregate.
                    if recency.get("review_count") is not None:
                        review_count = int(recency["review_count"])
                    if recency.get("rating") is not None:
                        rating = float(recency["rating"])

        review_signals = TripAdvisorReviewSignals(
            review_count=review_count,
            rating=rating,
            most_recent_review_at=most_recent_dt,
        )

        # Stage 2 — review enrichment. ok when a recent-review date was fetched
        # (enrich path), skip otherwise. LGPD: aggregate signals only, no review text.
        timeline.append(
            {
                "source": "tripadvisor",
                "source_ref": source_ref,
                "stage": "review_enriched",
                "status": ("ok" if (enrich_reviews and most_recent_dt is not None) else "skip"),
                "entity_type": "attraction",
                "uf": uf,
                "data": {"review_count": review_count, "rating": rating},
            }
        )

        # Compute reliability criterion values
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
                if ibge_match is not None:
                    # Stage — geolocation complemented via Nominatim (fallback A).
                    timeline.append(
                        {
                            "source": "tripadvisor",
                            "source_ref": source_ref,
                            "stage": "geo_enriched",
                            "status": "ok",
                            "message": name,
                            "entity_type": "attraction",
                            "uf": uf,
                            "data": {"via": "nominatim"},
                        }
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
                    if derived_uf and geo.get("city_name"):
                        ibge_match = resolve_municipio(
                            geo["city_name"],
                            derived_uf,
                            self._ibge_records,
                        )
                        if ibge_match is not None:
                            # Stage — geolocation complemented via TA geo query (fallback B).
                            timeline.append(
                                {
                                    "source": "tripadvisor",
                                    "source_ref": source_ref,
                                    "stage": "geo_enriched",
                                    "status": "ok",
                                    "message": name,
                                    "entity_type": "attraction",
                                    "uf": uf,
                                    "data": {"via": "ta_geo"},
                                }
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
            # TERMINAL Log-tab event alongside the poison chip (kept). Idempotent
            # (record_event_once) so a persistently-unmatched card does not re-emit its
            # terminal event every sweep. LGPD: name is public-geo; reason + locationId
            # only — never PII/review text.
            record_event_once(
                self._session,
                source="tripadvisor",
                source_ref=source_ref,
                stage="quarantined",
                status="fail",
                message=f"ibge_unmatched: '{name}'",
                entity_type="attraction",
                uf=uf,
                data={
                    "reason": "ibge_unmatched",
                    "name": name,
                    "locationId": location_id,
                },
            )
            return

        # Município resolved — public-geo (IBGE code + name).
        timeline.append(
            {
                "source": "tripadvisor",
                "source_ref": source_ref,
                "stage": "municipio_resolved",
                "status": "ok",
                "message": ibge_match.nome,
                "entity_type": "attraction",
                "uf": uf,
                "data": {"ibge_code": ibge_match.ibge_code, "municipio": ibge_match.nome},
            }
        )

        # Resolve parent destino from same-sweep map (TA-03). When the destino is
        # absent, create it on demand from the IBGE município (destino-first), cache
        # it for the rest of the sweep, then link the atrativo — NO quarantine.
        map_entry = self._destino_rio_map.get(ibge_match.ibge_code)
        if map_entry is None:
            map_entry = self._ensure_destino(ibge_match)
            self._destino_rio_map[ibge_match.ibge_code] = map_entry

        parent_rio_id, parent_source_ref = map_entry

        # Parent destino linked (destino-first). LGPD: engineering id only.
        timeline.append(
            {
                "source": "tripadvisor",
                "source_ref": source_ref,
                "stage": "parent_destino_linked",
                "status": "ok",
                "entity_type": "attraction",
                "uf": uf,
                "data": {"parent_rio_id": str(parent_rio_id)},
            }
        )

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

        # Validated through the Pydantic payload model (LGPD enforced at parse time).
        timeline.append(
            {
                "source": "tripadvisor",
                "source_ref": source_ref,
                "stage": "validated",
                "status": "ok",
                "message": payload_model.name,
                "entity_type": "attraction",
                "uf": uf,
            }
        )

        payload: dict[str, Any] = {
            "name": payload_model.name,
            "uf": payload_model.uf,
            "locationId": location_id,
            "lat": payload_model.lat,
            "lng": payload_model.lng,
            "municipio_id": ibge_match.ibge_code,
            # reliability criterion *_value fields
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
                # Reserved distrito/subdistrito keys — uniform wire shape across lanes.
                # TA cards carry no sub-município text → stay None (see Places lane).
                "distrito_name": None,
                "distrito_code": None,
                "distrito_municipio_ibge": None,
                "subdistrito_name": None,
                "subdistrito_code": None,
            },
        }

        # Phase F: MASKED WhatsApp-candidate seam. TripAdvisor AttractionsFusion cards
        # carry NO phone (LGPD-aggregate-only lane, "NO WHATSAPP OUTREACH") so this is a
        # no-op today; if a phone is ever added to the card it is captured MASKED only
        # (whatsapp_candidate_from_phone → mask_phone) and NEVER as a raw number. The
        # value rides into normalized["contact"] via process_nascente_record.
        whatsapp_candidate = whatsapp_candidate_from_phone(entity.get("phone"))
        if whatsapp_candidate is not None:
            payload["contact"] = {"whatsapp_candidate": whatsapp_candidate}

        nascente = store_raw(
            session=self._session,
            source="tripadvisor",
            source_ref=source_ref,
            entity_type="attraction",
            uf=uf,
            payload=payload,
            # Flush the buffered success-stage Log events ONLY on a NEW Nascente row
            # (behind store_raw's content_hash early-return) so a re-sweep of an
            # already-ingested card does not duplicate the timeline.
            timeline=timeline,
        )

        if run_rio:
            rio = process_nascente_record(
                session=self._session,
                nascente=nascente,
                config=self._config,
            )
            # Inline enrichment (description + distrito + hours/contact/price + liveness),
            # run here — like geo/review/município above — so the stored score reflects it
            # and a later task-kill can't strand it (the old post-produce dispatch could).
            # The agent flushes into this session; produce() owns the commit. Its own
            # re-score is the final word on routing. Never raises (keeps the TA floor).
            if self._places_agent is not None and (rio.canonical_key or "").startswith(
                "tripadvisor:"
            ):
                await self._places_agent.run(rio)
            return str(rio.id)
        return None

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
        reliability scoring + DLQ (the canonical gate; CONTEXT A1). ``_ingest_one`` is left
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

        # Compute reliability criterion values
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
            # TERMINAL Log-tab event alongside the poison chip. Idempotent
            # (record_event_once) so a persistently-unmatched card does not re-emit its
            # terminal event every sweep. Bulk lane has no UF context here (ibge
            # unresolved) → uf=None. LGPD: locationId only.
            record_event_once(
                self._session,
                source="tripadvisor",
                source_ref=f"tripadvisor:attraction:{location_id}",
                stage="quarantined",
                status="fail",
                message=f"ibge_unmatched: locationId={location_id}",
                entity_type="attraction",
                data={"reason": "ibge_unmatched", "locationId": location_id},
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
            # reliability criterion *_value fields
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
                # Reserved distrito/subdistrito keys — uniform wire shape across lanes.
                # TA cards carry no sub-município text → stay None (see Places lane).
                "distrito_name": None,
                "distrito_code": None,
                "distrito_municipio_ibge": None,
                "subdistrito_name": None,
                "subdistrito_code": None,
            },
        }

        # Phase F: MASKED WhatsApp-candidate seam. TripAdvisor AttractionsFusion cards
        # carry NO phone (LGPD-aggregate-only lane, "NO WHATSAPP OUTREACH") so this is a
        # no-op today; if a phone is ever added to the card it is captured MASKED only
        # (whatsapp_candidate_from_phone → mask_phone) and NEVER as a raw number. The
        # value rides into normalized["contact"] via process_nascente_record.
        whatsapp_candidate = whatsapp_candidate_from_phone(entity.get("phone"))
        if whatsapp_candidate is not None:
            payload["contact"] = {"whatsapp_candidate": whatsapp_candidate}

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
        """Drive the paginated GraphQL listing and bulk-ingest each page (Phase 15).

        Streams ``(offset, cards)`` tuples from ``fetch_attractions_paginated_gql``
        (Phase G — replaces the deprecated fragile HTML-SSR ``fetch_attractions_paginated``;
        same (offset, cards) yield shape), ingests every card via ``_ingest_one_bulk``
        (parent-less national path — bulk stays enrich_reviews=False, no per-card review
        calls at 10k scale),
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
        async for offset, cards in self._client.fetch_attractions_paginated_gql(
            geo_id, start_page, max_pages
        ):
            # Mid-sweep pause/off/stop honored at page granularity (prior pages are
            # already committed + progress recorded, so the resume point is intact).
            # Mode-keyed so a standalone bulk run (state IDLE) still runs but obeys a
            # painel PAUSADO/DESLIGADO. See engine.should_halt_producer.
            if collection_engine.should_halt_producer(redis):
                logger.info("ta_bulk_producer_halt", offset=offset)
                break
            ingested = 0
            errors = 0
            for card in cards:
                try:
                    wrote_row = await self._ingest_one_bulk(card, run_rio=run_rio)
                except Exception as exc:  # noqa: BLE001
                    wrote_row = False
                    # Unified locationId fallback (str(... or "")) so this failure
                    # source_ref matches the success path's default and record_event_once
                    # dedups correctly (no "unknown" vs "" identity split).
                    location_id = str(card.get("locationId") or "")
                    quarantine_poison(
                        session=self._session,
                        nascente_id=None,
                        task_name="brave.ta.atrativos.produce_paginated",
                        error=str(exc),
                        # LGPD: offset + locationId + error-class only — never name/address.
                        payload={"offset": offset, "locationId": location_id, "error": str(exc)},
                    )
                    # TERMINAL Log-tab event alongside the poison chip. Idempotent
                    # (record_event_once) so a persistently-failing card does not re-emit
                    # its terminal event every sweep. LGPD: offset + locationId + error
                    # string only — never name/address/PII.
                    record_event_once(
                        self._session,
                        source="tripadvisor",
                        source_ref=f"tripadvisor:attraction:{location_id}",
                        stage="quarantined",
                        status="fail",
                        message=str(exc),
                        entity_type="attraction",
                        data={"offset": offset, "locationId": location_id, "error": str(exc)},
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
