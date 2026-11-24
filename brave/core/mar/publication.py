"""Mar publication: the only way a Rio record enters Mar and reaches norteia-api.

- Promoção (``promote``): synchronous, human-validated entry into Mar (steward or the
  owner over WhatsApp) — re-score, promote, audit, commit, then enqueue the publish.
- Publicação (``publish``): the async send of the active Mar row to norteia-api — the
  body of the ``brave.publish_mar`` task. Never promotes.
- Pendente: an active Mar row norteia-api is behind on (``sync._needs_sync``: never
  accepted, updated after the last attempt, or its Rio left Mar and the API still shows
  it) — the outbox. The 15-min beat and the Painel "Reenviar" drain it via
  ``republish_pending``; ``publish`` is the single reconciliation point.
- Editorial destinations (``push_editorial_destinations``): destinations are not in Mar, so
  ``editorial_destinations`` is their own outbox (pending = ``pushed_at`` NULL, no error),
  behind ``editorial_push_enabled``. Same digest rule as ``publish``.
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

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from brave.clients.base import NorteiaApiClientProtocol
from brave.config.settings import ScoreConfig
from brave.core.dlq.service import validate_and_promote_rio
from brave.core.mar import sync
from brave.core.mar.service import build_push_payload, upsert_mar_snapshot
from brave.core.models import EditorialDestination, MarRecord, RioRecord
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
    # "pushed" | "unchanged" | "not_sent" | "api_down" | "not_in_mar" | "retracted" | "rejected"
    status: str


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


# 4xx that are NOT a verdict on the payload: auth/token (401/403), timeout (408), conflict
# (409), too early (425) and rate limit (429) heal on their own — keep retrying those.
_TRANSIENT_4XX = frozenset({401, 403, 408, 409, 425, 429})


def publish(session: Session, rio_id: uuid.UUID, api: NorteiaApiClientProtocol) -> Published:
    """Bring norteia-api in line with the active Mar row of ``rio_id``.

    Rio in Mar: re-derive the snapshot first (a description / Places / TA-backfill write
    after the promotion supersedes the row — F48), then push it; stamped only on a real
    2xx. Rio out of Mar with a visible copy on the API: push it again as
    ``visibility=hidden`` and stamp ``retracted_at`` (F47) — a row the API never accepted
    is just stamped. A permanent 4xx is recorded in ``push_error`` and NOT raised, so the
    Celery task does not retry a payload the API will refuse again (F46).
    """
    rio = session.get(RioRecord, rio_id)
    if rio is None:
        return Published("not_in_mar")
    mar = session.scalars(
        select(MarRecord)
        .where(MarRecord.rio_id == rio_id, MarRecord.superseded_by_id.is_(None))
        .order_by(MarRecord.published_at.desc())
    ).first()
    if mar is None:
        return Published("not_in_mar")

    now = datetime.now(UTC)
    in_mar = rio.routing == "mar"
    if in_mar:
        mar = upsert_mar_snapshot(session, rio)
        payload = build_push_payload(mar, rio)
        if mar.retracted_at is not None:  # Brave hid it (F47): show it again
            payload["visibility"] = "published"
    elif mar.retracted_at is not None:
        return Published("not_in_mar")
    elif mar.push_hash is None:
        # Never reached the API: nothing to hide there, just leave the outbox. push_hash,
        # not pushed_at: a superseding row starts with pushed_at NULL while the API still
        # holds the previous (visible) version — push_hash is carried across supersession.
        mar.retracted_at = now
        session.commit()
        return Published("retracted")
    else:
        payload = {**build_push_payload(mar, rio), "visibility": "hidden"}

    digest = _digest(payload)
    if mar.push_hash == digest and mar.retracted_at is None:
        if mar.pushed_at is None:  # a supersession that left the payload as the API has it
            mar.pushed_at = now
        mar.push_attempted_at = now
        session.commit()
        return Published("unchanged")
    # Committed BEFORE the POST: the outbox's backoff reads it, so a tick does not stack a
    # second retry chain on a row whose push is in flight (F65).
    mar.push_attempted_at = now
    session.commit()
    try:
        sent = asyncio.run(api.push(mar.entity_type, payload))
    except ApiDown:
        return Published("api_down")
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if not 400 <= status < 500 or status in _TRANSIENT_4XX:
            raise
        mar.push_error = f"{status}: {exc.response.text[:500]}"
        session.commit()
        logger.error("publish_rejected", rio_id=str(rio_id), status=status,
                     error=mar.push_error)
        return Published("rejected")
    if not sent:
        # Null adapter sent nothing: stamping would make the first real push a silent no-op.
        return Published("not_sent")
    mar.push_hash = digest
    mar.push_error = None
    if in_mar:
        mar.pushed_at = datetime.now(UTC)
        mar.retracted_at = None
    else:
        mar.retracted_at = datetime.now(UTC)
    session.commit()
    return Published("pushed" if in_mar else "retracted")


def republish_pending(session: Session, enqueue: Enqueue) -> int:
    """Enqueue the publish of every pending Mar row (capped per call); broker errors propagate."""
    rows = sync.pending_push_rows(session)
    for rio_id, _entity_type in rows:
        enqueue(str(rio_id))
    return len(rows)


def editorial_destination_payload(row: EditorialDestination) -> dict[str, Any]:
    """The destination upsert body: required keys + ONLY the filled editorial columns.

    The API upserts by ``source_ref`` and an explicit null OVERWRITES what it holds, so a
    null / empty / blank column is dropped here, never sent. ``source`` is the prefix of the
    source_ref — what the attraction push's ``destino`` block already sends for it.
    """
    payload: dict[str, Any] = {
        "source_ref": row.source_ref,
        "source": row.source_ref.split(":")[0],
        "tourist_name": row.tourist_name,
        "municipio_ibge": row.municipio_ibge,
    }
    for column, value in (row.fields or {}).items():
        if value is None or (isinstance(value, (str, list)) and not "".join(map(str, value)).strip()):
            continue
        payload[column] = value
    return payload


@dataclass(frozen=True)
class DestinationPushes:
    pushed: int = 0
    unchanged: int = 0  # pending, but the API already holds exactly this payload
    rejected: int = 0  # permanent 4xx / missing required key: ``push_error`` set
    not_sent: int = 0  # the Null adapter: nothing stamped
    held: bool = False  # editorial_push_enabled is off
    api_down: bool = False


def push_editorial_destinations(
    session: Session,
    api: NorteiaApiClientProtocol,
    *,
    enabled: bool,
    limit: int = 100,
) -> DestinationPushes:
    """Send the pending editorial destination rows (``POST /destinations``), oldest first.

    ``enabled`` False holds everything untouched. A row whose payload digest equals the last
    accepted one is only stamped (never re-sent); stamped on a real 2xx only. A permanent 4xx
    is recorded in ``push_error`` and NOT raised (the row waits for a change: the resolver
    clears it); auth/transient 4xx and 5xx propagate so the task retries, like ``publish``.
    """
    if not enabled:
        return DestinationPushes(held=True)
    rows = session.scalars(
        select(EditorialDestination)
        .where(
            EditorialDestination.pushed_at.is_(None),
            EditorialDestination.push_error.is_(None),
            EditorialDestination.has_fields(),  # an emptied row has nothing to say
        )
        .order_by(EditorialDestination.updated_at)
        .limit(limit)
    ).all()
    pushed = unchanged = rejected = not_sent = 0
    for row in rows:
        payload = editorial_destination_payload(row)
        columns = set(payload) - {"source_ref", "source", "tourist_name", "municipio_ibge"}
        if not columns:
            continue  # nothing to say: never send a bare upsert
        missing = [k for k in ("tourist_name", "municipio_ibge") if not payload.get(k)]
        if missing:
            row.push_error = f"missing required key(s): {', '.join(missing)}"
            session.commit()
            rejected += 1
            continue
        digest = _digest(payload)
        if row.push_hash == digest:
            row.pushed_at = datetime.now(UTC)
            session.commit()
            unchanged += 1
            continue
        try:
            sent = asyncio.run(api.push("destination", payload))
        except ApiDown:
            return DestinationPushes(pushed, unchanged, rejected, not_sent, api_down=True)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if not 400 <= status < 500 or status in _TRANSIENT_4XX:
                raise
            row.push_error = f"{status}: {exc.response.text[:500]}"
            session.commit()
            logger.error("editorial_destination_rejected", source_ref=row.source_ref, status=status)
            rejected += 1
            continue
        if not sent:
            not_sent += 1  # the Null adapter sent nothing: stamping would hide the first real push
            break
        row.push_hash, row.pushed_at, row.push_error = digest, datetime.now(UTC), None
        session.commit()
        pushed += 1
    return DestinationPushes(pushed, unchanged, rejected, not_sent)
