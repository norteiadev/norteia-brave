"""Atrativos operator API — audited stage transitions for atrativos (UI-PAINEL-2).

Endpoint:
  PATCH /api/v1/atrativos/{rio_id}/transition — generic, audited stage transition

Security (T-17.1-03-03):
  - require_steward_or_bearer (mutation)
  - `_ATRATIVO_ALLOWED_EDGES` is the server-side edge allow-list (the atrativo twin
    of the client mapDrop): any (expected, to) pair absent from it returns 409 and
    NEVER mutates; every ("mar", *) edge is absent so a live Mar record is never moved.

Borderline promotion (the ("rio","mar") edge) flows through the standard reliability gate
(validate_and_promote_rio): validacao_humana=100 → re-score → promote only when the
score crosses threshold_mar. The former mar_ready promote-override bypass was removed —
under the binary Mar/DLQ threshold a validated attraction reaches Mar directly when it
qualifies.
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from brave.api.deps import get_db, require_editing_unlocked, require_steward_or_bearer
from brave.api.routers.cms import _ROUTING_TO_COLUMN, TransitionBody
from brave.core.dlq.service import validate_and_promote_rio
from brave.core.models import RioRecord
from brave.observability.audit import write_audit

router = APIRouter()
logger = structlog.get_logger(__name__)


# Server-side ATRATIVO edge allow-list — the atrativo twin of the client mapDrop
# (keyed by (expected_column, to_column) → handler tag). Mirrors the destino
# _ALLOWED_EDGES posture: any (expected, to) pair absent from it returns 409 and
# NEVER mutates; every ("mar", *) edge is absent (T-17.1-03-03). The into-whatsapp
# edge is sub_state-guarded and delegates to the audited gate approve — it never
# duplicates the outreach dispatch.
_ATRATIVO_ALLOWED_EDGES: dict[tuple[str, str], str] = {
    ("rio", "dlq"): "send_to_review",      # force send-to-review
    ("dlq", "rio"): "reprocess",           # reopen / reprocess (NEW backward edge)
    ("rio", "mar"): "promote",             # borderline promotion via the reliability gate
    ("rio", "descarte"): "descarte",       # descarte
    # Rio/DLQ column merge: an atrativo at routing=in_progress OR dlq now rests in
    # the single "Rio · revisão" column (keyed "dlq"), so its promote/descarte edges
    # must be reachable from "dlq" too (mirrors the destino _ALLOWED_EDGES, which
    # already carries dlq→mar / dlq→descarte). The "rio"-keyed twins above stay for
    # back-compat but are now unreachable (no card reports column "rio").
    ("dlq", "mar"): "promote",
    ("dlq", "descarte"): "descarte",
    ("whatsapp", "whatsapp"): "gate_approve",  # delegate to atrativos_gate approve
}


@router.patch(
    "/api/v1/atrativos/{rio_id}/transition",
    status_code=200,
    dependencies=[Depends(require_steward_or_bearer), Depends(require_editing_unlocked)],
)
def transition_atrativo(
    rio_id: uuid.UUID,
    body: TransitionBody,
    db: Session = Depends(get_db),
) -> dict:
    """Generic, audited stage transition for an atrativo (UI-PAINEL-2).

    Server-side `_ATRATIVO_ALLOWED_EDGES` is the security boundary (the atrativo
    twin of the client mapDrop): only allow-listed (expected, to) edges mutate;
    everything else — notably every mar → * edge — returns 409 and never touches
    the record. The into-whatsapp edge is sub_state-guarded (only from
    `aguardando_consulta_whatsapp`) and DELEGATES to the audited gate approve —
    it never duplicates the outreach dispatch. All other edges reuse an existing
    helper (no new pipeline logic), write one audit row, then commit.
    """
    rio = db.get(RioRecord, rio_id)
    if rio is None:
        raise HTTPException(status_code=404, detail="RioRecord not found")

    edge = _ATRATIVO_ALLOWED_EDGES.get((body.expected, body.to))
    if edge is None:
        # Unmapped (incl. every mar → *) — reject BEFORE any mutation.
        raise HTTPException(status_code=409, detail="transição não suportada")

    if edge == "gate_approve":
        # Into-whatsapp is sub_state-guarded (NOT routing-guarded). Only valid from
        # aguardando_consulta_whatsapp; delegate to the audited gate approve, which
        # owns the outreach dispatch + audit + commit. Never duplicate it here.
        if rio.sub_state != "aguardando_consulta_whatsapp":
            raise HTTPException(
                status_code=409,
                detail=(
                    "into-whatsapp válido apenas a partir de "
                    "sub_state 'aguardando_consulta_whatsapp'"
                ),
            )
        from brave.api.routers.atrativos_gate import approve_whatsapp_gate

        return approve_whatsapp_gate(rio_id, db)

    # Optimistic concurrency: the caller's `expected` column must still be current.
    current_column = _ROUTING_TO_COLUMN.get(rio.routing, rio.routing)
    if current_column != body.expected:
        raise HTTPException(
            status_code=409,
            detail=f"coluna atual é '{current_column}', esperado '{body.expected}'",
        )

    before_state = {"column": body.expected, "routing": rio.routing, "sub_state": rio.sub_state}

    if edge == "promote":
        # Borderline promotion flows through the standard reliability gate: inject
        # validacao_humana=100 → re-score → promote only if score ≥ threshold_mar.
        # Returns None when the record does not cross the gate; it then stays put.
        validate_and_promote_rio(db, rio)
        db.refresh(rio)
        if rio.routing != "mar":
            # D1: the reliability/liveness gate held the record in the Rio (e.g.
            # dlq_reason=no_recent_reviews). Audit the REAL outcome (not a phantom
            # transition_mar) and 409 with the reason so the UI shows "segurado no
            # Rio" instead of a ghost Mar move that reverts on the next poll.
            write_audit(
                session=db,
                action="promote_held",
                entity_type=rio.entity_type,
                record_id=rio.id,
                before_state=before_state,
                after_state={
                    "column": _ROUTING_TO_COLUMN.get(rio.routing, rio.routing),
                    "routing": rio.routing,
                },
                actor="steward",
            )
            db.commit()
            raise HTTPException(
                status_code=409,
                detail=(
                    "registro não cruzou o gate de confiabilidade e permanece no "
                    f"Rio (motivo: {rio.dlq_reason or 'reprovado'})"
                ),
            )
    elif edge == "descarte":
        rio.routing = "descarte"
        rio.dlq_reason = "steward_rejected"
        rio.sub_state = None
    elif edge == "send_to_review":
        rio.routing = "dlq"
        rio.dlq_reason = "steward_sent_to_review"
        rio.sub_state = None
    elif edge == "reprocess":
        # Reopen: reset → re-score (reliability). Reuses the routing helper, no new machinery.
        from brave.config.runtime import load_effective_config
        from brave.core.rio.routing import reprocess_record

        reprocess_record(db, rio.id, load_effective_config(db).score)
        db.refresh(rio)

    write_audit(
        session=db,
        action=f"transition_{body.to}",
        entity_type=rio.entity_type,
        record_id=rio.id,
        before_state=before_state,
        after_state={"column": body.to, "routing": rio.routing},
        actor="steward",
    )
    db.commit()

    # D2: a steward promote that reached Mar must publish to norteia-api, same
    # contract as the pipeline. Commit already happened above (WR-01); the push
    # task early-returns if routing != "mar" and is idempotent by source_ref.
    if edge == "promote" and rio.routing == "mar":
        try:
            from brave.tasks.pipeline import push_attraction_task

            push_attraction_task.delay(str(rio_id))
        except Exception as exc:  # noqa: BLE001 — narrow via run_real_externals below
            from brave.config.settings import AppConfig

            if AppConfig().run_real_externals:
                logger.error("atrativo_push_dispatch_failed", rio_id=str(rio_id), error=str(exc))
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "Atrativo promovido ao Mar, mas a publicação para a "
                        "norteia-api falhou (broker indisponível). Refaça a "
                        "promoção quando o broker voltar para redisparar o push."
                    ),
                ) from exc

    return {"status": "ok", "to": body.to}
