"""Process observability endpoints (D-05).

Provides:
  GET /api/v1/workers   — Celery inspect + Redis queue depths, graceful broker-absent
  GET /api/v1/failures  — PoisonQuarantine list with by_task counts

Both endpoints are Bearer-guarded. Neither performs any writes.

Design decisions:
  - celery_app imported lazily inside handler body to avoid import-time broker
    connection (Pitfall 1: hanging on Celery import when broker is down).
  - inspect(timeout=1.0) + try/except wraps the entire inspect block; None returns
    coerced to {} so broker absence always returns 200 with broker_reachable=False,
    never a 500.
  - Redis LLEN wrapped in separate try/except; returns null on Redis error.
  - PoisonQuarantine.payload NOT serialized in /failures list response — it can be
    large and contain pipeline internals (T-08-08).
"""

import re

from fastapi import APIRouter, Depends, Query
from redis import Redis
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from brave.api.deps import get_db, get_redis, require_bearer
from brave.core.models import PoisonQuarantine, RecordEvent

router = APIRouter()


# ---------------------------------------------------------------------------
# LGPD output-side scrubbing (defense-in-depth)
# ---------------------------------------------------------------------------

# A phone-like digit run: optional '+', a digit, then 7+ digits/spacers, a digit.
# RecordEvent.message / data["error"] are written from str(exc) at the emission
# sites, which can echo input that contains a phone. The write side is the
# minimization boundary, but this is a second guard on the read side.
_PHONE_RE = re.compile(r"\+?\d[\d\s().-]{7,}\d")


def _scrub_event_text(s: str | None) -> str | None:
    """Redact phone-like digit runs from surfaced free-text and cap length (LGPD).

    Returns None for None; otherwise redacts any phone-like digit run and
    truncates to 500 chars. Applied to RecordEvent.message / data["error"]
    before they leave the API (the new Log-tab + Falha-card endpoints).
    """
    if s is None:
        return None
    return _PHONE_RE.sub("[redacted]", s)[:500]


def _scrub_event_data(data: dict | None) -> dict | None:
    """Return event ``data`` with its free-text ``error`` field scrubbed (LGPD).

    Structured fields (stage/status/name/uf/locationId/…) pass through untouched;
    only the free-text ``error`` (str(exc) at write time) is redacted + capped.
    """
    if not isinstance(data, dict) or "error" not in data:
        return data
    if not isinstance(data.get("error"), str):
        return data
    scrubbed = dict(data)
    scrubbed["error"] = _scrub_event_text(scrubbed["error"])
    return scrubbed


def _poison_source_ref(payload: dict | None) -> str | None:
    """Derive the universal drawer source_ref from a legacy poison payload.

    Legacy PoisonQuarantine rows for the TripAdvisor attractions lane carry the
    numeric ``locationId`` in their (LGPD-minimized) payload. The canonical
    source_ref for a TA attraction is ``tripadvisor:attraction:{locationId}`` —
    the same key RioRecord.canonical_key / RecordEvent.source_ref use — so a
    legacy incident with no RecordEvent can still be keyed into the Falha column.
    Returns None when the payload has no locationId (nothing to key on).
    """
    if not isinstance(payload, dict):
        return None
    loc = payload.get("locationId")
    if loc in (None, "", "unknown"):
        return None
    return f"tripadvisor:attraction:{loc}"


@router.get("/api/v1/workers", dependencies=[Depends(require_bearer)])
def get_workers(redis: Redis = Depends(get_redis)) -> dict:
    """Return Celery worker health + Redis queue depths.

    Gracefully handles broker absence: inspect timeout=1.0s, entire block in
    try/except, None returns coerced to empty dict. Returns broker_reachable=false
    and workers=[] (not a 500) when no broker or workers are available.

    T-08-07: timeout=1.0 + try/except prevents self-inflicted DoS from broker hang.
    """
    # Lazy import to avoid import-time broker connection (Pitfall 1).
    from brave.tasks.celery_app import app as celery_app  # noqa: PLC0415

    try:
        i = celery_app.control.inspect(timeout=1.0)
        ping = i.ping() or {}  # None → {} when broker unreachable
        active = i.active() or {}
        reserved = i.reserved() or {}
    except Exception:
        ping = active = reserved = {}

    broker_reachable = bool(ping)
    workers = [
        {
            "hostname": h,
            "status": "up" if resp.get("ok") == "pong" else "down",
            "active_count": len(active.get(h, [])),
            "reserved_count": len(reserved.get(h, [])),
        }
        for h, resp in ping.items()
    ]

    try:
        queue_depths = {
            "brave.sweep": redis.llen("brave.sweep"),
            "celery": redis.llen("celery"),
        }
    except Exception:
        queue_depths = {"brave.sweep": None, "celery": None}

    # WR-04: derive the real entry count from the live schedule rather than a
    # hardcoded literal that drifts the moment UF_LIST changes.
    from brave.tasks.beat_schedule import BRAVE_BEAT_SCHEDULE  # noqa: PLC0415

    return {
        "broker_reachable": broker_reachable,
        "workers": workers,
        "queues": queue_depths,
        "beat_schedule": {
            "entries": len(BRAVE_BEAT_SCHEDULE),
            "queues": ["brave.sweep"],
        },
    }


@router.get("/api/v1/failures", dependencies=[Depends(require_bearer)])
def get_failures(
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    """Return PoisonQuarantine list with by_task counts.

    Returns up to `limit` quarantine entries ordered by quarantined_at DESC.
    Provides a by_task count dict for quick anomaly detection.

    T-08-08: PoisonQuarantine.payload NOT included in list response — it can be
    large and contain pipeline internals. Only task_name + error_message (truncated
    to 500 chars) + quarantined_at are surfaced.
    """
    rows = list(
        db.scalars(
            select(PoisonQuarantine)
            .order_by(PoisonQuarantine.quarantined_at.desc())
            .limit(limit)
        ).all()
    )

    # WR-02: the true quarantine count, independent of the page limit. `total`
    # previously reported len(rows) (== limit when capped), undercounting during
    # incident spikes. `returned` carries the page size.
    total = db.scalar(select(func.count()).select_from(PoisonQuarantine)) or 0

    # WR-03: aggregate by_task across ALL quarantine rows via a grouped DB query,
    # not just the returned page — otherwise the breakdown chips reflect only the
    # most recent `limit` rows.
    by_task: dict[str, int] = {
        task_name: count
        for task_name, count in db.execute(
            select(PoisonQuarantine.task_name, func.count()).group_by(
                PoisonQuarantine.task_name
            )
        ).all()
    }

    return {
        "total": total,
        "returned": len(rows),
        "by_task": by_task,
        "items": [
            {
                "id": str(r.id),
                "task_name": r.task_name,
                "error_message": (r.error_message or "")[:500],
                "quarantined_at": r.quarantined_at.isoformat() if r.quarantined_at else None,
            }
            for r in rows
        ],
    }


@router.get("/api/v1/failures/cards", dependencies=[Depends(require_bearer)])
def get_failure_cards(db: Session = Depends(get_db)) -> list[dict]:
    """Return one card per failed record, keyed by the universal source_ref.

    Primary source is RecordEvent (status='fail'): the LATEST fail event per
    source_ref becomes a Falha-column card carrying the record's REAL identity
    (name from event.data, uf, entity_type) instead of the opaque task_name.

    Fallback (LEFT-merge): legacy PoisonQuarantine rows that predate the
    RecordEvent store (or whose lane never emitted an event) surface too — their
    source_ref is derived from payload.locationId (``_poison_source_ref``). A
    poison row is only added when its derived source_ref is NOT already covered
    by a fail event (the RecordEvent card wins — it is richer/newer).

    LGPD: only public-geo + engineering fields are surfaced (name/uf/entity_type,
    stage, error message, quarantined_at) — never PII/phone/review text.

    Returns a list ordered by quarantined_at DESC (most recent incident first).
    """
    # --- Primary: latest fail RecordEvent per source_ref ---------------------
    latest = (
        select(
            RecordEvent.source_ref.label("source_ref"),
            func.max(RecordEvent.created_at).label("mx"),
        )
        .where(RecordEvent.status == "fail")
        .group_by(RecordEvent.source_ref)
        .subquery()
    )
    fail_rows = list(
        db.scalars(
            select(RecordEvent)
            .join(
                latest,
                and_(
                    RecordEvent.source_ref == latest.c.source_ref,
                    RecordEvent.created_at == latest.c.mx,
                ),
            )
            .where(RecordEvent.status == "fail")
        ).all()
    )

    cards: dict[str, dict] = {}
    for e in fail_rows:
        if e.source_ref in cards:
            continue  # tie on created_at → keep the first (deterministic)
        data = e.data or {}
        cards[e.source_ref] = {
            "source_ref": e.source_ref,
            "name": data.get("name"),
            "uf": e.uf or data.get("uf"),
            "entity_type": e.entity_type,
            "last_stage": e.stage,
            # LGPD: scrub phone-like runs + cap the free-text error before surfacing.
            "error": _scrub_event_text(e.message),
            "quarantined_at": e.created_at.isoformat() if e.created_at else None,
        }

    # --- Fallback: legacy poison rows without a covering fail event ----------
    poison_rows = list(
        db.scalars(
            select(PoisonQuarantine).order_by(PoisonQuarantine.quarantined_at.desc())
        ).all()
    )
    for p in poison_rows:
        payload = p.payload or {}
        sref = _poison_source_ref(payload)
        if sref is None or sref in cards:
            continue
        cards[sref] = {
            "source_ref": sref,
            "name": payload.get("name"),
            "uf": payload.get("uf"),
            "entity_type": "attraction",
            "last_stage": "quarantined",
            # LGPD: scrub phone-like runs + cap the free-text error before surfacing.
            "error": _scrub_event_text(p.error_message),
            "quarantined_at": p.quarantined_at.isoformat() if p.quarantined_at else None,
        }

    return sorted(
        cards.values(),
        key=lambda c: c["quarantined_at"] or "",
        reverse=True,
    )


@router.get("/api/v1/failures/cards/log", dependencies=[Depends(require_bearer)])
def get_failure_card_log(
    source_ref: str = Query(..., description="Universal drawer key, e.g. tripadvisor:attraction:{locationId}"),
    db: Session = Depends(get_db),
) -> dict:
    """Return the Log-tab timeline + identity for a Falha card without a rio_id.

    Mirrors the ``events`` block of the CMS detail endpoints but keyed on the
    caller-supplied source_ref (a card in the Falha column may have no RioRecord,
    so it cannot go through fetchAtrativoDetail). Returns the append-only event
    timeline oldest→newest plus a small ``identity`` block the drawer header uses.

    Legacy fallback: when no RecordEvent exists for the source_ref, a matching
    PoisonQuarantine row (derived source_ref) synthesizes a single terminal
    ``quarantined`` step so the incident is never opaque.

    LGPD: RecordEvent.data / poison payload are already public-geo + engineering
    only — surfaced verbatim; no PII path.
    """
    rows = list(
        db.scalars(
            select(RecordEvent)
            .where(RecordEvent.source_ref == source_ref)
            .order_by(RecordEvent.created_at.asc())
        ).all()
    )

    if rows:
        events = [
            {
                "stage": e.stage,
                "status": e.status,
                # LGPD: scrub free-text message + data["error"] before surfacing.
                "message": _scrub_event_text(e.message),
                "data": _scrub_event_data(e.data),
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in rows
        ]
        name = uf = entity_type = last_error = None
        for e in rows:
            data = e.data or {}
            if data.get("name"):
                name = data.get("name")
            if e.uf or data.get("uf"):
                uf = e.uf or data.get("uf")
            if e.entity_type:
                entity_type = e.entity_type
            if e.status == "fail" and e.message:
                last_error = e.message
        return {
            "events": events,
            "identity": {
                "name": name,
                "uf": uf,
                "entity_type": entity_type,
                # LGPD: last_error is the same free-text as the event message.
                "last_error": _scrub_event_text(last_error),
            },
        }

    # --- Legacy fallback: synthesize from the matching poison row ------------
    poison = next(
        (
            p
            for p in db.scalars(
                select(PoisonQuarantine).order_by(
                    PoisonQuarantine.quarantined_at.desc()
                )
            ).all()
            if _poison_source_ref(p.payload) == source_ref
        ),
        None,
    )
    if poison is None:
        return {
            "events": [],
            "identity": {"name": None, "uf": None, "entity_type": None, "last_error": None},
        }

    payload = poison.payload or {}
    events = [
        {
            "stage": "quarantined",
            "status": "fail",
            # LGPD: scrub the free-text error before surfacing.
            "message": _scrub_event_text(poison.error_message),
            "data": {
                k: payload.get(k)
                for k in ("locationId", "name", "uf", "reason", "offset")
                if payload.get(k) is not None
            },
            "created_at": (
                poison.quarantined_at.isoformat() if poison.quarantined_at else None
            ),
        }
    ]
    return {
        "events": events,
        "identity": {
            "name": payload.get("name"),
            "uf": payload.get("uf"),
            "entity_type": "attraction",
            "last_error": _scrub_event_text(poison.error_message),
        },
    }
