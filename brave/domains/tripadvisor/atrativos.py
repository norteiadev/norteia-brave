"""TripAdvisorAtrativosIngest — TA-02.

This lane scrapes TripAdvisor attraction listings via the GraphQL hybrid
client (TripAdvisorClientProtocol) and ingests them into Nascente with
source='tripadvisor', entity_type='attraction', origem=80.0.

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
import re
import uuid
from contextlib import nullcontext
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from brave.clients.null_places import NullPlacesClient
from brave.config.settings import ScoreConfig, TripAdvisorConfig
from brave.core import engine as collection_engine
from brave.core.models import (
    NascenteRecord,
    PoisonQuarantine,
    RioRecord,
    whatsapp_candidate_from_phone,
)
from brave.core.nascente.service import store_raw
from brave.core.quarantine import quarantine_poison
from brave.core.rio.persist import persist_normalized
from brave.core.rio.routing import STICKY_DLQ_REASONS, process_nascente_record, route_by_score
from brave.domains.tripadvisor import sweep_progress
from brave.domains.tripadvisor.ibge import (
    IbgeMunicipio,
    haversine_km,
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
from brave.shared.exceptions import (
    CostGuardError,
    ProviderBalanceError,
    SourceRateLimitError,
    SourceRecordError,
    SourceSchemaError,
    SourceSessionError,
)
from brave.shared.ibge_distritos import resolve_distrito_in_uf

if TYPE_CHECKING:
    from brave.clients.base import GeocoderClientProtocol, TripAdvisorClientProtocol
    from brave.shared.ibge_distritos import IbgeDistrito


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TA_ATRATIVO_ORIGEM_VALUE = 80.0
# origem=80 (>Places 60, <gov 100): published TA listings are trusted above a Places-only
# find. Still no TA atrativo reaches Mar on its own — the max without the human-validation
# criterion is 79 — so promotion stays the gate. Was 65 (CONTEXT.md TA-04) until 10/2026.

# Version of the município-resolution chain, stamped on every ibge_unmatched quarantine.
# A card quarantined under THIS version is skipped by later sweeps (its per-card TA calls
# would only fail again and eat a max_per_uf slot); one quarantined under an older chain
# is retried once. Bump it whenever the chain learns a new way to place a card.
# The stamp is written only when the WHOLE chain really ran (TA page with a live config,
# distritos loaded, Nominatim answered, Places consulted with a live client); otherwise the
# quarantine carries resolver_version=0 and the next sweep retries the card.
# 2 = + distrito by TA cityName + Google Places Text Search.
# 3 = + Places município consensus when no result matches the name.
# 4 = v3 stamps were also written when Places was off/failing — release them once.
# 5 = Places município from locality / the result's coords + 2/3-majority consensus.
# 6 = TA's own attraction page (addressLocality/addressRegion + its point) first.
IBGE_RESOLVER_VERSION = 6
_IBGE_UNMATCHED_TASK = "brave.ta.atrativos.ibge_unmatched"

# Run-level failures: not a property of the card, so they must escape the per-card
# try/quarantine and stop the sweep (the task layer pauses / needs_bootstrap / backs off).
# Swallowing them poisoned every remaining card of the UF while the task "succeeded".
_PAUSE_ERRORS: tuple[type[BaseException], ...] = (
    ProviderBalanceError,
    CostGuardError,
    SourceSessionError,
    SourceRateLimitError,
    SourceSchemaError,
    SoftTimeLimitExceeded,
)

# Marker of the TA-side backfill (contract C1): set once TA page + review recency ran.
TA_BACKFILLED_KEY = "ta_backfilled_at"

# Inside the right UF, a record whose own coords lie within this radius of TA's city seat
# keeps its município: TA's cityName is its parent geo (the town next door), the coords'
# nearest seat is the finer label. Farther than this, the coords are a homonym's point.
_SAME_UF_KEEP_KM = 50.0

# Written for the place where the bulk put a card; stale once TA page moves it (_apply_ta_geo).
_MOVED_STALE_KEYS = (
    "place_id_cache", "google_place_id", "distrito_name", "distrito_code",
    "distrito_municipio_ibge", "weekday_text", "phone", "website", "address", "price_level",
    "signal", "google_fetched_at", "most_recent_review_at", "descricao_editorial",
    "descricao_rascunho", "descricao_gate", "descricao_groundedness",
)

logger = structlog.get_logger(__name__)
# Logging discipline (T-15-06-02 / T-12-04-01): the bulk methods log ONLY
# offset / counts / locationId / error-class — never name, address, cookies,
# user_agent, session_id, or proxy values.


def _source_ref(entity: dict[str, Any]) -> str:
    """Nascente source_ref / Rio canonical_key of a TA card (same key _ingest_one writes)."""
    return f"tripadvisor:attraction:{entity.get('locationId', '')}"


# TA listing categories that are not a place to visit: tours sold by agencies (forbidden in
# Norteia, whatever their reviews), transport, services and commercial leisure. The pt-BR
# labels of the listing card (primaryInfo.text); "Passeios ..."/"Excursões ..." are prefixes.
_NOT_ATRATIVO_PREFIXES = ("Passeios", "Excursões", "Viagens de um dia", "Táxis", "Aulas", "Locação")
_NOT_ATRATIVO = frozenset({
    "Lojas de departamento", "Serviços de ônibus", "Sistemas de transporte público",
    "Spas", "Academias de ginástica", "Complexos esportivos", "Surfe e windsurfe",
    "Stand-up paddle", "Caiaque e canoagem", "Caminhadas", "Cinemas",
    "Centros de entretenimento e jogos", "Pistas de boliche", "Jogos de fuga", "Baladas",
    "Bares e clubes", "Cervejarias", "Clubes com piscina e praia",
    "Trilhas para quadriciclos e off-road", "Shopping centers", "Clínicas e quadras esportivas",
    # Operator-approved 10/2026: stadiums, casinos, lounges and services are not a place to
    # visit (Arena Castelão left Mar on 20/10). Not "Centros de convenção": TA files cultural
    # centers there (Centro Cultural Oscar Niemeyer) — Google's types decide those.
    "Arenas e estádios", "Cassinos", "Lounges de aeroporto",
    "Stand de tiro", "Caça ao tesouro", "Universidades e escolas",
})
# Bars and clubs of any kind ("Bares de karaokê", "Clubes de comédia"); "Clubes com piscina
# e praia" is listed above.
_NOT_PLACE_PREFIXES = ("Bares ", "Clubes de ")
# An agency TA files under one place-like activity too ("Sergio Tour: Passeios Privativos":
# "Passeios urbanos • Mergulho com e sem snorkel") slips the all-parts rule above. Its name
# gives it away, but only next to an activity/service category: "Centro de Turismo" and
# "Praça de Apoio Ao Turismo" are "Pontos de interesse" and stay.
# Also operators and studios: "Discovery Tur", "Maracajaú Diver", "Yoga na Rota".
_AGENCY_NAME = re.compile(
    r"\b(tour|tours|tur|turismo|receptivo|transfer|transfers|ag[eê]ncia|viagens|expedi[cç][oõ]es"
    r"|dive|divers?|diving|yoga|pilates)\b",
    re.IGNORECASE,
)
_PASSEIO_NAME = re.compile(r"\bpasseios?\b", re.IGNORECASE)
_ACTIVITY_PREFIXES = _NOT_ATRATIVO_PREFIXES + (
    "Ônibus turístico", "Mergulho", "Esportes aquáticos", "Canionismo", "Rafting",
    "Parapente", "Centros turísticos", "Serviços de ônibus", "Ioga e pilates",
)


def _is_agency(entity: dict[str, Any], parts: list[str]) -> bool:
    """An agency/operator, forbidden whatever its reviews.

    Either its name gives it away next to an activity/service category, or every category
    is an activity with at least one sold service: "Jr Mossoró" ("Mergulho ... • Excursões
    de off-road ...") has no agency word, but no place either. A bare "Mergulho com e sem
    snorkel" (Piscinas Naturais de Ipioca) sells nothing and stays.
    """
    name = str(entity.get("name") or "")
    activity = any(p.startswith(_ACTIVITY_PREFIXES) for p in parts)
    tour_category = any(p.startswith(("Passeios", "Excursões")) for p in parts)
    only_activities = bool(parts) and all(p.startswith(_ACTIVITY_PREFIXES) for p in parts)
    sells_service = any(p.startswith(_NOT_ATRATIVO_PREFIXES) for p in parts)
    return (only_activities and sells_service) or (activity and (
        bool(_AGENCY_NAME.search(name)) or (tour_category and bool(_PASSEIO_NAME.search(name)))
    ))


def _is_not_atrativo(entity: dict[str, Any]) -> bool:
    """Every TA category of the card is a non-place one — drop it before any Places/geo/Rio cost.

    A card mixing in one place category stays (Feira do Turista: "Lojas ... • Passeios
    urbanos"). Shopping malls never stay. Shops ("Lojas ...") count as places only when
    reviewed: TA lists any local store (Magazine Luiza, a motorcycle dealer) there with no
    reviews, while the Mercado Thales Ferraz has hundreds. A mall TA files under "Lojas"
    (Riomar) is caught by Google's primaryType after Places. No category → it stays.
    """
    # ponytail: pt-BR label match; switch to TA category ids if the session locale changes.
    reviewed = int(entity.get("review_count") or 0) > 0
    parts = [p.strip() for p in str(entity.get("category") or "").split("•") if p.strip()]
    if _is_agency(entity, parts):
        return True
    return bool(parts) and all(
        p in _NOT_ATRATIVO
        or p.startswith(_NOT_ATRATIVO_PREFIXES)
        or p.startswith(_NOT_PLACE_PREFIXES)
        or (p.startswith("Lojas ") and not reviewed)
        for p in parts
    )


def _parse_dt(raw: Any) -> datetime | None:
    """ISO-8601 → aware datetime, or None."""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _places_live(agent: Any) -> bool:
    """False when the agent's Places client is the Null one (flag off): no real search."""
    return not isinstance(getattr(agent, "_places_client", None), NullPlacesClient)


def _merge_recency(after: dict[str, Any], recency: dict[str, Any]) -> None:
    """Fold a fetch_recent_review result into ``after`` — max-merge, never regress.

    The newest review date wins (TA's or one Google already wrote); atualidade and
    review_count only go up.
    """
    old_dt = _parse_dt(after.get("most_recent_review_at"))
    newest = max(
        (d for d in (old_dt, recency.get("most_recent_review_at")) if d is not None),
        default=None,
    )
    if newest is not None:
        if newest != old_dt:
            after["most_recent_review_at"] = newest.isoformat()
        after["atualidade_value"] = max(
            float(after.get("atualidade_value") or 0.0), atualidade_from_recency(newest)
        )
    if recency.get("review_count") is not None:
        after["review_count"] = max(int(after.get("review_count") or 0), int(recency["review_count"]))


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
        distritos: list[IbgeDistrito] | None = None,
    ) -> None:
        self._client = ta_client
        self._distritos = distritos or []
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
        skipped = 0
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
            synced = self._already_synced(cards, run_rio=run_rio)
            # Per-UF enrich path: a card already in Rio but never TA-backfilled (a bulk record
            # with a provisional UF/município, or one ingested before the marker existed) is
            # COMPLETED via backfill_record instead of skipped. The bulk path
            # (produce_paginated) keeps skipping every Rio row, so its page resume is intact.
            backfill = self._backfill_candidates(cards) if (enrich_reviews and run_rio) else {}
            for entity in cards:
                ref = _source_ref(entity)
                # Already in Brave: the TA signals were consumed by the score on the first
                # sync — skip before any per-card TA call. Not counted toward max_per_uf,
                # so the cap measures NEW atrativos.
                if (ref in synced and ref not in backfill) or _is_not_atrativo(entity):
                    skipped += 1
                    continue
                # Snapshot the destino cache keys BEFORE the attempt so that a rollback
                # (per-atrativo on the enrich path, the card's savepoint otherwise) can evict
                # any destino cached by _ensure_destino during THIS (now discarded) iteration —
                # otherwise the map would point the next same-município atrativo at a parent
                # rio whose rows no longer exist.
                keys_before = set(self._destino_rio_map)
                try:
                    if ref in backfill:
                        await self.backfill_record(backfill[ref])
                    else:
                        # Non-enrich path commits per page: a SAVEPOINT per card reverts only
                        # this card on a flush error (e.g. IntegrityError), leaving the
                        # transaction healthy for the poison row and the page's other cards.
                        with nullcontext() if enrich_reviews else self._session.begin_nested():
                            _rio_id = await self._ingest_one(
                                uf, entity, run_rio=run_rio, enrich_reviews=enrich_reviews
                            )
                        if _rio_id is not None:
                            ingested_ids.append(_rio_id)
                    if enrich_reviews:
                        # Per-atrativo durability: persist this atrativo's rows now.
                        self._session.commit()
                except _PAUSE_ERRORS:
                    # Run-level (balance/budget wall, dead or throttled session, retired qid,
                    # soft time limit) — escape produce() entirely; quarantining it would
                    # poison every remaining card. pipeline.py rolls back + pauses.
                    raise
                except Exception as exc:  # noqa: BLE001
                    if enrich_reviews:
                        # Isolate the failed atrativo: discard its partial writes and
                        # clear any aborted-transaction state before writing poison.
                        self._session.rollback()
                    # Cache coherence: evict destinos cached this iteration but now rolled
                    # back, so the next same-município atrativo re-creates them.
                    for k in set(self._destino_rio_map) - keys_before:
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

        logger.info("ta_atrativos_producer_done", uf=uf, processed=processed, skipped=skipped)
        return ingested_ids

    def _already_synced(
        self, cards: list[dict[str, Any]], *, run_rio: bool, bulk: bool = False
    ) -> set[str]:
        """source_refs of this page's cards already in Brave — one query per page.

        With run_rio the record must be in Rio (canonical_key == Nascente source_ref);
        a Nascente-only record (earlier nascente-depth sweep) is re-ingested so it
        reaches Rio. A card quarantined as ibge_unmatched under the CURRENT resolver chain
        is skipped too (it would fail the same way); older quarantines are retried. The
        bulk also skips cards its own (shorter, Places-less) chain already missed.
        """
        ids = [str(c["locationId"]) for c in cards if c.get("locationId")]
        if not ids:
            return set()
        refs = [f"tripadvisor:attraction:{i}" for i in ids]
        col = RioRecord.canonical_key if run_rio else NascenteRecord.source_ref
        synced = set(self._session.scalars(select(col).where(col.in_(refs))))
        loc = PoisonQuarantine.payload["locationId"].as_string()
        final = PoisonQuarantine.payload["resolver_version"].as_integer() == IBGE_RESOLVER_VERSION
        if bulk:
            final = or_(
                final,
                PoisonQuarantine.payload["bulk_resolver_version"].as_integer()
                == IBGE_RESOLVER_VERSION,
            )
        unmatched = self._session.scalars(
            select(loc).where(
                PoisonQuarantine.task_name == _IBGE_UNMATCHED_TASK, loc.in_(ids), final
            )
        )
        return synced | {f"tripadvisor:attraction:{i}" for i in unmatched}

    def _backfill_candidates(self, cards: list[dict[str, Any]]) -> dict[str, RioRecord]:
        """This page's Rio rows still missing the TA backfill marker (never descarte)."""
        refs = [_source_ref(c) for c in cards if c.get("locationId")]
        if not refs:
            return {}
        rows = self._session.scalars(
            select(RioRecord).where(
                RioRecord.canonical_key.in_(refs),
                RioRecord.normalized[TA_BACKFILLED_KEY].as_string().is_(None),
                RioRecord.routing != "descarte",
            )
        )
        return {r.canonical_key: r for r in rows}

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

    def _parent_destino(self, ibge_match: IbgeMunicipio) -> tuple[uuid.UUID, str]:
        """(rio_id, source_ref) of the município's destino — one ensure_destino per sweep.

        Cached in ``destino_rio_map`` by IBGE code, so a 10k-card bulk costs one
        ensure_destino per distinct município, not one per card. Callers that roll back
        evict the keys added during the failed attempt (see produce / produce_paginated).
        """
        entry = self._destino_rio_map.get(ibge_match.ibge_code)
        if entry is None:
            entry = self._ensure_destino(ibge_match)
            self._destino_rio_map[ibge_match.ibge_code] = entry
        return entry

    # -----------------------------------------------------------------------
    # Backfill of a record ALREADY in Rio (contract C2) — the bulk lane's completion step.
    # -----------------------------------------------------------------------

    async def backfill_record(self, rio: RioRecord) -> str:
        """Complete one TA atrativo already in Rio: TA page + review recency, then Places.

        Idempotent, keyed on the C1 markers: the TA part runs only while
        ``normalized['ta_backfilled_at']`` is missing, the Places part only while
        ``google_enriched`` is not True and a places agent is configured. The caller owns
        the transaction (commit per record). Nothing is swallowed: run-level errors
        (session / rate limit / retired qid, ProviderBalanceError, CostGuardError,
        SoftTimeLimitExceeded) reach the caller so it can pause, and the marker of the step
        that failed is NOT set, so the record is retried. The Places step runs in a
        SAVEPOINT: when it raises, only its writes are undone and the TA side (marker
        included) is left for the caller to commit.

        Returns "+"-joined steps run ("ta", "places"; "ta_page_pendente" when TA's page
        failed — no marker, no Places), "noop", "municipio_nao_confirmado"
        when TA puts the card in another UF and no resolver could place it there (routed to
        the DLQ; Places is skipped — a search in the stored, wrong UF could only match a
        homonym), or "municipio_pendente" for the same case when a resolver was off or
        failing (DLQ too, but no marker: the next backfill retries it).
        """
        steps: list[str] = []
        if not (rio.normalized or {}).get(TA_BACKFILLED_KEY):
            steps.append(await self._backfill_ta(rio))
        normalized = rio.normalized or {}
        ta_uf = normalized.get("ta_uf")
        if ta_uf and rio.uf and ta_uf != rio.uf:
            if not normalized.get(TA_BACKFILLED_KEY):
                return "municipio_pendente"
            return "municipio_nao_confirmado"
        # A card whose TA page failed this pass is still placed by name: Places would
        # measure from a possibly wrong seat and its place_id_cache would pin it
        # (_apply_ta_geo keep-rule). The next backfill redoes the page, then Places.
        if (
            self._places_agent is not None
            and normalized.get("google_enriched") is not True
            and "ta_page_pendente" not in steps
        ):
            with self._session.begin_nested():
                await self._places_agent.run(rio)
            steps.append("places")
        return "+".join(steps) or "noop"

    async def _throttle(self) -> None:
        if self._ta_config is not None and self._ta_config.page_throttle_seconds > 0:
            await asyncio.sleep(self._ta_config.page_throttle_seconds)

    def _ibge(self, code: str | None) -> IbgeMunicipio | None:
        return next((r for r in self._ibge_records if r.ibge_code == code), None) if code else None

    def _resolve_ta_city(
        self, city: str | None, uf: str
    ) -> tuple[IbgeMunicipio | None, IbgeDistrito | None]:
        """TA cityName → município of ``uf`` (by name), else the parent of a distrito."""
        if not city:
            return None, None
        match = resolve_municipio(city, uf, self._ibge_records)
        if match is None and self._distritos:
            distrito = resolve_distrito_in_uf(city, uf, self._distritos)
            if distrito is not None:
                match = self._ibge(distrito.ibge_code)
                if match is not None:
                    return match, distrito
        return match, None

    async def _ta_address(self, location_id: int) -> dict[str, Any] | None:
        """TA's own page for one card, paced like every TA call. None = the page had no
        address block (SourceRecordError): retried later, never a verdict."""
        await self._throttle()
        try:
            return await self._client.fetch_attraction_address(location_id)
        except SourceRecordError:
            return None

    def _ta_coords(
        self, page: dict[str, Any], match: IbgeMunicipio | None
    ) -> tuple[float, float] | None:
        """TA's point, unless it contradicts TA's own locality: farther than _SAME_UF_KEEP_KM
        from the município seat AND within 50 km of ANOTHER município's seat (any UF).
        Balneário Água Gelada (RO 10/2026): locality Porto Velho, point 2 km from Pimenta
        Bueno's seat, 437 km away. A far point with no other seat near is kept (Fortaleza do
        Abunã, a Porto Velho distrito 210 km out). ponytail: nearest seat, not a polygon;
        Ariquemes' park (37 km off, 1 km from Alto Paraíso) passes — the hybrid's 2 km pick
        rejects it."""
        lat, lng = page.get("lat"), page.get("lng")
        if lat is None or lng is None:
            return None
        if match is None or haversine_km(lat, lng, match.lat, match.lng) <= _SAME_UF_KEEP_KM:
            return lat, lng
        nearest = resolve_municipio_national(lat, lng, self._ibge_records)  # default 50 km
        return None if nearest is not None and nearest.ibge_code != match.ibge_code else (lat, lng)

    def _place_ta_page(
        self, page: dict[str, Any], uf: str
    ) -> tuple[str, IbgeMunicipio | None, IbgeDistrito | None, tuple[float, float] | None]:
        """Where TA's own page puts a card: UF from addressRegion (the sweep's when absent or
        unknown), município from addressLocality or the distrito it names, and TA's point
        unless it contradicts them."""
        page_uf = state_name_to_uf(page.get("state_name") or "") or uf
        match, distrito = self._resolve_ta_city(page.get("city_name"), page_uf)
        return page_uf, match, distrito, self._ta_coords(page, match)

    async def _backfill_ta(self, rio: RioRecord) -> str:
        """TA page → UF/município/parent destino; TA reviews → recency. Sets the marker.

        The marker is withheld for "municipio_pendente" (C1: a verdict reached while a
        resolver was off or failing is not "conclusively empty"), "ta_page_pendente" (the
        page failed) and a failed reviews call.
        """
        before = dict(rio.normalized or {})
        after = dict(before)
        uf_before = rio.uf
        status = "ta"
        reviews_failed = False
        page: dict[str, Any] | None = {}
        loc_id_raw = (rio.canonical_key or "").removeprefix("tripadvisor:attraction:")
        if loc_id_raw.isdigit():  # else nothing to fetch, ever — mark it done
            loc_id = int(loc_id_raw)
            page = await self._ta_address(loc_id)
            await self._throttle()
            try:
                recency = await self._client.fetch_recent_review(loc_id)
            except SourceRecordError:
                recency, reviews_failed = None, True  # no marker: retried later
            if page:
                status = await self._apply_ta_geo(rio, loc_id_raw, page, after)
            elif page is None:
                status = "ta_page_pendente"
            if recency is not None:
                _merge_recency(after, recency)
        unplaced = status in ("municipio_nao_confirmado", "municipio_pendente")
        if not unplaced and not after.get("parent_rio_id"):
            # A moved record lost its parent (as did bulk records ingested before the bulk
            # linked one): link it to the destino of its (confirmed or kept) município — also
            # when TA's page gave no usable address.
            current = self._ibge(rio.municipio_id)
            if current is not None:
                entry = self._parent_destino(current)
                after["parent_rio_id"], after["parent_source_ref"] = str(entry[0]), entry[1]
                if after.get("parent_mar_id"):
                    after["parent_mar_id"] = None
        if status != "municipio_pendente" and not reviews_failed and page is not None:
            after[TA_BACKFILLED_KEY] = datetime.now(UTC).isoformat()
        persist_normalized(self._session, rio, before, after)
        if (
            not unplaced
            and rio.dlq_reason == "municipio_nao_confirmado"
            and before.get("ta_uf") not in (None, uf_before)
        ):
            # Our own earlier verdict (TA said another UF): the card is placed now.
            rio.dlq_reason = None
        # Re-score without erasing deliberate verdicts (C5): this step only moved geo/recency.
        route_by_score(self._session, rio, self._config)
        # Hold the unplaced card — but never over a descarte (C5: it may have been discarded
        # since the batch was selected) nor over another deliberate verdict (steward review,
        # community report, closed_temporarily): ta_uf != uf already keeps Places off it.
        if (
            unplaced
            and rio.routing != "descarte"
            and (
                rio.dlq_reason not in STICKY_DLQ_REASONS
                or rio.dlq_reason == "municipio_nao_confirmado"
            )
        ):
            rio.routing = "dlq"
            rio.dlq_reason = "municipio_nao_confirmado"
        logger.info("ta_backfill_record", rio_id=str(rio.id), uf=rio.uf, status=status)
        return status

    async def _apply_ta_geo(
        self, rio: RioRecord, location_id: str, geo: dict[str, Any], after: dict[str, Any]
    ) -> str:
        """Re-derive the record's UF/município from TA's own page; write the deltas to ``after``.

        The bulk placed the card by a name-only national geocode (a homonym in another state
        is possible); TA's addressRegion is authoritative for the UF. Its addressLocality can
        be TA's PARENT geo (often the touristic town next door), so inside the right UF it
        only overrides a município the record has no evidence of its own for: no place_id,
        and no coords — or coords farther than _SAME_UF_KEEP_KM from TA's city seat (a
        homonym's point). TA's point and contact are a floor under Google's.
        """
        ta_uf = state_name_to_uf(geo.get("state_name") or "")
        if ta_uf is None:
            return "ta"
        after["ta_uf"] = ta_uf
        current = self._ibge(rio.municipio_id)
        current_ok = current is not None and current.uf == ta_uf
        match, distrito = self._resolve_ta_city(geo.get("city_name"), ta_uf)
        ta_coords = self._ta_coords(geo, match)
        if current_ok and match is not None and match.ibge_code != current.ibge_code:
            lat, lon = after.get("lat"), after.get("lon")
            near = (
                lat is not None
                and lon is not None
                and haversine_km(float(lat), float(lon), match.lat, match.lng) <= _SAME_UF_KEEP_KM
            )
            if after.get("place_id_cache") or near:
                match, distrito = current, None
        coords: tuple[float, float] | None = None
        place_id: str | None = None
        if match is None and not current_ok:
            # The stored município is in the wrong UF: pay for the in-UF fallbacks.
            name = after.get("name") or ""
            match, coords, place_id, nominatim_ok, places_ok = await self._locate_in_uf(
                location_id, name, ta_uf
            )
            if match is None:
                city = geo.get("city_name")
                conclusive = not name or (
                    nominatim_ok and places_ok and (not city or bool(self._distritos))
                )
                logger.warning("ta_backfill_municipio_unconfirmed", rio_id=str(rio.id), uf=ta_uf,
                               conclusive=conclusive)
                return "municipio_nao_confirmado" if conclusive else "municipio_pendente"
        if match is None:
            match = current
        assert match is not None
        moved = current is None or match.ibge_code != current.ibge_code
        if moved:
            rio.uf = match.uf
            rio.municipio_id = match.ibge_code
            after["municipio"] = match.nome
            after["municipio_id"] = match.ibge_code
            # Everything written for where the bulk put the card belongs to ANOTHER place:
            # coords, the Google match and all it wrote (hours/contact/status/its review
            # date), and a description anchored on the old município. persist_normalized
            # merges, so None (not pop). The paid Places step is re-opened; the TA recency
            # fetched in this same pass is merged back right after.
            after["lat"], after["lon"] = ta_coords or coords or (None, None)
            for key in _MOVED_STALE_KEYS:
                if after.get(key) is not None:
                    after[key] = None
            if after.get("atualidade_value"):
                after["atualidade_value"] = 0.0
            if after.get("descricao_attempts"):
                after["descricao_attempts"] = 0
            if after.get("google_enriched"):
                after["google_enriched"] = False
            if after.get("parent_rio_id"):
                after["parent_rio_id"] = None
                after["parent_source_ref"] = None
        if place_id:
            after["place_id_cache"] = place_id
        # TA's own point beats a name geocode (the bulk's geocode_national, Nominatim); only a
        # Google match keeps Google's point.
        if ta_coords and (
            not after.get("place_id_cache") or after.get("lat") is None or after.get("lon") is None
        ):
            after["lat"], after["lon"] = ta_coords
        for key in ("address", "phone", "website"):  # TA floor: never over Google's
            if geo.get(key) and not after.get(key):
                after[key] = geo[key]
        if moved:
            # completude counted the dropped coords/address and the description (which
            # raised it to 90): recount it like the ingest did, from the card + what is left.
            card = getattr(self._session.get(NascenteRecord, rio.nascente_id), "payload", None)
            after["completude_value"] = completude_from_fields(
                {
                    **(card if isinstance(card, dict) else {}),
                    "name": after.get("name"),
                    "uf": rio.uf,
                    "location_id": location_id,
                    "lat": after["lat"],
                    "lng": after["lon"],
                    "address": after.get("address"),
                    "description": None,
                },
                cap=100,
            )
        if distrito is not None:
            after.update(
                distrito_name=distrito.nome,
                distrito_code=distrito.distrito_code,
                distrito_municipio_ibge=distrito.ibge_code,
            )
        return "ta"

    async def _locate_in_uf(
        self, location_id: str, name: str, uf: str
    ) -> tuple[IbgeMunicipio | None, tuple[float, float] | None, str | None, bool, bool]:
        """Nominatim (free) then one Places Text Search, both inside ``uf``.

        Returns (município, coords, place_id, nominatim_ok, places_ok). The two flags say
        whether each resolver really answered: Nominatim without a swallowed failure (an
        unconfigured geocoder is not part of the chain), Places with a live client, no
        failure and no ``locate_failed``. A miss is a verdict only when both answered. A
        provider defect degrades to None; run-level errors propagate.
        """
        nominatim_ok = True
        if self._geocoder is not None and name:
            try:
                geo = await self._geocoder.geocode(location_id, name, uf)
            except _PAUSE_ERRORS:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("nominatim_geocode_failed", location_id=location_id, uf=uf,
                               error=type(exc).__name__)
                geo = None
                nominatim_ok = False
            if geo is not None:
                match = resolve_municipio(
                    geo.get("municipio_name") or name,
                    uf,
                    self._ibge_records,
                    candidate_lat=geo["lat"],
                    candidate_lng=geo["lon"],
                    max_distance_km=50.0,
                )
                if match is not None:
                    return match, (geo["lat"], geo["lon"]), None, True, True
        places_ok = False
        if self._places_agent is not None and name:
            try:
                place = await self._places_agent.locate(name, uf)
                places_ok = _places_live(self._places_agent) and not getattr(
                    self._places_agent, "locate_failed", False
                )
            except _PAUSE_ERRORS:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("places_locate_failed", uf=uf, error=type(exc).__name__)
                place = None
            if place is not None:
                match = self._ibge(place.get("municipio_ibge"))
                if match is not None:
                    ploc = place.get("location") or {}
                    coords = (
                        (ploc["lat"], ploc["lng"])
                        if ploc.get("lat") is not None and ploc.get("lng") is not None
                        else None
                    )
                    return match, coords, place.get("place_id") or None, nominatim_ok, True
        return None, None, None, nominatim_ok, places_ok

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
        # True once the review call answered (a date, or conclusively none): the per-UF
        # record is then TA-complete and gets the backfill marker (C1) below.
        recency_fetched = False

        if enrich_reviews:
            try:
                loc_id_int = int(location_id) if location_id else None
            except (ValueError, TypeError):
                loc_id_int = None
            if loc_id_int is not None:
                # Mirror the geo-enrichment throttle: pace review calls when configured.
                if self._ta_config is not None and self._ta_config.page_throttle_seconds > 0:
                    await asyncio.sleep(self._ta_config.page_throttle_seconds)
                try:
                    recency = await self._client.fetch_recent_review(loc_id_int)
                    recency_fetched = True
                except SourceRecordError:
                    recency = None  # recency_fetched stays False: no marker, backfill retries
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

        address = phone = website = None
        page_failed = False
        distrito = None
        ibge_match = None
        # TA's own attraction page first: the name-only steps below placed homonyms (RO
        # 10/2026: 9/89 wrong municípios, Places matches 439-602 km away measured from the
        # wrong seat).
        if self._ta_config is not None and location_id.isdigit():
            page = await self._ta_address(int(location_id))
            page_failed = page is None
            if page:
                # The record follows TA's UF (a cross-border card listed under this sweep).
                uf, ibge_match, distrito, coords = self._place_ta_page(page, uf)
                if coords:
                    lat, lng = coords
                address, phone, website = (page.get(k) for k in ("address", "phone", "website"))
                if ibge_match is not None:
                    timeline.append(
                        {
                            "source": "tripadvisor",
                            "source_ref": source_ref,
                            "stage": "geo_enriched",
                            "status": "ok",
                            "message": name if distrito is None
                            else f"{name} → distrito {distrito.nome}",
                            "entity_type": "attraction",
                            "uf": uf,
                            "data": {
                                "via": "ta_page",
                                **({"distrito_code": distrito.distrito_code} if distrito else {}),
                            },
                        }
                    )

        # Name resolution (card lat/lng, or TA's point; None for coordless cards).
        if ibge_match is None:
            ibge_match = resolve_municipio(
                name,
                uf,
                self._ibge_records,
                candidate_lat=lat,
                candidate_lng=lng,
            )

        # TA-15: geo-enrichment via Nominatim — only when every step above missed.
        # Promote geocoded lat/lng into working variables so completude and the
        # persisted payload carry real coordinates (WR-01 fix: previously these
        # were discarded and lat/lng remained None even after a successful geocode).
        # TA's own point beats a geocode by name: it is kept when present.
        nominatim_failed = False
        if ibge_match is None and self._geocoder is not None:
            try:
                geo = await self._geocoder.geocode(location_id, name, uf)
            except _PAUSE_ERRORS:
                raise
            except Exception as exc:  # noqa: BLE001 — a Nominatim 429/ban must not skip D
                logger.warning(
                    "nominatim_geocode_failed",
                    location_id=location_id,
                    uf=uf,
                    error=type(exc).__name__,
                )
                geo = None
                nominatim_failed = True
            if geo is not None:
                if lat is None:
                    lat, lng = geo["lat"], geo["lon"]
                ibge_match = resolve_municipio(
                    geo.get("municipio_name") or name,
                    uf,
                    self._ibge_records,
                    candidate_lat=geo["lat"],
                    candidate_lng=geo["lon"],
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

        # Fallback D (one Places Text Search, only for cards every free path missed —
        # TA mostly returns no cityName, just a region geoId). The place_id rides into the
        # payload as place_id_cache, so the inline enrichment reuses it instead of paying
        # for a second search.
        located_place_id: str | None = None
        # True only when Places REALLY answered: a live client (not the flag-off Null one)
        # and no failure. A None from a Null/failed search is not "Places could not place it".
        places_consulted = False
        if ibge_match is None and self._places_agent is not None:
            try:
                place = await self._places_agent.locate(name, uf, category)
                places_consulted = _places_live(self._places_agent) and not getattr(
                    self._places_agent, "locate_failed", False
                )
            except _PAUSE_ERRORS:
                raise
            except Exception as exc:  # noqa: BLE001 — a Places defect never breaks the ingest
                logger.warning("places_locate_failed", uf=uf, error=type(exc).__name__)
                place = None
            if place is not None:
                ibge_match = next(
                    (r for r in self._ibge_records if r.ibge_code == place["municipio_ibge"]),
                    None,
                )
                if ibge_match is not None:
                    located_place_id = place.get("place_id") or None
                    ploc = place.get("location") or {}
                    if lat is None and ploc.get("lat") is not None:
                        lat, lng = ploc["lat"], ploc.get("lng")
                    timeline.append(
                        {
                            "source": "tripadvisor",
                            "source_ref": source_ref,
                            "stage": "geo_enriched",
                            "status": "ok",
                            "message": f"{name} → {place.get('name') or ''}".strip(" →"),
                            "entity_type": "attraction",
                            "uf": uf,
                            "data": {
                                "via": "places_consensus"
                                if place.get("consensus")
                                else "places_text_search",
                                "place_id": located_place_id,
                            },
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
            "address": address or entity.get("address"),
        }
        completude_value = completude_from_fields(completude_entity, cap=100)  # atrativo cap=100

        if ibge_match is None:
            # Stamp the current chain only when every fallback really ran; a card that missed
            # because a fallback was off or failing gets 0 and is retried by the next sweep.
            chain_complete = (
                self._ta_config is not None
                and bool(self._distritos)
                and not nominatim_failed
                and places_consulted
                and not page_failed
            )
            quarantine_poison(
                session=self._session,
                nascente_id=None,
                task_name=_IBGE_UNMATCHED_TASK,
                error=f"ibge_unmatched: could not resolve '{name}' in UF={uf}",
                payload={
                    "uf": uf,
                    "locationId": location_id,
                    "name": name,
                    "resolver_version": IBGE_RESOLVER_VERSION if chain_complete else 0,
                },
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

        # TA's page may place it in ANOTHER UF (addressRegion of a cross-border card): the
        # record's UF follows its município, never the sweep's.
        uf = ibge_match.uf

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
        parent_rio_id, parent_source_ref = self._parent_destino(ibge_match)

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
            # Review signals (LGPD-aggregate only). most_recent_review_at is the SAME
            # date atualidade_value was computed from above; persisting it is what lets
            # the promote_to_mar 90-day recency backstop verify the freshness the score
            # already claims. Aggregate date only — never review text/title/author.
            # None when enrich_reviews is off.
            "review_count": review_count,
            "rating": rating,
            "most_recent_review_at": (
                most_recent_dt.isoformat() if most_recent_dt is not None else None
            ),
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

        # TA's attraction page: its contact floor (Places overwrites it when it has one).
        payload.update({k: v for k, v in (("address", address), ("phone", phone),
                                          ("website", website)) if v})
        # Where TA's page / fallback D placed the card: the distrito it named, and the Places id
        # the inline enrichment reuses (routing copies place_id_cache + canonical distrito_*).
        if distrito is not None:
            payload["canonical"].update(
                distrito_name=distrito.nome,
                distrito_code=distrito.distrito_code,
                distrito_municipio_ibge=distrito.ibge_code,
            )
        if located_place_id:
            payload["place_id_cache"] = located_place_id

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
            # The per-UF card is TA-complete (UF from TA's own UF listing, recency answered):
            # mark it so the backfill (C1/C2) does not redo its TA calls. Only on THIS card's
            # row — a dedup may have returned another record.
            if (
                recency_fetched
                and not page_failed
                and rio.canonical_key == source_ref
                and not (rio.normalized or {}).get(TA_BACKFILLED_KEY)
            ):
                before = dict(rio.normalized or {})
                persist_normalized(
                    self._session,
                    rio,
                    before,
                    {**before, TA_BACKFILLED_KEY: datetime.now(UTC).isoformat()},
                )
            # Inline enrichment (description + distrito + hours/contact/price + liveness),
            # run here — like geo/review/município above — so the stored score reflects it
            # and a later task-kill can't strand it (the old post-produce dispatch could).
            # The agent flushes into this session; produce() owns the commit. Its own
            # re-score is the final word on routing. Never raises (keeps the TA floor).
            # A card TA's page could not place was placed by name: Places would measure from
            # a possibly wrong seat and its place_id_cache would pin it (_apply_ta_geo
            # keep-rule). The backfill redoes the page, then Places.
            if (
                self._places_agent is not None
                and (rio.canonical_key or "").startswith("tripadvisor:")
                and not page_failed
            ):
                await self._places_agent.run(rio)
            return str(rio.id)
        return None

    # -----------------------------------------------------------------------
    # Bulk national ingest path (Phase 15, TA-12) — DISTINCT from _ingest_one.
    # -----------------------------------------------------------------------

    async def _ingest_one_bulk(self, entity: dict[str, Any], *, run_rio: bool) -> bool:
        """Ingest a single attraction from the bulk national listing (no per-UF context).

        Resolves the operator-locked A1 blocker: the all-Brazil bulk lane
        (geoId 294280) has no per-UF context. ``uf`` + município are DERIVED from the
        attraction's national geocode (``geocode_national``) + nearest-IBGE-seat
        resolution (``resolve_municipio_national``). Once the município is known the
        parent destino is ensured exactly like ``_ingest_one`` does (``_parent_destino``,
        cached per município for the sweep) and linked as ``parent_rio_id`` /
        ``parent_source_ref = "ibge:{UF}:{ibge7}"``.

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
                geo_uf = geo.get("uf")
                if geo_uf:
                    # Stay inside the state Nominatim placed the point in (ISO3166-2): the
                    # nearest seat of ANY state put border attractions in the wrong UF.
                    ibge_match = resolve_municipio(
                        geo.get("municipio_name") or "",
                        geo_uf,
                        self._ibge_records,
                        candidate_lat=lat,
                        candidate_lng=lng,
                        max_distance_km=50.0,
                    ) or resolve_municipio_national(  # no município name → nearest in-UF seat
                        lat,
                        lng,
                        [r for r in self._ibge_records if r.uf == geo_uf],
                        max_distance_km=50.0,
                    )
                else:
                    ibge_match = resolve_municipio_national(
                        lat,
                        lng,
                        self._ibge_records,
                        max_distance_km=50.0,
                    )

        # Fallback (only for cards the name-only geocode could not place — no Rio row means
        # the backfill never sees them): TA's own geo names the UF (+ often the city or a
        # distrito), then Nominatim inside that UF. One TA call, paced like every TA call.
        distrito: IbgeDistrito | None = None
        chain_complete = False
        if ibge_match is None and self._ta_config is not None and location_id.isdigit():
            await self._throttle()
            ta_geo = await self._client.fetch_attraction_geo(int(location_id))
            ta_uf = state_name_to_uf((ta_geo or {}).get("state_name") or "")
            chain_complete = True  # TA answered; a miss below is final for the bulk chain
            if ta_uf is not None:
                city = (ta_geo or {}).get("city_name")
                ibge_match, distrito = self._resolve_ta_city(city, ta_uf)
                if ibge_match is None:
                    ibge_match, coords, _pid, nominatim_ok, _ = await self._locate_in_uf(
                        location_id, name, ta_uf
                    )
                    if coords is not None:
                        lat, lng = coords
                    chain_complete = nominatim_ok and (not city or bool(self._distritos))

        if ibge_match is None:
            # No geocode OR no IBGE seat within radius → quarantine (never dropped).
            # NO parent_destino_absent path exists in the bulk lane (gate dropped).
            quarantine_poison(
                session=self._session,
                nascente_id=None,
                task_name=_IBGE_UNMATCHED_TASK,
                error=f"ibge_unmatched: could not geo-resolve attraction locationId={location_id}",
                # LGPD: locationId only — never name/address. bulk_resolver_version stamps
                # a card the whole bulk chain (TA geo + Nominatim in its UF) missed, so later
                # bulk runs skip it; the per-UF sweep (which adds Places) still retries it.
                payload={
                    "locationId": location_id,
                    "bulk_resolver_version": IBGE_RESOLVER_VERSION if chain_complete else 0,
                },
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

        # Derive UF from the matched IBGE record — NOT from any input arg. PROVISIONAL: the
        # geocode was by name alone (a homonym in another state is possible), so the record
        # carries no ta_backfilled_at and backfill_record re-derives UF/município from TA page.
        uf = ibge_match.uf

        # Link the parent destino exactly like the per-UF path (F43-b): the CMS groups by it
        # and the push needs no fallback. Cached per município — DB only, no TA/Places call.
        # A later backfill that moves the record clears it and re-links the new município.
        parent_rio_id, parent_source_ref = self._parent_destino(ibge_match)

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
            # reliability criterion *_value fields
            "origem_value": payload_model.origem_value,
            "completude_value": payload_model.completude_value,
            "corroboracao_value": payload_model.corroboracao_value,
            "atualidade_value": payload_model.atualidade_value,
            "validacao_humana_value": payload_model.validacao_humana_value,
            # Parent destino linkage (same as the per-UF path).
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
        if distrito is not None:  # placed by TA's cityName naming a distrito (fallback)
            payload["canonical"].update(
                distrito_name=distrito.nome,
                distrito_code=distrito.distrito_code,
                distrito_municipio_ibge=distrito.ibge_code,
            )

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
        run_id: str | None = None,
    ) -> bool:
        """Drive the paginated GraphQL listing and bulk-ingest each page (Phase 15).

        Streams ``(offset, cards)`` tuples from ``fetch_attractions_paginated_gql``
        (Phase G — replaces the deprecated fragile HTML-SSR ``fetch_attractions_paginated``;
        same (offset, cards) yield shape), ingests every card via ``_ingest_one_bulk``
        (national path, parent destino cached per município — bulk stays
        enrich_reviews=False, no per-card review calls at 10k scale),
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
            run_id:     Engine run to count each completed page against (None = none).

        Returns:
            True when a pause/off/stop halted the run before the pages ran out (the task
            then marks the progress ``stopped``, not ``done``); False otherwise.
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
                return True
            ingested = 0
            errors = 0
            synced = self._already_synced(cards, run_rio=run_rio, bulk=True)
            for card in cards:
                if _source_ref(card) in synced or _is_not_atrativo(card):
                    continue  # already in Brave / not a place — skip the geocode/ingest
                keys_before = set(self._destino_rio_map)  # see produce(): cache coherence
                try:
                    # SAVEPOINT per card: a flush error reverts only this card, so the poison
                    # row and the page's earlier cards survive the per-page commit.
                    with self._session.begin_nested():
                        wrote_row = await self._ingest_one_bulk(card, run_rio=run_rio)
                except _PAUSE_ERRORS:
                    raise  # stops the run (task: progress stopped + pause), as in produce()
                except Exception as exc:  # noqa: BLE001
                    wrote_row = False
                    # The savepoint rolled back any destino ensured for this card: evict it so
                    # the next same-município card re-creates it instead of linking a ghost.
                    for k in set(self._destino_rio_map) - keys_before:
                        del self._destino_rio_map[k]
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
            if run_id is not None:  # the engine run this bulk sweep was claimed against
                collection_engine.progress(redis, run_id)
            logger.info(
                "ta_bulk_page_ingested",
                offset=offset,
                ingested=ingested,
                errors=errors,
            )
        return False
