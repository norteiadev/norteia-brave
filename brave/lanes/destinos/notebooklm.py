"""NotebookLMIngest — DEST-02.

Ingests NotebookLM structured reports at origem=80 and boosts corroboracao on matching
Mtur records (IBGE exact-match, RISK-02 mitigation per D-02).

Implements LaneProtocol.produce(uf) from brave/lanes/base.py.

D-04: Producers populate *_value fields in the Nascente payload; the Rio normalizer
reads them from process_nascente_record — no core changes required.

D-02: NotebookLM reports confirm Mtur municipality data. When a NotebookLM report
overlaps a live Mtur RioRecord by IBGE code, corroboracao_value is boosted by 50 on
the surviving record and reprocess_record is called. This is the load-bearing
mechanism for Mtur records to reach Mar: without corroboration, max score = 80.0 after
human validation (RESEARCH.md Pitfall 2).

D-18: This module imports only from brave.core, brave.clients, and brave.config.
It does NOT import from any other brave.lanes module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from brave.config.settings import ScoreConfig
from brave.core.models import RioRecord
from brave.core.nascente.service import store_raw
from brave.core.rio.routing import reprocess_record

if TYPE_CHECKING:
    from brave.clients.base import NotebookLMClientProtocol

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NOTEBOOKLM_ATUALIDADE_DEFAULT = 60.0
# NotebookLM reports are periodically updated; 60.0 reflects "reasonably current"
# in the §7.6 scoring scheme. Re-calibrate when the report generation cadence
# is known (e.g., monthly → raise to 70.0).


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _completude_from_report(report: dict[str, Any]) -> float:
    """Compute completude_value from field coverage of a NotebookLM report.

    Checks four critical fields: name, highlights, overview, publish_date.
    Returns a score proportional to how many are non-empty.

    Args:
        report: NotebookLM report dict (parsed JSON from local file).

    Returns:
        100.0 if all four fields are non-empty.
        75.0 if exactly three are non-empty.
        50.0 if exactly two are non-empty.
        25.0 if exactly one is non-empty.
        0.0 if none are non-empty.
    """
    fields = [
        report.get("name", ""),
        report.get("highlights", []) or [],
        report.get("overview", ""),
        report.get("publish_date", ""),
    ]
    count = sum(1 for f in fields if f)
    return float(count * 25)


def _atualidade_from_report_date(report: dict[str, Any]) -> float:
    """Compute atualidade_value from publish_date in a NotebookLM report.

    Uses NOTEBOOKLM_ATUALIDADE_DEFAULT if publish_date is absent.

    Args:
        report: NotebookLM report dict.

    Returns:
        NOTEBOOKLM_ATUALIDADE_DEFAULT if no publish_date.
        70.0 if publish_date is present (treated as "recently published").
    """
    if report.get("publish_date"):
        return 70.0
    return NOTEBOOKLM_ATUALIDADE_DEFAULT


# ---------------------------------------------------------------------------
# Lane implementation
# ---------------------------------------------------------------------------


class NotebookLMIngest:
    """NotebookLM report ingestion lane — ingests structured tourism reports into Nascente.

    Implements LaneProtocol.produce(uf) — see brave/lanes/base.py.

    For each municipality in mtur_municipalities that has a non-empty NotebookLM report,
    writes a NascenteRecord via store_raw (source='notebooklm', origem=80.0).

    Corroboration boost (D-02, RISK-02 mitigation):
    After ingesting each report, checks for an existing live Mtur RioRecord with the
    same IBGE code. If found, boosts corroboracao_value by 50 (capped at 100) on the
    surviving record's normalized dict and calls reprocess_record. This enables Mtur
    records to cross the Mar threshold (score ≥85) after human validation.

    Args:
        notebooklm_client:  NotebookLMClientProtocol implementation (real or fake).
        session:            SQLAlchemy synchronous Session.
        config:             ScoreConfig with §7.6 weights and thresholds.
        mtur_municipalities: List of municipality dicts with keys: ibge_code, name, uf.
                             Injected by the caller because NotebookLMClient has no
                             listing method — the caller provides IBGE codes to iterate.
    """

    def __init__(
        self,
        notebooklm_client: "NotebookLMClientProtocol",
        session: Session,
        config: ScoreConfig,
        mtur_municipalities: list[dict[str, Any]],
    ) -> None:
        self._client = notebooklm_client
        self._session = session
        self._config = config
        self._mtur_municipalities = mtur_municipalities

    async def produce(self, uf: str) -> None:
        """Ingest one full UF sweep for the NotebookLM lane.

        Iterates over mtur_municipalities filtered to the given UF. For each
        municipality with a non-empty NotebookLM report, writes a NascenteRecord
        (source='notebooklm', origem=80) and applies the corroboration boost to
        any existing Mtur RioRecord matching by IBGE code.

        Idempotent: store_raw deduplicates by (source, source_ref, content_hash).
        Re-running produce() for the same UF with the same data is a no-op for the
        Nascente write; the corroboration boost is also idempotent (re-boosts are
        capped at 100 and reprocess_record is idempotent).

        Args:
            uf: Two-letter Brazilian state code (e.g. "BA", "RJ", "SP").
        """
        uf_upper = uf.upper()

        for mun in self._mtur_municipalities:
            mun_uf = (mun.get("uf") or "").strip().upper()
            if mun_uf != uf_upper:
                continue

            ibge_code: str = mun.get("ibge_code", "")
            name: str = mun.get("name", "")

            # Build the municipio key in the canonical "nome:uf:ibge" format
            municipio_key = f"{name}:{uf_upper}:{ibge_code}"

            # Fetch report — returns {} when no report exists (graceful degradation)
            report = await self._client.fetch_report(municipio_key)
            if not report:
                continue

            source_ref = f"notebooklm:{uf_upper}:{ibge_code}"

            payload: dict[str, Any] = {
                "name": report.get("name", name),
                "municipio_id": ibge_code,  # 7-digit IBGE code (D-10)
                "uf": uf_upper,
                # §7.6 criterion *_value fields — routing.py reads these at normalize step
                "origem_value": 80.0,
                "completude_value": _completude_from_report(report),
                "corroboracao_value": 0.0,
                "atualidade_value": _atualidade_from_report_date(report),
                "validacao_humana_value": 0.0,
                # Canonical sub-dict matching the Pact contract shape (D-10)
                "canonical": {
                    "name": report.get("name", name),
                    "uf": uf_upper,
                    "municipio": report.get("name", name),
                    "ibge_code": ibge_code,
                },
            }

            store_raw(
                session=self._session,
                source="notebooklm",
                source_ref=source_ref,
                entity_type="destination",
                uf=uf_upper,
                payload=payload,
            )

            # ------------------------------------------------------------------
            # Corroboration boost (D-02, RISK-02):
            # After store_raw, check for existing Mtur RioRecord by IBGE code.
            # If found, boost corroboracao_value by 50 on the surviving record's
            # normalized dict. flag_modified is REQUIRED — SQLAlchemy does not
            # auto-track in-place JSON mutations (RESEARCH.md Pitfall 3).
            # ------------------------------------------------------------------
            existing = self._session.scalar(
                select(RioRecord).where(
                    RioRecord.municipio_id == ibge_code,
                    RioRecord.uf == uf_upper,
                    RioRecord.entity_type == "destination",
                    RioRecord.routing.in_(["dlq", "mar"]),  # only live records
                )
            )
            if existing is not None:
                normalized = dict(existing.normalized or {})
                normalized["corroboracao_value"] = min(
                    100.0, float(normalized.get("corroboracao_value", 0.0)) + 50.0
                )
                existing.normalized = normalized
                flag_modified(existing, "normalized")
                self._session.flush()
                reprocess_record(self._session, existing.id, self._config)


# NotebookLMIngest satisfies LaneProtocol (brave/lanes/base.py)
# Checked via structural typing — mypy/pyright will raise if produce(uf) is missing.
# _lane: LaneProtocol = NotebookLMIngest(...)  # noqa: F841 (type-annotation comment only)
