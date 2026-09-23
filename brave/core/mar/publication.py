"""Mar publication: the only way a Rio record enters Mar and reaches norteia-api.

- Promoção (``promote``): synchronous, human-validated entry into Mar (steward or the
  owner over WhatsApp) — re-score, promote, audit, commit, then enqueue the publish.
- Publicação (``publish``): the async send of the active Mar row to norteia-api — the
  body of the ``brave.publish_mar`` task. Never promotes.
- Pendente: an active Mar row with ``pushed_at IS NULL`` — the outbox. The 15-min beat
  and the Painel "Reenviar" drain it via ``republish_pending``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from brave.clients.base import NorteiaApiClientProtocol
from brave.config.settings import ScoreConfig
from brave.core.dlq.service import validate_and_promote_rio
from brave.core.mar import sync
from brave.core.mar.service import build_push_payload
from brave.core.models import MarRecord, RioRecord
from brave.observability.audit import write_audit
from brave.shared.exceptions import ApiDown

logger = structlog.get_logger(__name__)

Enqueue = Callable[[str], Any]


@dataclass(frozen=True)
class Promotion:
    routing: str
    mar_id: uuid.UUID | None
    held_reason: str | None
    push_queued: bool


@dataclass(frozen=True)
class Published:
    status: str  # "pushed" | "unchanged" | "not_sent" | "api_down" | "not_in_mar"


def promote(
    session: Session,
    rio: RioRecord,
    *,
    actor: str,
    enqueue: Enqueue,
    action: str = "dlq_validated",
    held_action: str | None = None,
    extra: dict[str, Any] | None = None,
    config: ScoreConfig | None = None,
) -> Promotion:
    """Human-validate ``rio`` into Mar (or record why it was held), audit, commit, enqueue.

    ``action`` is audited when the record lands in Mar, ``held_action`` (default:
    ``action``) when it is held. A broker failure never escapes: the row stays pending
    and the outbox re-dispatches it.
    """
    before = {"routing": rio.routing, "score": float(rio.score or 0)}
    mar = validate_and_promote_rio(session, rio, config)
    in_mar = mar is not None
    after: dict[str, Any] = {"routing": rio.routing, "score": float(rio.score or 0), **(extra or {})}
    if not in_mar:
        after["reason"] = rio.dlq_reason
    write_audit(
        session,
        action=action if in_mar else (held_action or action),
        entity_type=rio.entity_type,
        record_id=rio.id,
        before_state=before,
        after_state=after,
        actor=actor,
    )
    rio_id, routing = str(rio.id), rio.routing
    mar_id = mar.id if in_mar else None
    held_reason = None if in_mar else rio.dlq_reason
    # WR-01: commit BEFORE enqueue — the worker must see the Mar row it is told to publish.
    session.commit()

    push_queued = False
    if in_mar:
        try:
            enqueue(rio_id)
            push_queued = True
        except Exception as exc:  # noqa: BLE001 — broker down: the outbox recovers it
            logger.error("publish_enqueue_failed", rio_id=rio_id, error=str(exc))
    return Promotion(routing=routing, mar_id=mar_id, held_reason=held_reason, push_queued=push_queued)


def _digest(payload: dict[str, Any]) -> str:
    """sha256 of the push payload — must match the stored push_hash formula exactly."""
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def publish(session: Session, rio_id: uuid.UUID, api: NorteiaApiClientProtocol) -> Published:
    """Send the active Mar row of ``rio_id`` to norteia-api; stamp it only on a real 2xx."""
    rio = session.get(RioRecord, rio_id)
    if rio is None or rio.routing != "mar":
        return Published("not_in_mar")
    mar = session.scalars(
        select(MarRecord)
        .where(MarRecord.rio_id == rio_id, MarRecord.superseded_by_id.is_(None))
        .order_by(MarRecord.published_at.desc())
    ).first()
    if mar is None:
        return Published("not_in_mar")

    payload = build_push_payload(mar, rio)
    digest = _digest(payload)
    if mar.push_hash == digest:
        return Published("unchanged")
    try:
        sent = asyncio.run(api.push(mar.entity_type, payload))
    except ApiDown:
        return Published("api_down")
    if not sent:
        # Null adapter sent nothing: stamping would make the first real push a silent no-op.
        return Published("not_sent")
    mar.push_hash = digest
    mar.pushed_at = datetime.now(UTC)
    session.commit()
    return Published("pushed")


def republish_pending(session: Session, enqueue: Enqueue) -> int:
    """Enqueue the publish of every pending Mar row (capped per call); broker errors propagate."""
    rows = sync.pending_push_rows(session)
    for rio_id, _entity_type in rows:
        enqueue(str(rio_id))
    return len(rows)
