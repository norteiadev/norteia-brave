"""DLQ service — validate_and_promote_rio helper (D-06, Phase 7).

Extracted from brave/api/routers/dlq.py validate_dlq_record inline logic.
Called by both the DLQ router and the loadtest harness.
"""

from __future__ import annotations

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from brave.config.runtime import load_effective_config
from brave.config.settings import ScoreConfig
from brave.core.models import MarRecord, RioRecord
from brave.core.mar.service import promote_to_mar
from brave.core.rio.routing import reprocess_record


def validate_and_promote_rio(
    session: Session,
    rio: RioRecord,
    config: ScoreConfig | None = None,
) -> MarRecord | None:
    """Set validacao_humana=100 → re-score → promote_to_mar if routing=='mar'.

    Extracted from dlq.py validate_dlq_record. Pitfall 3: reassign+flag_modified
    required for SQLAlchemy JSON mutation tracking. Pitfall 4: reprocess_record
    (NOT process_nascente_record) re-scores an existing record.

    Does NOT dispatch Celery tasks or write audit rows — caller is responsible.

    Every promotion path (DLQ validate/validate-batch, destino/atrativo promote,
    promote-bulk, WhatsApp finalize, CLI) funnels through here, so both guards live here:

    - Row lock first (F49): the record is re-read ``FOR UPDATE``. The FSM/enrichment tasks
      hold that lock across their Places/LLM I/O; copying ``normalized`` from an unlocked
      read and writing it back after their commit erased google_enriched / weekday_text /
      descricao_editorial (and paid for them again).
    - A ``descarte`` is never promoted (F50): a CLOSED_PERMANENTLY place or a steward
      rejection is a verdict, not a low score; validacao=100 must not re-score it into Mar.
      Returned as held (None), untouched — the board has no descarte→Mar edge either.

    Returns:
        MarRecord if promoted to Mar; None if routing != 'mar' after re-score (or refused).
    """
    config = config or load_effective_config(session).score

    # Pending writes of the caller (WhatsApp finalize's owner_horarios) must reach the row
    # before the refresh re-reads it, or the refresh would discard them.
    session.flush()
    session.refresh(rio, with_for_update=True)
    if rio.routing == "descarte":
        return None

    # Step 1: CRITICAL — reassign + flag_modified (Pitfall 3)
    normalized = dict(rio.normalized or {})
    normalized["validacao_humana_value"] = 100.0
    rio.normalized = normalized
    flag_modified(rio, "normalized")
    session.flush()

    # Step 2: re-score — reprocess_record NOT process_nascente_record (Pitfall 4). The human
    # decision is the one caller allowed to override the sticky DLQ verdicts (C5); descarte
    # was already refused above.
    reprocess_record(session, rio.id, config, override_verdicts=True)
    session.refresh(rio)

    # Step 3: promote only when routing == 'mar'
    if rio.routing == "mar":
        return promote_to_mar(session, rio)
    return None
