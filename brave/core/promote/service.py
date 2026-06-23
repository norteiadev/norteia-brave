"""Promote-override service — steward bypass of the ≥85 score gate (TA-05).

promote_override(session, rio, reason, config) -> MarRecord:
  Exclusively for TripAdvisor attractions that have already been scored and
  flagged as mar_ready=True by route_by_score. The steward reviews the record
  on the mar-ready queue and triggers this override, which bypasses the ≥85
  threshold (the signal quality is sufficient but the overall weighted score
  may be slightly below the gate due to the origin_value penalty for TA).

  The key differences from validate_and_promote_rio (dlq/service.py):
    1. PromoteNotAllowed guard at start — only mar_ready=True records can proceed.
    2. After reprocess_record, routing is FORCED to "mar" regardless of score
       (this is the override — it bypasses the §7.6 gate).
    3. promotion_reason is written to the MarRecord's provenance for audit trail.

Security boundary (T-11-03-01):
  mar_ready is set exclusively by route_by_score (the score engine). Callers
  cannot set it via the API — the atrativos router reads it but never sets it.
  A non-mar_ready RioRecord always raises PromoteNotAllowed regardless of the
  caller's identity.
"""

from __future__ import annotations

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from brave.config.settings import ScoreConfig
from brave.core.mar.service import promote_to_mar
from brave.core.models import MarRecord, RioRecord
from brave.core.rio.routing import reprocess_record


class PromoteNotAllowed(Exception):
    """Raised when a RioRecord is not mar_ready and cannot be promote-overridden.

    Maps to HTTP 409 Conflict in the atrativos router.
    """


def promote_override(
    session: Session,
    rio: RioRecord,
    reason: str,
    config: ScoreConfig | None = None,
) -> MarRecord:
    """Steward override — promote a mar_ready TripAdvisor attraction to Mar.

    Bypasses the ≥85 score gate; the mar_ready flag (set by route_by_score)
    is the authoritative gate. Only attractions with atualidade ≥ 70 AND
    corroboracao ≥ 60 can ever have mar_ready=True.

    Steps (mirrors dlq/service.py validate_and_promote_rio):
      1. Guard: raise PromoteNotAllowed if not rio.mar_ready.
      2. Inject validacao_humana=100 (flag_modified pattern — Pitfall 3).
      3. Reprocess (reprocess_record, not process_nascente_record — Pitfall 4).
      4. Force rio.routing="mar" (the override bypasses the ≥85 threshold).
      5. Call promote_to_mar → returns MarRecord.
      6. Append promotion_reason to MarRecord.provenance for audit trail.

    Args:
        session: SQLAlchemy synchronous Session.
        rio:     RioRecord to promote. Must have mar_ready=True.
        reason:  Audit reason string (e.g. "steward_override_review_validated").
        config:  ScoreConfig; defaults to ScoreConfig() if None.

    Returns:
        The created or updated MarRecord.

    Raises:
        PromoteNotAllowed: If rio.mar_ready is False.
    """
    if not rio.mar_ready:
        raise PromoteNotAllowed(
            f"RioRecord {rio.id} is not mar_ready — promote-override requires "
            "mar_ready=True (set by route_by_score for qualifying TA attractions)"
        )

    config = config or ScoreConfig()

    # Step 2: Inject validacao_humana=100 — CRITICAL: reassign + flag_modified
    # (SQLAlchemy JSON mutation tracking, Pitfall 3 from dlq/service.py).
    normalized = dict(rio.normalized or {})
    normalized["validacao_humana_value"] = 100.0
    rio.normalized = normalized
    flag_modified(rio, "normalized")
    session.flush()

    # Step 3: Re-score — reprocess_record (not process_nascente_record, Pitfall 4).
    # This gives the record the best possible score before forcing routing.
    reprocess_record(session, rio.id, config)
    session.refresh(rio)

    # Step 4: Force routing="mar" — the override bypasses the ≥85 gate.
    # The score may still be below 85 after reprocess (TA origin_value penalty);
    # mar_ready=True already attests that signal quality is sufficient.
    rio.routing = "mar"

    # Step 5: Promote to Mar layer.
    mar = promote_to_mar(session, rio)

    # Step 6: Append promotion_reason to MarRecord.provenance for audit trail.
    # promote_to_mar builds its own provenance from score_breakdown/version;
    # we append promotion_reason so steward decisions are traceable in Mar.
    mar.provenance = {**mar.provenance, "promotion_reason": reason}
    # The dict reassignment mutates the JSON column — flush it explicitly.
    flag_modified(mar, "provenance")

    return mar
