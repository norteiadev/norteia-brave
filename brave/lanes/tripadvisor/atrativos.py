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

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session

from brave.config.settings import ScoreConfig
from brave.core.nascente.service import store_raw
from brave.core.quarantine import quarantine_poison
from brave.core.rio.routing import process_nascente_record
from brave.lanes.tripadvisor.ibge import IbgeMunicipio, resolve_municipio
from brave.lanes.tripadvisor.schemas import TripAdvisorAtrativoPayload, TripAdvisorReviewSignals
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
    ) -> None:
        self._client = ta_client
        self._session = session
        self._config = config
        self._ibge_records = ibge_records
        self._destino_rio_map: dict[str, tuple[uuid.UUID, str]] = destino_rio_map or {}
        self._geocoder = geocoder

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
