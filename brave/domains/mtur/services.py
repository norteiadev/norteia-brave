"""MturSeedIngest — DEST-01.

Reads the bundled Mtur seed CSV via MturClientProtocol and ingests categorized
municipalities into Nascente (source='mtur', origem=100).

Implements LaneProtocol.produce(uf) from brave/lanes/base.py.

D-04: Producers populate *_value fields in the Nascente payload; the Rio
normalizer reads them from process_nascente_record — no core changes required.

D-06: The origem=40 firewall is a scoring consequence, not a code branch.
MturSeedIngest sets origem_value=100 (authoritative government source).

D-10: IBGE municipality code is carried in payload["municipio_id"] and in
payload["canonical"]["ibge_code"]; norteia-api resolves IBGE→municipality_id.

D-18: This module imports only from brave.core, brave.clients, and brave.config.
It does NOT import from any other brave.lanes module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy.orm import Session

from brave.config.settings import ScoreConfig
from brave.core import engine as collection_engine
from brave.core.nascente.service import store_raw
from brave.core.quarantine import quarantine_poison
from brave.core.rio.routing import process_nascente_record

if TYPE_CHECKING:
    from brave.clients.base import MturClientProtocol

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MTUR_ATUALIDADE_DEFAULT = 70.0
# 2024 dataset edition; re-calibrate when new Mtur edition is published.
# The Mtur dataset is versioned by year (municipios_mtur_YYYY.csv).
# A 2024 or 2025 dataset is considered recent; atualidade=70 reflects
# "recently published" in the reliability scoring scheme.


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _completude_from_fields(mun: dict[str, Any]) -> float:
    """Compute completude_value from field coverage of an Mtur municipality row.

    Checks four critical fields: ibge_code, name, categoria, uf.
    Returns a score proportional to how many are non-empty.

    Args:
        mun: Municipality dict with keys ibge_code, name, categoria, uf.

    Returns:
        100.0 if all four fields are non-empty.
        75.0 if exactly three are non-empty.
        50.0 if exactly two are non-empty.
        25.0 if exactly one is non-empty.
        0.0 if none are non-empty.
    """
    fields = [
        mun.get("ibge_code", ""),
        mun.get("name", ""),
        mun.get("categoria", ""),
        mun.get("uf", ""),
    ]
    count = sum(1 for f in fields if f)
    return float(count * 25)


# ---------------------------------------------------------------------------
# Lane implementation
# ---------------------------------------------------------------------------


class MturSeedIngest:
    """Mtur seed ingestion lane — ingests categorized municipalities into Nascente.

    Implements LaneProtocol.produce(uf) — see brave/lanes/base.py.

    For each municipality returned by the Mtur client, writes a NascenteRecord
    via store_raw and immediately runs the Rio pipeline via process_nascente_record.

    Per-record SAVEPOINT isolation (pfr #2A): each municipality is wrapped in a
    session.begin_nested() savepoint. On exception the savepoint is rolled back
    and the record is quarantined — the outer transaction retains all good records
    and quarantine rows so a single bad record never discards a whole UF sweep.

    Args:
        mtur_client: MturClientProtocol implementation (real or fake).
        session:     SQLAlchemy synchronous Session.
        config:      ScoreConfig with reliability weights and thresholds.
    """

    def __init__(
        self,
        mtur_client: MturClientProtocol,
        session: Session,
        config: ScoreConfig,
    ) -> None:
        self._client = mtur_client
        self._session = session
        self._config = config

    async def produce(
        self, uf: str, *, run_rio: bool = True, redis: Any | None = None
    ) -> None:
        """Ingest one full UF sweep for the Mtur lane.

        Fetches all Mtur-categorized municipalities for the given state,
        writes each to Nascente with source='mtur' and origem_value=100, then —
        when run_rio is True — immediately triggers the Rio pipeline.

        run_rio is the depth gate (plan 10-02): the orchestrator owns the depth
        read and passes run_rio down; this lane NEVER reads Redis depth itself.
          - run_rio=True (default): store_raw then process_nascente_record, as today.
          - run_rio=False: the `Apenas nascente` (free) path — Nascente + the reliability
            *_value score inputs are still written via store_raw, but
            process_nascente_record (Rio) is skipped entirely. No RioRecord is
            created, zero LLM/Places, zero external cost.

        Idempotent: store_raw deduplicates by (source, source_ref, content_hash).
        Re-running produce() for the same UF with the same data is a no-op.

        Per-record SAVEPOINT isolation (pfr #2A): each record is wrapped in a
        savepoint so a single failure quarantines only that record without
        discarding the 168+ good records already written in the same UF sweep.

        Args:
            uf: Two-letter Brazilian state code (e.g. "BA", "RJ", "SP").
            run_rio: When False, skip process_nascente_record (Nascente-only,
                free). Defaults to True (full Nascente → Rio).
        """
        municipalities = await self._client.fetch_municipalities(uf)

        for mun in municipalities:
            # Mid-sweep pause/off/stop: stop inserting the rest of this UF's destinos
            # when the operator paused/turned off the motor. Skipped when redis is None
            # (direct unit-test calls) — behavior then unchanged.
            if redis is not None and collection_engine.should_halt_producer(redis):
                logger.info("mtur_producer_halt", uf=uf)
                break
            ibge_code: str = mun.get("ibge_code", "")
            name: str = mun.get("name", "")
            categoria: str = mun.get("categoria", "")

            source_ref = f"mtur:{uf}:{ibge_code}"

            payload: dict[str, Any] = {
                "name": name,
                "municipio_id": ibge_code,  # 7-digit IBGE code (D-10)
                "uf": uf,
                "categoria": categoria,
                # reliability criterion *_value fields — routing.py reads these at normalize step
                "origem_value": 100.0,
                "completude_value": _completude_from_fields(mun),
                "corroboracao_value": 0.0,
                "atualidade_value": MTUR_ATUALIDADE_DEFAULT,
                "validacao_humana_value": 0.0,
                # Canonical sub-dict matching the Pact contract shape (D-10, RISK-01 fix)
                # ibge_code enables norteia-api to resolve IBGE→municipality_id without
                # relying on name-based disambiguation (multiple "Santa Cruz" in Brazil).
                "canonical": {
                    "name": name,
                    "uf": uf,
                    "municipio": name,
                    "ibge_code": ibge_code,
                },
            }

            # pfr #2A: per-record SAVEPOINT so a single bad record does not discard
            # the entire UF sweep. sp.rollback() releases only the nested savepoint;
            # the outer transaction remains valid and accumulates good records.
            # quarantine_poison is called AFTER sp.rollback() — it writes to the
            # outer transaction which is still open and will be committed by sweep_uf.
            sp = self._session.begin_nested()
            try:
                nascente = store_raw(
                    session=self._session,
                    source="mtur",
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
                sp.commit()
            except Exception as exc:
                sp.rollback()
                quarantine_poison(
                    session=self._session,
                    nascente_id=None,
                    task_name="brave.sweep_uf",
                    error=str(exc),
                    payload={"source_ref": source_ref, "uf": uf},
                )


# MturSeedIngest satisfies LaneProtocol (brave/lanes/base.py)
# Checked via structural typing — mypy/pyright will raise if produce(uf) is missing.
# _lane: LaneProtocol = MturSeedIngest(...)  # noqa: F841 (type-annotation comment only)
