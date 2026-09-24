"""Brave collection engine — a Redis-backed start/stop control for the sweep.

The platform runs 24/7 but the *collection engine* is idle by default: no
destinos/atrativos sweep fans out until an operator starts it from the dashboard.
Stopping is graceful — the orchestrator finishes dispatching the current UF and
then drains: already-enqueued UF tasks complete on the workers, no new UFs are
fanned out, and the engine returns to `idle`.

State lives in Redis (shared between the FastAPI control endpoints and the Celery
orchestrator task):

  brave:engine:state      idle | running | stopping
  brave:engine:current_uf the UF currently being fanned out (for visual feedback)
  brave:engine:ufs_done   how many UFs the current run has dispatched
  brave:engine:ufs_total  how many UFs the current run will dispatch
  brave:engine:mode       LIGADO | PAUSADO | DESLIGADO — the operator layer,
                          orthogonal to state; governs auto-dispatch + the
                          Kanban card edit-lock (Motor Pausado, phase C)

This module performs no dispatch. The orchestrator task
(brave.tasks.pipeline.engine_sweep_run) reads `state` between UFs and breaks the
loop when it is no longer `running`, which is what makes Stop graceful. It also
reads `mode` and breaks when it is no longer `LIGADO`: PAUSADO/DESLIGADO stop new
fan-out (graceful drain) while releasing the card edit-lock.

Run lifecycle — callers use only these verbs; every key above is private:

  start(...)            → run_id   guard + reset + depth/source + LIGADO + runs_history row
  abort(...)                       dispatch failed: back to idle, row → "falha"
  claim_producer(...)              before each producer .delay
  progress(...)                    one UF (or one bulk page) dispatched
  producer_done(...)               in each producer's terminal finally
  dispatch_finished(...)           when the orchestrator's loop ends

The run completes (motor off + runs_history finalized) inside producer_done /
dispatch_finished once dispatch is done and no producer is in flight. ``run_id`` is a
generation token: a producer_done/progress for a run that is no longer current is
ignored, so a straggler from an old run can never drain a new run's counters.
"""

from __future__ import annotations

import contextlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog

from brave.config.runtime import ENGINE_MODE_KEY, upsert_config

logger = structlog.get_logger(__name__)

IDLE = "idle"
RUNNING = "running"
STOPPING = "stopping"
_VALID = {IDLE, RUNNING, STOPPING}

# Pipeline depth = how far a run reaches (the cost-checkpoint contract, shared
# verbatim with the dashboard TS layer). Orthogonal to `lane` (which entity
# families run). Depth is the spend gate:
#   nascente         — ingest + reliability score only. Free (no Places, no LLM).
#   nascente_rio     — + Places/LLM validation up to Rio routing (paid).
#   nascente_rio_mar — full pipeline incl. the idempotent norteia-api Mar push.
NASCENTE = "nascente"
NASCENTE_RIO = "nascente_rio"
NASCENTE_RIO_MAR = "nascente_rio_mar"
VALID_DEPTHS = frozenset({NASCENTE, NASCENTE_RIO, NASCENTE_RIO_MAR})

_STATE_KEY = "brave:engine:state"
_CURRENT_UF_KEY = "brave:engine:current_uf"
_UFS_DONE_KEY = "brave:engine:ufs_done"
_UFS_TOTAL_KEY = "brave:engine:ufs_total"
_DEPTH_KEY = "brave:engine:depth"
_SOURCE_KEY = "brave:engine:source"
_ENABLED_KEY = "brave:engine:enabled"
_MODE_KEY = "brave:engine:mode"
# Sync marker (BUG 6/7): "1" iff the most recent run finished draining. Cleared at
# run START (a fresh run is not "synced" yet) and set at run END — atomically inside
# _maybe_complete when the LAST producer finishes. Drives get_status's derived
# "sync_phase" for the dashboard badge.
_LAST_RUN_ENDED_KEY = "brave:engine:last_run_ended"
# run_id of the CURRENT run — the generation token (and the runs_history row id).
_RUN_ID_KEY = "brave:engine:run_id"
# Producer-completes lifecycle (live-kanban fix): the run stays RUNNING while any
# producer task is in flight and only flips to synced when the LAST producer finishes.
#   _INFLIGHT_KEY      count of dispatched producer tasks still running (>=0)
#   _DISPATCH_DONE_KEY "1" once the orchestrator's dispatch loop has fanned out every
#                      producer for this run (absent = still dispatching). A run may
#                      only complete AFTER dispatch is done AND inflight has drained.
_INFLIGHT_KEY = "brave:engine:producers_inflight"
_DISPATCH_DONE_KEY = "brave:engine:dispatch_done"
# A reasoned pause (provider_balance | daily_budget) — set by pause_with_reason, read by
# get_status for the Painel banner, cleared only by set_mode(LIGADO) (both resume paths:
# POST /engine/start and POST /engine/mode LIGADO already call set_mode).
_PAUSE_REASON_KEY = "brave:engine:pause_reason"

# Source selects which ingest lane the orchestrator dispatches:
#   default      — Google Places attraction lane (discover_atrativo_task; dormant by
#                  default — the Mtur destino seed is retired)
#   tripadvisor  — TripAdvisor lane (sweep_tripadvisor task, plan 11-03)
VALID_SOURCES = frozenset({"default", "tripadvisor"})

# Operator mode (Motor Pausado, phase C) — an ORTHOGONAL operator layer, distinct
# from the runtime state axis (idle|running|stopping). It governs two things at
# once: whether the orchestrator keeps fanning out work, and whether the Kanban
# card edit-lock is released.
#   LIGADO     — normal auto-collection: the sweep dispatches; card editing is
#                LOCKED (the four mutation endpoints return 423).
#   PAUSADO    — the orchestrator drains (breaks its loop: no new UFs, no auto-push)
#                but the runtime state is left AS-IS; card editing is UNLOCKED so a
#                steward can hand-edit / promote. Does NOT clear the enabled latch.
#   DESLIGADO  — hard off: additionally marks the engine idle + clears the enabled
#                latch; card editing is UNLOCKED.
# Values are uppercase Portuguese (operator-facing), unlike the lowercase runtime
# state/depth/source values — the case difference marks the distinct axis.
LIGADO = "LIGADO"
PAUSADO = "PAUSADO"
DESLIGADO = "DESLIGADO"
VALID_MODES = frozenset({LIGADO, PAUSADO, DESLIGADO})


def _decode(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def set_enabled(redis: Any, enabled: bool) -> None:
    """Set the operator-intent latch. True = engine should be running; False = stopped."""
    redis.set(_ENABLED_KEY, "1" if enabled else "0")


def is_enabled(redis: Any) -> bool:
    """Return the operator-intent latch. Absent or any non-'1' value → False."""
    return _decode(redis.get(_ENABLED_KEY)) == "1"


def get_state(redis: Any) -> str:
    """Current engine state. Absent key → idle (idle by default in production)."""
    raw = _decode(redis.get(_STATE_KEY))
    return raw if raw in _VALID else IDLE


def is_running(redis: Any) -> bool:
    return get_state(redis) == RUNNING


def should_halt_producer(redis: Any) -> bool:
    """True when an in-flight producer must STOP mid-sweep (pause / off / stop).

    The orchestrator's dispatch loop breaks on pause/off/stop, but each producer
    it already fanned out (one per UF) keeps paginating and inserting until its
    own list/pages are exhausted — so a mid-run Motor Pausado/Desligado leaves
    destinos/atrativos still landing in Nascente. Each producer polls this between
    pages/records and breaks when it fires.

    Keyed on MODE (get_mode defaults LIGADO even on a flushed Redis) plus a Stop
    (state == STOPPING), mirroring the orchestrator gate. It intentionally does
    NOT treat state == IDLE as halt, so a directly-dispatched standalone bulk run
    (scripts/ta_bulk_sweep.py — never went through start, so state stays IDLE)
    is not falsely halted; it still honors a painel PAUSADO/DESLIGADO.
    """
    return get_mode(redis) != LIGADO or get_state(redis) == STOPPING


def start(
    redis: Any,
    session: Any,
    *,
    action: str,
    depth: str,
    source: str,
    ufs: list[str],
    lane: str,
    valid_sources: Any = None,
) -> str | None:
    """Start a fresh run and return its run_id; None (no-op) if a run is already active.

    Start never stacks two orchestrators. Resets the progress/lifecycle counters, sets
    depth + source (sweep only — a describe run's ``depth``/``source`` are only the
    runs_history labels), turns the motor LIGADO (a cold start IS the LIGADO transition:
    otherwise a DESLIGADO left over from a seed/reset makes the orchestrator's mode gate
    abort before the first UF), stores the run_id generation token and inserts the
    runs_history row (status "running"). The row is BEST-EFFORT — a DB failure never
    aborts an otherwise-valid start; the run just has no Varreduras trail.

    Validation (depth/source/HTTP semantics) stays at the API edge; set_depth/set_source
    still raise ValueError on an out-of-contract value. ``valid_sources`` is injected by
    the caller (the kernel must not import the domains registry, D-18).
    """
    if get_state(redis) in (RUNNING, STOPPING):
        return None
    # A previous run forced idle (DESLIGADO/R1) with producers still in flight never
    # completed; its stragglers are now a stale generation, so close its row here.
    previous = _decode(redis.get(_RUN_ID_KEY)) or None
    if previous is not None and not _run_ended(redis):
        _finalize(session, previous, "parcial", int(_decode(redis.get(_UFS_DONE_KEY)) or 0))
    redis.set(_STATE_KEY, RUNNING)
    redis.set(_ENABLED_KEY, "1")
    redis.set(_UFS_TOTAL_KEY, len(ufs))
    redis.set(_UFS_DONE_KEY, 0)
    redis.delete(_CURRENT_UF_KEY)
    redis.delete(_LAST_RUN_ENDED_KEY)  # a fresh run is not "synced" yet
    redis.set(_INFLIGHT_KEY, "0")
    redis.delete(_DISPATCH_DONE_KEY)
    if action == "sweep":
        set_depth(redis, depth)
        set_source(redis, source, valid_sources=valid_sources)
    set_mode(redis, LIGADO, session=session)
    run_id = str(uuid.uuid4())
    redis.set(_RUN_ID_KEY, run_id)
    if session is not None:
        try:
            from brave.core.models import RunHistory  # noqa: PLC0415

            session.add(
                RunHistory(
                    id=uuid.UUID(run_id),
                    ufs=list(ufs),
                    source=source,
                    depth=depth,
                    lane=lane,
                    ufs_total=len(ufs),
                    status="running",
                )
            )
            session.commit()
        except Exception as exc:  # best-effort — never abort a valid start
            session.rollback()
            logger.warning("engine_start_runs_history_write_failed", error=str(exc))
    return run_id


def abort(redis: Any, session: Any, run_id: str) -> None:
    """Revert a start whose dispatch failed: idle, latch off, run_id cleared, row → falha."""
    if _current(redis, run_id) is not None:
        _mark_idle(redis)
        set_enabled(redis, False)
        redis.delete(_RUN_ID_KEY)
    _finalize(session, run_id, "falha", 0)


def request_stop(redis: Any) -> bool:
    """Ask a running engine to stop after the current UF. Returns False if idle."""
    if get_state(redis) != RUNNING:
        return False
    redis.set(_STATE_KEY, STOPPING)
    return True


def _mark_idle(redis: Any) -> None:
    redis.set(_STATE_KEY, IDLE)
    redis.delete(_CURRENT_UF_KEY)


# ---------------------------------------------------------------------------
# Producer-completes lifecycle (live-kanban fix)
# ---------------------------------------------------------------------------
#
# The orchestrator (engine_sweep_run) only *dispatches* producer tasks; those tasks
# run for minutes AFTER the dispatch loop returns. Completion therefore belongs to the
# LAST producer: the orchestrator claims each producer before its dispatch and calls
# dispatch_finished when its loop ends; every producer calls producer_done in its own
# terminal finally; whoever brings the counter to zero (dispatch already done)
# atomically claims completion.


def _current(redis: Any, run_id: str | None) -> str | None:
    """The current run's id if ``run_id`` addresses it (None = "the current run").

    None when there is no current run or ``run_id`` belongs to an older generation.
    """
    current = _decode(redis.get(_RUN_ID_KEY)) or None
    if current is None or (run_id is not None and run_id != current):
        return None
    return current


def _run_ended(redis: Any) -> bool:
    return _decode(redis.get(_LAST_RUN_ENDED_KEY)) == "1"


def claim_producer(redis: Any, run_id: str | None = None) -> str | None:
    """Count one producer in flight for the run; call BEFORE its dispatch.

    Returns the run_id it was counted against (pass it to the producer and to its
    producer_done), or None when there is no live run to count against (stale
    generation / run already ended) — then the producer must not call producer_done.
    """
    current = _current(redis, run_id)
    if current is None or _run_ended(redis):
        return None
    redis.incr(_INFLIGHT_KEY)
    return current


def progress(redis: Any, run_id: str | None = None, n: int = 1, *, uf: str | None = None) -> None:
    """Record ``n`` units of the plan dispatched (a UF, or a bulk page). Stale runs are ignored."""
    if _current(redis, run_id) is None or _run_ended(redis):
        return
    if uf is not None:
        redis.set(_CURRENT_UF_KEY, uf)
    redis.incrby(_UFS_DONE_KEY, n)


def producer_done(redis: Any, session: Any, run_id: str | None = None) -> bool:
    """A producer reached its terminal outcome. Returns True iff it completed the run.

    A producer of an older generation is ignored — it can never drain the new run's
    counter. So is one with ``run_id=None``: it ran outside any run (the daily discover
    beat, a reprocess, the CLI) and was never claimed, so it has nothing to pay back.
    The decrement is clamped at zero (DESLIGADO zeroes the counter while producers are
    still in flight).
    """
    if run_id is None:
        return False
    current = _current(redis, run_id)
    if current is None:
        return False
    if int(redis.decr(_INFLIGHT_KEY)) < 0:
        redis.set(_INFLIGHT_KEY, "0")
    return _maybe_complete(redis, session, current)


def dispatch_finished(redis: Any, session: Any, run_id: str | None = None) -> bool:
    """The orchestrator's dispatch loop ended. Returns True iff it completed the run."""
    current = _current(redis, run_id)
    if current is None:
        return False
    redis.set(_DISPATCH_DONE_KEY, "1")
    return _maybe_complete(redis, session, current)


def _inflight(redis: Any) -> int:
    return int(_decode(redis.get(_INFLIGHT_KEY)) or 0)


def _maybe_complete(redis: Any, session: Any, run_id: str) -> bool:
    """Complete the run iff dispatch is done and no producer is still in flight.

    RACE-SAFE single-winner: two producers can drain the counter to zero concurrently;
    the atomic ``GETSET`` on the sync marker is the claim, so exactly one caller turns
    the motor off (redis-only DESLIGADO — skipped on a reasoned pause, which must keep
    reading PAUSADO + reason) and finalizes the runs_history row.

    Status: "concluido" only when every planned unit was dispatched and nothing
    interrupted the run; a Stop, pause, DESLIGADO or R1 (state no longer RUNNING, or
    mode no longer LIGADO) → "parcial".
    """
    if _inflight(redis) > 0 or _decode(redis.get(_DISPATCH_DONE_KEY)) != "1":
        return False
    done = int(_decode(redis.get(_UFS_DONE_KEY)) or 0)
    total = int(_decode(redis.get(_UFS_TOTAL_KEY)) or 0)
    interrupted = (
        get_state(redis) != RUNNING or get_mode(redis) != LIGADO or done < total
    )
    if _decode(redis.getset(_LAST_RUN_ENDED_KEY, "1")) == "1":
        return False  # another caller already completed this run
    # The run still ENDS on a reasoned pause (state must go idle, or the Painel's Continuar
    # → /engine/start would 409 on "already running"); only the mode flip is skipped.
    _mark_idle(redis)
    set_enabled(redis, False)
    if redis.get(_PAUSE_REASON_KEY) is None:
        redis.set(_MODE_KEY, DESLIGADO)
    _finalize(session, run_id, "parcial" if interrupted else "concluido", done)
    return True


def _finalize(session: Any, run_id: str, status: str, dispatched: int) -> None:
    """Best-effort finalize of the runs_history row; never raises (T-17.1-02-02)."""
    if session is None:
        return
    try:
        from brave.core.models import RunHistory  # noqa: PLC0415

        run = session.get(RunHistory, uuid.UUID(run_id))
        if run is not None:
            run.ended_at = datetime.now(UTC)
            run.ufs_dispatched = dispatched
            run.status = status
            session.commit()
    except Exception as exc:  # best-effort — never break the run
        with contextlib.suppress(Exception):
            session.rollback()
        logger.warning("engine_run_history_finalize_failed", run_id=run_id, error=str(exc))


def pause_with_reason(
    redis: Any, reason: str, provider: str | None = None, *, action: str | None = None
) -> None:
    """Pause the motor (mode=PAUSADO) with a human-visible reason for the Painel.

    Distinct from a plain ``set_mode(redis, PAUSADO)``: this ALSO writes the reason
    (``provider_balance`` | ``daily_budget``), which provider tripped it (if any), and
    which action (``sweep`` | ``describe``) was interrupted — so the Painel can render a
    banner and rebuild the Continuar resume call. Cleared only by ``set_mode(LIGADO)``.
    """
    redis.set(
        _PAUSE_REASON_KEY,
        json.dumps(
            {
                "reason": reason,
                "provider": provider,
                "action": action,
                "at": datetime.now(UTC).isoformat(),
            }
        ),
    )
    set_mode(redis, PAUSADO)


def get_pause_reason(redis: Any) -> dict[str, Any] | None:
    """The current reasoned-pause payload, or None when absent/corrupt."""
    raw = _decode(redis.get(_PAUSE_REASON_KEY))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def set_depth(redis: Any, depth: str) -> None:
    """Persist the chosen pipeline depth. Rejects anything outside the contract.

    Invalid values raise ValueError and are never written — the engine must not
    silently spend on an unrecognized (possibly more expensive) reach. Kept
    orthogonal to the run state so lane (entity family) and depth (reach) stay
    independent; start() sets it for a sweep run.
    """
    if depth not in VALID_DEPTHS:
        raise ValueError(
            f"invalid depth {depth!r}; expected one of {sorted(VALID_DEPTHS)}"
        )
    redis.set(_DEPTH_KEY, depth)


def get_depth(redis: Any) -> str | None:
    """Persisted depth, or None when absent/corrupt (unset → required at the edge)."""
    raw = _decode(redis.get(_DEPTH_KEY))
    return raw if raw in VALID_DEPTHS else None


def set_source(
    redis: Any, source: str, *, valid_sources: Any = None
) -> None:
    """Persist the chosen ingest source lane. Rejects anything outside the contract.

    Invalid values raise ValueError and are never written — the engine must not
    silently dispatch an unrecognized (possibly expensive or unknown) lane.

    Validation set (import posture / D-18): ``brave.core`` is kernel and must NOT
    import the ``brave.domains`` registry, so the caller INJECTS the allowed set via
    ``valid_sources`` — the API edge passes the REGISTERED-AND-ENABLED lanes
    (``enabled_sources(config)``) so a disabled/unknown source is rejected here too.
    When ``valid_sources`` is ``None`` the legacy in-kernel ``VALID_SOURCES`` literal
    is used (back-compat for direct callers/tests). Mirrors set_depth otherwise.
    """
    allowed = VALID_SOURCES if valid_sources is None else frozenset(valid_sources)
    if source not in allowed:
        raise ValueError(
            f"invalid source {source!r}; expected one of {sorted(allowed)}"
        )
    redis.set(_SOURCE_KEY, source)


def get_source(redis: Any) -> str | None:
    """Persisted source lane, or None when absent/corrupt (defaults to 'default' at /start)."""
    raw = _decode(redis.get(_SOURCE_KEY))
    return raw if raw in VALID_SOURCES else None


def set_mode(redis: Any, mode: str, *, session: Any = None) -> None:
    """Persist the operator mode (Motor Pausado, phase C). Rejects unknown values.

    Mode is orthogonal to the runtime state (idle|running|stopping) and does NOT by
    itself drive state transitions — with one deliberate exception:

      - DESLIGADO is a hard off, so it ALSO returns the engine to idle,
        clears the operator-intent enabled latch (set_enabled False), and zeroes the
        producer inflight counter so the sync badge cannot stay "syncing" after OFF.
      - PAUSADO leaves the runtime AS-IS — a running sweep drains gracefully on its
        next mode check — and does NOT clear the enabled latch.
      - LIGADO only records the mode.

    Invalid values raise ValueError and are never written (mirrors set_source): the
    engine must not land in an unrecognized operator mode.

    Durable persistence (Phase D): Redis stays the fast/authoritative path for the
    LIVE mode (dispatch + card edit-lock). When ``session`` is supplied the mode is
    ALSO upserted into ``config_settings`` (key ``engine.mode``) so a Redis flush no
    longer resets the mode to LIGADO — :func:`get_mode` re-seeds Redis from that row.
    The caller commits; that commit drops the cached config overlay (upsert_config's
    after_commit listener). The Redis write happens FIRST (and the DESLIGADO side
    effects), so a DB hiccup can never lose the live mode. When ``session`` is None the behavior is exactly the
    Phase-C Redis-only path (unchanged).
    """
    if mode not in VALID_MODES:
        raise ValueError(
            f"invalid mode {mode!r}; expected one of {sorted(VALID_MODES)}"
        )
    redis.set(_MODE_KEY, mode)
    if mode == DESLIGADO:
        _mark_idle(redis)
        set_enabled(redis, False)
        # Hard off must also zero the producer inflight counter. get_status derives
        # sync_phase="syncing" while inflight > 0, so without this the badge stays
        # "Sincronizando" after OFF — either through drain lag or, if a producer leaked a
        # +1 by never reaching its finally, permanently. producer_done clamps at 0, so a
        # still-draining producer that finishes after OFF cannot underflow this reset.
        redis.set(_INFLIGHT_KEY, "0")
    if mode == LIGADO:
        # Both resume paths (POST /engine/start and POST /engine/mode LIGADO) call
        # set_mode(LIGADO) directly or via start — clearing the reason here covers
        # every resume without separate clear-on-resume code anywhere else.
        redis.delete(_PAUSE_REASON_KEY)
    if session is not None:
        upsert_config(session, {ENGINE_MODE_KEY: mode}, updated_by="engine")


def get_mode(redis: Any, *, session: Any = None) -> str:
    """Operator mode. Absent/corrupt → LIGADO (normal auto-collection).

    NB the default is LIGADO, NOT None — the OPPOSITE convention from
    get_depth/get_source. A fresh or flushed Redis must keep the engine runnable and
    the card edit-lock engaged; defaulting to anything else would silently halt every
    sweep (or unlock editing) on an empty key.

    Durable fallback (Phase D): on a Redis MISS (absent/corrupt key — e.g. after a
    flush) AND when ``session`` is supplied, the persisted ``config_settings`` row
    (key ``engine.mode``) is consulted; a valid value re-seeds Redis (self-healing
    fast path) and is returned. Without a ``session`` the Phase-C behavior is exact:
    a Redis miss returns the LIGADO default.
    """
    raw = _decode(redis.get(_MODE_KEY))
    if raw in VALID_MODES:
        return raw
    if session is not None:
        persisted = _read_persisted_mode(session)
        if persisted is not None:
            redis.set(_MODE_KEY, persisted)  # re-seed the fast path
            return persisted
    return LIGADO


def _read_persisted_mode(session: Any) -> str | None:
    """Return the durable ``engine.mode`` from config_settings, or None when absent/invalid.

    Reads the single row via the ORM; a missing row, a malformed value wrapper, or an
    out-of-contract mode all yield None so the caller falls back to the LIGADO default.
    Never raises on a read miss — durability must not make mode-reads fragile.
    """
    from brave.core.models import ConfigSetting  # lazy: same package, avoids import cost

    row = session.get(ConfigSetting, ENGINE_MODE_KEY)
    if row is None or not isinstance(row.value, dict):
        return None
    value = row.value.get("v")
    return value if value in VALID_MODES else None


def is_editing_unlocked(redis: Any, *, session: Any = None) -> bool:
    """True iff the card edit-lock is released — i.e. mode is PAUSADO or DESLIGADO.

    ``session`` is forwarded to :func:`get_mode` so a mode-read after a Redis flush
    self-heals from the durable ``config_settings`` row (Phase D). Omitting it keeps
    the exact Phase-C Redis-only behavior.
    """
    return get_mode(redis, session=session) in (PAUSADO, DESLIGADO)


def get_status(redis: Any, *, session: Any = None) -> dict[str, Any]:
    """Engine status snapshot for the dashboard.

    ``session`` (optional) is threaded only into the mode reads so a status poll
    after a Redis flush re-seeds the live mode from the durable ``config_settings``
    row (Phase D self-heal). Every other field stays Redis-only; callers that pass
    no session get byte-identical Phase-C behavior.

    ``sync_phase`` (BUG 6/7) is a DERIVED tri-state for the dashboard sync badge:
      - "syncing" while a run is active (state RUNNING), the operator-intent latch is
        set (is_enabled), OR any producer task is still in flight (inflight > 0) —
        the last keeps the badge syncing even after the orchestrator's dispatch loop
        has returned but its fanned-out producers are still landing rows (live kanban).
      - "synced"  once a run has finished draining (the last_run_ended marker == "1").
      - "idle"    otherwise (fresh/flushed base, never run since the marker was cleared).
    """
    state = get_state(redis)
    enabled = is_enabled(redis)
    run_ended = _decode(redis.get(_LAST_RUN_ENDED_KEY)) == "1"
    if state == RUNNING or enabled or _inflight(redis) > 0:
        sync_phase = "syncing"
    elif run_ended:
        sync_phase = "synced"
    else:
        sync_phase = "idle"
    return {
        "state": state,
        "current_uf": _decode(redis.get(_CURRENT_UF_KEY)) or None,
        "ufs_done": int(_decode(redis.get(_UFS_DONE_KEY)) or 0),
        "ufs_total": int(_decode(redis.get(_UFS_TOTAL_KEY)) or 0),
        "depth": get_depth(redis),
        "source": get_source(redis),
        "enabled": enabled,
        "mode": get_mode(redis, session=session),
        "editing_unlocked": is_editing_unlocked(redis, session=session),
        "sync_phase": sync_phase,
        "pause_reason": get_pause_reason(redis),
    }
