"""TripAdvisorDestinosIngest — TA-02.

This lane scrapes TripAdvisor destination listings via the GraphQL hybrid
client (TripAdvisorClientProtocol) and ingests them into Nascente with
source='tripadvisor', entity_type='destination', origem=65.0.

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

Mirrors brave/lanes/destinos/mtur.py exactly in class structure (D-18).

D-04: Producers populate *_value fields in the Nascente payload; the Rio
normalizer reads them from process_nascente_record — no core changes required.

D-18: This module imports only from brave.core, brave.clients, brave.config,
and brave.domains.tripadvisor.*. It does NOT import from other lane modules.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session

from brave.config.settings import ScoreConfig
from brave.core.nascente.service import store_raw
from brave.core.quarantine import quarantine_poison
from brave.core.rio.routing import process_nascente_record
from brave.domains.tripadvisor.ibge import IbgeMunicipio, resolve_municipio
from brave.domains.tripadvisor.schemas import TripAdvisorDestinoPayload, TripAdvisorReviewSignals
from brave.domains.tripadvisor.scoring import (
    atualidade_from_recency,
    completude_from_fields,
    corroboracao_from_reviews,
)
from brave.observability.record_events import record_event_once

if TYPE_CHECKING:
    from brave.clients.base import TripAdvisorClientProtocol


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TA_DESTINO_ORIGEM_VALUE = 65.0
# origem=65 (>Places 60, <gov 100 — firewall: TA never crosses 85 on origem alone).
# Source: CONTEXT.md TA-04.


# ---------------------------------------------------------------------------
# Lane implementation
# ---------------------------------------------------------------------------


class TripAdvisorDestinosIngest:
    """TripAdvisor destinations ingestion lane — ingests destinos into Nascente.

    Mirrors MturSeedIngest (brave/lanes/destinos/mtur.py) in structure.
    For each destination returned by the TA client, validates via
    TripAdvisorDestinoPayload, resolves IBGE, writes a NascenteRecord via
    store_raw, and (when run_rio=True) triggers the Rio pipeline.

    Args:
        ta_client:    TripAdvisorClientProtocol implementation (real or fake).
        session:      SQLAlchemy synchronous Session.
        config:       ScoreConfig with §7.6 weights and thresholds.
        ibge_records: Pre-loaded IBGE municipality records (from load_ibge_csv).
    """

    def __init__(
        self,
        ta_client: TripAdvisorClientProtocol,
        session: Session,
        config: ScoreConfig,
        ibge_records: list[IbgeMunicipio],
    ) -> None:
        self._client = ta_client
        self._session = session
        self._config = config
        self._ibge_records = ibge_records

    async def produce(self, uf: str, *, run_rio: bool = True) -> None:
        """Ingest one full UF sweep for TripAdvisor destinations.

        Fetches all TripAdvisor destinations for the given state, validates
        each through TripAdvisorDestinoPayload, resolves IBGE municipality,
        writes each to Nascente with source='tripadvisor' and origem_value=65,
        then (when run_rio=True) triggers the Rio pipeline.

        run_rio is the depth gate: the orchestrator owns the depth read and
        passes run_rio down. This lane NEVER reads Redis depth itself.

        Idempotent: store_raw deduplicates by (source, source_ref, content_hash).

        Args:
            uf:      Two-letter Brazilian state code (e.g. "BA", "SP").
            run_rio: When True, trigger process_nascente_record after store_raw.
                     When False, Nascente-only (no RioRecord created).
        """
        destinations = await self._client.fetch_destinations(uf)

        for entity in destinations:
            try:
                self._ingest_one(uf, entity, run_rio=run_rio)
            except Exception as exc:  # noqa: BLE001
                # Quarantine the entity and continue with the rest of the sweep
                location_id = str(entity.get("locationId", "unknown"))
                quarantine_poison(
                    session=self._session,
                    nascente_id=None,
                    task_name="brave.ta.destinos.produce",
                    error=str(exc),
                    payload={"uf": uf, "locationId": location_id, "error": str(exc)},
                )
                # TERMINAL Log-tab event alongside the poison chip so the Falha card
                # carries a stable identity + the error. Idempotent (record_event_once)
                # so a persistently-failing card does not re-emit every sweep. LGPD:
                # locationId + reason string + public-geo name/uf only — never PII.
                record_event_once(
                    session=self._session,
                    source="tripadvisor",
                    source_ref=f"tripadvisor:destination:{location_id}",
                    stage="quarantined",
                    status="fail",
                    entity_type="destination",
                    uf=uf,
                    message=str(exc),
                    data={
                        "reason": "produce_error",
                        "name": str(entity.get("name", "")),
                        "locationId": location_id,
                    },
                )

    def _ingest_one(self, uf: str, entity: dict[str, Any], *, run_rio: bool) -> None:
        """Ingest a single TripAdvisor destination entity."""
        location_id = str(entity.get("locationId", ""))
        name = str(entity.get("name", ""))
        lat = entity.get("lat")
        lng = entity.get("lng")

        # Build review signals (LGPD boundary — never pass author/text)
        review_count = int(entity.get("reviewCount", 0))
        rating = float(entity.get("rating", 0.0))
        most_recent_str = entity.get("mostRecentReviewDate")
        most_recent_dt: datetime | None = None
        if most_recent_str:
            try:
                # Parse date string (YYYY-MM-DD or ISO datetime)
                if "T" not in most_recent_str:
                    most_recent_str = most_recent_str + "T00:00:00"
                most_recent_dt = datetime.fromisoformat(most_recent_str).replace(
                    tzinfo=UTC
                )
            except (ValueError, AttributeError):
                pass

        review_signals = TripAdvisorReviewSignals(
            review_count=review_count,
            rating=rating,
            most_recent_review_at=most_recent_dt,
        )

        # Compute §7.6 criterion values
        corroboracao_value = corroboracao_from_reviews(review_count, rating)
        atualidade_value = atualidade_from_recency(most_recent_dt)
        completude_value = completude_from_fields(entity, cap=80)  # destino cap=80

        # Validate through Pydantic payload model (LGPD enforcement at parse time)
        payload_model = TripAdvisorDestinoPayload(
            name=name,
            uf=uf,
            location_id=location_id,
            lat=lat,
            lng=lng,
            review_signals=review_signals,
            origem_value=TA_DESTINO_ORIGEM_VALUE,
            completude_value=completude_value,
            corroboracao_value=corroboracao_value,
            atualidade_value=atualidade_value,
            validacao_humana_value=0.0,
        )

        # Resolve IBGE municipality
        ibge_match = resolve_municipio(
            name,
            uf,
            self._ibge_records,
            candidate_lat=lat,
            candidate_lng=lng,
        )
        if ibge_match is None:
            quarantine_poison(
                session=self._session,
                nascente_id=None,
                task_name="brave.ta.destinos.ibge_unmatched",
                error=f"ibge_unmatched: could not resolve '{name}' in UF={uf}",
                payload={"uf": uf, "locationId": location_id, "name": name},
            )
            # TERMINAL Log-tab event alongside the poison chip. Idempotent
            # (record_event_once) so a persistently-unmatched card does not re-emit its
            # terminal event every sweep. LGPD: name is public-geo; reason + locationId
            # only — never PII/review text.
            record_event_once(
                session=self._session,
                source="tripadvisor",
                source_ref=f"tripadvisor:destination:{location_id}",
                stage="quarantined",
                status="fail",
                entity_type="destination",
                uf=uf,
                message=f"ibge_unmatched: '{name}'",
                data={
                    "reason": "ibge_unmatched",
                    "name": name,
                    "locationId": location_id,
                },
            )
            return

        source_ref = f"tripadvisor:destination:{location_id}"

        payload: dict[str, Any] = {
            "name": payload_model.name,
            "uf": payload_model.uf,
            "locationId": location_id,
            "lat": payload_model.lat,
            "lng": payload_model.lng,
            "municipio_id": ibge_match.ibge_code,
            # §7.6 criterion *_value fields — routing.py reads these at normalize step
            "origem_value": payload_model.origem_value,
            "completude_value": payload_model.completude_value,
            "corroboracao_value": payload_model.corroboracao_value,
            "atualidade_value": payload_model.atualidade_value,
            "validacao_humana_value": payload_model.validacao_humana_value,
            # Review signals (LGPD-aggregate only)
            "review_count": review_count,
            "rating": rating,
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
            entity_type="destination",
            uf=uf,
            payload=payload,
        )

        if run_rio:
            process_nascente_record(
                session=self._session,
                nascente=nascente,
                config=self._config,
            )
