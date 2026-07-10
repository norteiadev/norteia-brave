"""Collection-engine control endpoints (operator start/stop of the Brave sweep).

The platform is up 24/7 but the collection engine is idle by default. These
endpoints let the dashboard start the full destinos+atrativos sweep, watch its
progress, and stop it gracefully (the orchestrator drains the in-flight UF and
returns to idle).

  GET  /api/v1/engine/status   — state + progress + pipeline counts (Bearer)
  POST /api/v1/engine/start    — start the sweep (steward or Bearer)
  POST /api/v1/engine/stop     — request graceful stop (steward or Bearer)

Start/stop are mutations → require_steward_or_bearer (T-05-07: an unauthenticated
caller must not be able to fan out expensive LLM/Places sweeps).
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from redis import Redis
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from brave.api.deps import get_db, get_redis, require_bearer, require_steward_or_bearer
from brave.config.runtime import enabled_sources, load_effective_config
from brave.config.settings import AppConfig
from brave.core import engine as collection_engine
from brave.core.models import MarRecord, NascenteRecord, RioRecord

logger = structlog.get_logger(__name__)
router = APIRouter()


def _effective_config(db: Session, redis: Redis) -> AppConfig:
    """Return the effective (env + config_settings overlay) config, best-effort.

    Phase D: the source registered/enabled gate reads the DB overlay so an operator
    who disables a lane in ``config_settings`` can no longer start it. The read is
    best-effort — any DB hiccup (or a MagicMock/stub session in the offline suite)
    falls back to the env-bootstrapped ``AppConfig()`` (both lanes enabled), so a
    config-store blip can never wedge the start endpoint. ``redis`` warms/serves the
    snapshot cache when available.
    """
    try:
        return load_effective_config(db, redis)
    except Exception as exc:  # pragma: no cover - defensive fallback
        logger.warning("engine_effective_config_fallback", error=str(exc))
        return AppConfig()

_ATRATIVO_SUB_STATES = [
    "discovered",
    "contacts_found",
    "signals_gathered",
    "description_enriched",
    "aguardando_consulta_whatsapp",
    "whatsapp_in_progress",
]


def _project_nascente_item(rec) -> dict:
    """Project a NascenteRecord into the LGPD field allow-list (board card).

    Pure + DB-free (unit-testable offline). The raw ``payload`` is NEVER returned
    wholesale — only the explicitly-approved fields below are read.

    LGPD allow-list — APPROVED fields:
      id, entity_type, uf, source, name, ingested_at, municipio, municipio_id.
    ``municipio`` (nome, e.g. "Vila Velha") and ``municipio_id`` (IBGE code) are
    PUBLIC-GEO — NOT PII, same class as name/uf (público, geo-territorial). They
    are resolved at ingest and live at ``payload.canonical.municipio`` /
    ``payload.municipio_id``. Both are null-safe: a missing/None payload,
    canonical, or field yields None (never raises).
    """
    payload = rec.payload or {}
    canonical = payload.get("canonical") or {}
    return {
        "id": str(rec.id),
        "entity_type": rec.entity_type,
        "uf": rec.uf,
        "source": rec.source,
        "name": payload.get("name") or rec.source_ref,
        "ingested_at": rec.ingested_at.isoformat() if rec.ingested_at else None,
        "municipio": canonical.get("municipio"),
        "municipio_id": payload.get("municipio_id"),
    }


def _pipeline_counts(db: Session) -> dict:
    """Per-layer counts for the engine progress feedback (mirrors /metrics)."""
    nascente = db.scalar(select(func.count(NascenteRecord.id))) or 0
    rio_rows = db.execute(
        select(RioRecord.routing, func.count(RioRecord.id)).group_by(RioRecord.routing)
    ).all()
    rio = {routing: n for routing, n in rio_rows}
    mar = db.scalar(select(func.count(MarRecord.id)).where(MarRecord.superseded_by_id.is_(None))) or 0

    atr_rows = db.execute(
        select(RioRecord.sub_state, func.count(RioRecord.id))
        .where(RioRecord.entity_type == "attraction")
        .group_by(RioRecord.sub_state)
    ).all()
    atr_by_state = {s: n for s, n in atr_rows if s is not None}

    return {
        "nascente": nascente,
        "rio": {
            "in_progress": rio.get("in_progress", 0),
            "mar": rio.get("mar", 0),
            "dlq": rio.get("dlq", 0),
            "descarte": rio.get("descarte", 0),
        },
        "mar": mar,
        "atrativos_by_sub_state": {s: atr_by_state.get(s, 0) for s in _ATRATIVO_SUB_STATES},
    }


@router.get("/api/v1/engine/status", dependencies=[Depends(require_bearer)])
def engine_status(
    db: Session = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> dict:
    """Engine state + run progress + live pipeline counts (for the dashboard).

    ``session=db`` lets the mode read self-heal from the durable ``config_settings``
    row after a Redis flush (Phase D): the first status poll re-seeds the live mode
    key, so the edit-lock and dispatch loop recover the operator's last choice.
    """
    status = collection_engine.get_status(redis, session=db)
    status["counts"] = _pipeline_counts(db)
    return status


@router.get("/api/v1/nascente", dependencies=[Depends(require_bearer)])
def list_nascente(
    uf: str | None = Query(None),
    entity_type: str | None = Query(None, description="destination | attraction"),
    unrouted: bool = Query(False, description="only Nascente records with no Rio twin"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> dict:
    """List raw Nascente records as read-only board cards (newest first).

    Nascente is the immutable append-only raw-payload layer. We surface only
    the CURRENT version of each record (superseded_by_id IS NULL) so the board
    reflects live ingest without duplicate version churn — matching the way the
    Mar count already excludes superseded rows.

    LGPD: an explicit allow-list of fields only (see `_project_nascente_item`).
    The raw `payload` is never returned wholesale; only `payload.name` (public
    place name) plus `payload.canonical.municipio` (município nome) and
    `payload.municipio_id` (IBGE code) are read. municipio + municipio_id are
    APPROVED PUBLIC-GEO fields — NOT PII, same class as name/uf (público,
    geo-territorial). Returns paginated {items, total, offset, limit}.
    """
    if unrouted:
        # Bug 4: only Nascente records with NO Rio twin (LEFT JOIN → NULL). Keeps
        # the current-version filter so the NASCENTE Kanban column shows only cards
        # that have not yet been routed into Rio.
        stmt = (
            select(NascenteRecord)
            .outerjoin(RioRecord, RioRecord.nascente_id == NascenteRecord.id)
            .where(
                NascenteRecord.superseded_by_id.is_(None),
                RioRecord.id.is_(None),
            )
        )
    else:
        stmt = select(NascenteRecord).where(NascenteRecord.superseded_by_id.is_(None))
    if uf:
        stmt = stmt.where(NascenteRecord.uf == uf)
    if entity_type:
        stmt = stmt.where(NascenteRecord.entity_type == entity_type)

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(NascenteRecord.ingested_at.desc()).offset(offset).limit(limit)
    ).all()

    items = [_project_nascente_item(rec) for rec in rows]

    return {"items": items, "total": total, "offset": offset, "limit": limit}


@router.post(
    "/api/v1/engine/start",
    status_code=202,
    dependencies=[Depends(require_steward_or_bearer)],
)
def engine_start(
    redis: Redis = Depends(get_redis),
    body: dict = Body(default={}),
    db: Session = Depends(get_db),
) -> dict:
    """Start the full sweep. Idempotent: 409 if a run is already active.

    Required body: { "depth": "nascente|nascente_rio|nascente_rio_mar", ... }.
    Optional: { "ufs": ["BA", ...], "lane": "destinos|atrativos|both" }.

    `depth` is the cost-checkpoint contract and is **required** — there is no
    implicit default, so the engine never silently spends.
    """
    from brave.tasks.beat_schedule import UF_LIST

    ufs = body.get("ufs") or list(UF_LIST)
    lane = body.get("lane", "both")

    # Validate depth BEFORE start_run (and before the already-running/409 branch):
    # a missing/invalid depth must return 422 even mid-run, never flipping engine
    # state nor first tripping 409 (T-10-02).
    depth = body.get("depth")
    if depth not in collection_engine._VALID_DEPTHS:
        raise HTTPException(
            status_code=422,
            detail="depth is required: nascente|nascente_rio|nascente_rio_mar",
        )

    # Validate source BEFORE start_run — same order as depth (T-11-03-03).
    # Phase D: the source must be REGISTERED (a known lane in the effective config)
    # AND ENABLED (enabled_sources). An unknown lane is a 422 (malformed request); a
    # known-but-disabled lane is a 409 (valid name, not currently collectable) — both
    # raised before any engine state mutation so a rejected start never spends.
    source = body.get("source", "tripadvisor")
    cfg = _effective_config(db, redis)
    if source not in cfg.sources:
        raise HTTPException(
            status_code=422,
            detail=f"source must be one of {sorted(cfg.sources)}",
        )
    if source not in enabled_sources(cfg):
        raise HTTPException(
            status_code=409,
            detail=f"source '{source}' is disabled in config — enable it before starting.",
        )

    # R2: TripAdvisor motor requires a live session — operator must inject a cURL first
    if source == "tripadvisor":
        from brave.lanes.tripadvisor.client import BRAVE_TA_SESSION_KEY  # noqa: PLC0415
        _ta_ttl = redis.ttl(BRAVE_TA_SESSION_KEY)
        # ttl > 0 → present + valid; 0 → just expired; -1 → no TTL (infinite, setex always
        # sets TTL so -1 = operator manually set via redis-cli); -2 → key absent
        if _ta_ttl != -1 and _ta_ttl <= 0:
            raise HTTPException(
                status_code=409,
                detail="Motor TripAdvisor requer uma sessão com TTL válido — injete um cURL primeiro.",
            )

    # Optional operator test-run throttle: cap attractions ingested per UF so the whole
    # Nascente→Rio→Mar flow can be exercised with a handful of records. Absent/null = no
    # cap (full sweep). Validated BEFORE start_run — a bad value 422s without mutating
    # engine state, mirroring the depth/source guards above. bool is an int subclass, so
    # reject it explicitly (True would otherwise pass as 1).
    max_atrativos_per_uf = body.get("max_atrativos_per_uf")
    if max_atrativos_per_uf is not None and (
        isinstance(max_atrativos_per_uf, bool)
        or not isinstance(max_atrativos_per_uf, int)
        or max_atrativos_per_uf < 1
    ):
        raise HTTPException(
            status_code=422,
            detail="max_atrativos_per_uf must be a positive integer",
        )

    if not collection_engine.start_run(redis, ufs_total=len(ufs)):
        raise HTTPException(
            status_code=409,
            detail="Engine already running — stop it before starting a new run.",
        )

    collection_engine.set_depth(redis, depth)
    # Persist the source under the SAME registered-and-enabled contract just validated
    # above (source ∈ cfg.sources ∧ source ∈ enabled_sources) — injected because the
    # kernel engine module must not import the domains registry (D-18).
    collection_engine.set_source(redis, source, valid_sources=enabled_sources(cfg))

    # A cold /start IS the LIGADO transition — the operator is turning collection ON.
    # Without this the operator-mode axis stays at whatever it was (e.g. DESLIGADO
    # after a fresh config_settings seed / DB reset), and engine_sweep_run's mode gate
    # (`get_mode() != LIGADO → break`) aborts the run BEFORE dispatching any UF: the
    # sweep "starts" (enabled=1, state=running) but collects nothing (dispatched=0).
    # Persist LIGADO durably (config_settings) so the dispatch loop and the Kanban
    # edit-lock agree. Mirrors the warm-resume path (POST /engine/mode LIGADO).
    collection_engine.set_mode(redis, collection_engine.LIGADO, session=db)

    # Persist a durable runs_history row (UI-PAINEL-2 Varreduras trail). Pitfall 3:
    # this is reached ONLY after the depth/source 422 guards AND start_run() success
    # — a rejected start (422/409 raises above) never creates a phantom row. The id
    # is generated client-side so run_id is available without a flush. Best-effort:
    # a runs-history write failure must NEVER abort an otherwise-valid engine start
    # (T-17.1-02-02). The run will simply have no DB trail (run_id stays None).
    run_id: str | None = None
    try:
        from brave.core.models import RunHistory

        run = RunHistory(
            id=uuid.uuid4(),
            ufs=list(ufs),
            source=source,
            depth=depth,
            lane=lane,
            ufs_total=len(ufs),
            status="running",
        )
        db.add(run)
        db.commit()
        run_id = str(run.id)
        redis.set("brave:engine:run_id", run_id)
    except Exception as exc:  # best-effort — never abort a valid start
        logger.warning("engine_start_runs_history_write_failed", error=str(exc))

    try:
        from brave.tasks.pipeline import engine_sweep_run

        engine_sweep_run.delay(
            ufs=ufs,
            lane=lane,
            depth=depth,
            source=source,
            run_id=run_id,
            max_per_uf=max_atrativos_per_uf,
        )
    except Exception as exc:  # broker-down
        from brave.config.settings import AppConfig

        if AppConfig().run_real_externals:
            collection_engine.mark_idle(redis)  # revert — the run never launched
            logger.error("engine_start_dispatch_failed", error=str(exc))
            raise HTTPException(
                status_code=503,
                detail="Engine start failed (broker unavailable). Retry once reachable.",
            ) from exc
        # Offline (tests/dev): no broker — leave state running; orchestrator is exercised separately.

    logger.info(
        "engine_started",
        ufs=len(ufs),
        lane=lane,
        depth=depth,
        source=source,
        max_atrativos_per_uf=max_atrativos_per_uf,
    )
    return {
        "status": "started",
        "ufs_total": len(ufs),
        "lane": lane,
        "depth": depth,
        "source": source,
        "max_atrativos_per_uf": max_atrativos_per_uf,
    }


@router.post(
    "/api/v1/engine/source",
    status_code=200,
    dependencies=[Depends(require_steward_or_bearer)],
)
def engine_set_source(
    redis: Redis = Depends(get_redis),
    body: dict = Body(default={}),
) -> dict:
    """Persist the active collection source without starting a run.

    Validates the source against the REGISTERED-AND-ENABLED lanes and writes it to
    the Redis source key (brave:engine:source). The next /start will read this key
    and route to the correct sweep lane.

    Registry-driven (Phase G STEP 3): the allowed set is ``enabled_sources`` of the
    env-effective ``AppConfig`` — no hardcoded ``'default'/'tripadvisor'`` literal, and
    no DB dependency (this configuration write stays DB-free, unlike /start which reads
    the config_settings overlay). Invalid source → 422 before any Redis write. No
    RunHistory row — this is a configuration write, not a dispatch.
    """
    source = body.get("source", "tripadvisor")
    valid = enabled_sources(AppConfig())
    if source not in valid:
        raise HTTPException(
            status_code=422,
            detail=f"source must be one of {sorted(valid)}",
        )
    collection_engine.set_source(redis, source, valid_sources=valid)
    logger.info("engine_source_set", source=source)
    return {"source": source}


@router.post(
    "/api/v1/engine/mode",
    status_code=200,
    dependencies=[Depends(require_steward_or_bearer)],
)
def engine_set_mode(
    redis: Redis = Depends(get_redis),
    body: dict = Body(default={}),
    db: Session = Depends(get_db),
) -> dict:
    """Set the operator mode (Motor Pausado, phase C) — LIGADO | PAUSADO | DESLIGADO.

    Orthogonal to the runtime state (idle|running|stopping): mode governs whether the
    orchestrator keeps fanning out UFs and whether the Kanban card edit-lock is
    released (require_editing_unlocked). The semantics live in engine.set_mode:
      - LIGADO    — normal auto-collection; card editing LOCKED (mutations → 423).
      - PAUSADO   — orchestrator drains on its next mode check (no new UFs, no
                    auto-push); runtime left as-is; card editing UNLOCKED.
      - DESLIGADO — hard off: also marks the engine idle + clears the enabled latch;
                    card editing UNLOCKED.

    Invalid mode → 422 before any Redis write (mirrors the /source guard). No
    RunHistory row — this is a configuration write, not a dispatch. Echoes the new
    mode plus editing_unlocked so the dashboard can update the lock indicator.
    """
    mode = body.get("mode")
    if mode not in collection_engine._VALID_MODES:
        raise HTTPException(
            status_code=422,
            detail="mode must be 'LIGADO', 'PAUSADO', or 'DESLIGADO'",
        )
    # Phase D: persist the mode durably. Redis stays the fast/authoritative live path
    # (set FIRST inside set_mode); config_settings is the durable store so a Redis
    # flush no longer resets the mode to LIGADO. Passing session enables the upsert +
    # snapshot-cache bust.
    collection_engine.set_mode(redis, mode, session=db)
    logger.info("engine_mode_set", mode=mode)
    return {
        "mode": mode,
        "editing_unlocked": collection_engine.is_editing_unlocked(redis, session=db),
    }


@router.post(
    "/api/v1/engine/stop",
    status_code=202,
    dependencies=[Depends(require_steward_or_bearer)],
)
def engine_stop(redis: Redis = Depends(get_redis)) -> dict:
    """Request a graceful stop. The orchestrator drains the in-flight UF then idles.

    Always clears the operator-intent enabled latch regardless of whether the engine
    was running — so the dashboard toggle stays OFF even when state is idle.
    """
    was_running = collection_engine.request_stop(redis)
    collection_engine.set_enabled(redis, False)
    if not was_running:
        return {"status": "noop", "detail": "engine is not running"}
    logger.info("engine_stop_requested")
    return {"status": "stopping"}
