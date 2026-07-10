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

This module is pure state — it performs no dispatch. The orchestrator task
(brave.tasks.pipeline.engine_sweep_run) reads `state` between UFs and breaks the
loop when it is no longer `running`, which is what makes Stop graceful. It also
reads `mode` and breaks when it is no longer `LIGADO`: PAUSADO/DESLIGADO stop new
fan-out (graceful drain) while releasing the card edit-lock.
"""

from __future__ import annotations

from typing import Any

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
_VALID_DEPTHS = frozenset({NASCENTE, NASCENTE_RIO, NASCENTE_RIO_MAR})

_STATE_KEY = "brave:engine:state"
_CURRENT_UF_KEY = "brave:engine:current_uf"
_UFS_DONE_KEY = "brave:engine:ufs_done"
_UFS_TOTAL_KEY = "brave:engine:ufs_total"
_DEPTH_KEY = "brave:engine:depth"
_SOURCE_KEY = "brave:engine:source"
_ENABLED_KEY = "brave:engine:enabled"
_MODE_KEY = "brave:engine:mode"
# Sync marker (BUG 6/7): "1" iff the most recent run finished draining. Cleared at
# run START (a fresh run is not "synced" yet) and set at run END (mark_run_ended, or
# — new producer-completes model — atomically inside maybe_complete when the LAST
# producer finishes). Drives get_status's derived "sync_phase" for the dashboard badge.
_LAST_RUN_ENDED_KEY = "brave:engine:last_run_ended"
# run_id of the durable runs_history row for the CURRENT run (set at engine /start so
# a producer's finally can finalize the row when it completes the run). Read-only here.
_RUN_ID_KEY = "brave:engine:run_id"
# Producer-completes lifecycle (live-kanban fix): the run stays RUNNING while any
# producer task is in flight and only flips to synced when the LAST producer finishes.
#   _INFLIGHT_KEY      count of dispatched producer tasks still running (>=0)
#   _DISPATCH_DONE_KEY "1" once the orchestrator's dispatch loop has fanned out every
#                      producer for this run (absent = still dispatching). A run may
#                      only complete AFTER dispatch is done AND inflight has drained.
_INFLIGHT_KEY = "brave:engine:producers_inflight"
_DISPATCH_DONE_KEY = "brave:engine:dispatch_done"

# Source selects which ingest lane the orchestrator dispatches:
#   default      — Google Places attraction lane (discover_atrativo_task; dormant by
#                  default — the Mtur destino seed is retired)
#   tripadvisor  — TripAdvisor lane (sweep_tripadvisor task, plan 11-03)
_VALID_SOURCES = frozenset({"default", "tripadvisor"})

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
_VALID_MODES = frozenset({LIGADO, PAUSADO, DESLIGADO})


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
    (scripts/ta_bulk_sweep.py — never went through start_run, so state stays IDLE)
    is not falsely halted; it still honors a painel PAUSADO/DESLIGADO.
    """
    return get_mode(redis) != LIGADO or get_state(redis) == STOPPING


def start_run(redis: Any, ufs_total: int) -> bool:
    """Mark the engine running for a fresh run.

    Returns False (no-op) if a run is already active — Start is idempotent and
    never stacks two orchestrators. Resets the progress counters.
    """
    if get_state(redis) in (RUNNING, STOPPING):
        return False
    redis.set(_STATE_KEY, RUNNING)
    redis.set(_ENABLED_KEY, "1")
    redis.set(_UFS_TOTAL_KEY, int(ufs_total))
    redis.set(_UFS_DONE_KEY, 0)
    redis.delete(_CURRENT_UF_KEY)
    redis.delete(_LAST_RUN_ENDED_KEY)  # a fresh run is not "synced" yet
    # Producer-completes lifecycle: a fresh run starts with zero producers in flight
    # and dispatch not yet done, so it cannot be spuriously "completed" by a stale key.
    redis.set(_INFLIGHT_KEY, "0")
    redis.delete(_DISPATCH_DONE_KEY)
    # Clear any stale run_id so a producer that completes this run before the API edge
    # re-writes brave:engine:run_id can never finalize a PREVIOUS run's runs_history row
    # (the /start edge sets it again immediately after this call).
    redis.delete(_RUN_ID_KEY)
    return True


def request_stop(redis: Any) -> bool:
    """Ask a running engine to stop after the current UF. Returns False if idle."""
    if get_state(redis) != RUNNING:
        return False
    redis.set(_STATE_KEY, STOPPING)
    return True


def mark_idle(redis: Any) -> None:
    """Orchestrator calls this when the loop exits (completed or drained)."""
    redis.set(_STATE_KEY, IDLE)
    redis.delete(_CURRENT_UF_KEY)


def mark_run_ended(redis: Any) -> None:
    """Set the sync marker: the most recent run finished draining (→ 'synced').

    Called from the orchestrator's finally block (engine_sweep_run) after the motor is
    turned OFF. Paired with start_run, which clears this marker so an in-flight run
    never reads as synced.
    """
    redis.set(_LAST_RUN_ENDED_KEY, "1")


def mark_uf_dispatched(redis: Any, uf: str) -> None:
    """Record that one UF was fanned out (for the progress feedback)."""
    redis.set(_CURRENT_UF_KEY, uf)
    redis.incr(_UFS_DONE_KEY)


# ---------------------------------------------------------------------------
# Producer-completes lifecycle (live-kanban fix)
# ---------------------------------------------------------------------------
#
# The orchestrator (engine_sweep_run) only *dispatches* producer tasks; those tasks
# run for minutes AFTER the dispatch loop returns. The old finally marked the run
# "synced" the moment dispatch finished — so the badge read synced while work was
# still landing. These primitives move completion to the LAST producer: the
# orchestrator increments the counter before each dispatch and latches dispatch_done
# in its finally; every producer decrements in its own finally; whoever brings the
# counter to zero (with dispatch already done) atomically claims completion.


def incr_inflight(redis: Any) -> int:
    """Increment the in-flight producer counter (called before each producer dispatch)."""
    return int(redis.incr(_INFLIGHT_KEY))


def decr_inflight(redis: Any) -> int:
    """Decrement the in-flight producer counter, clamped at zero.

    A best-effort producer finally may run more times than there were increments
    (retries, direct invocations, the standalone bulk path), so a negative counter is
    normalized back to 0 rather than allowed to underflow (which would wedge the run
    from ever completing).
    """
    n = int(redis.decr(_INFLIGHT_KEY))
    if n < 0:
        redis.set(_INFLIGHT_KEY, "0")
        n = 0
    return n


def get_inflight(redis: Any) -> int:
    """Current in-flight producer count. Absent/corrupt → 0."""
    return int(_decode(redis.get(_INFLIGHT_KEY)) or 0)


def set_dispatch_done(redis: Any, done: bool) -> None:
    """Latch (or clear) the 'orchestrator finished dispatching' flag ('1' / absent)."""
    if done:
        redis.set(_DISPATCH_DONE_KEY, "1")
    else:
        redis.delete(_DISPATCH_DONE_KEY)


def is_dispatch_done(redis: Any) -> bool:
    """True once the orchestrator's dispatch loop has fanned out every producer."""
    return _decode(redis.get(_DISPATCH_DONE_KEY)) == "1"


def maybe_complete(redis: Any) -> bool:
    """Complete the run iff dispatch is done and no producer is still in flight.

    RACE-SAFE single-winner: two producers can decrement the counter to zero and BOTH
    observe get_inflight()==0 concurrently. The atomic ``GETSET`` on the sync marker is
    the claim — it sets it to "1" and returns the OLD value in one round-trip, so
    exactly one caller sees the old value != "1" and performs the (idempotent) motor-off
    side effects + returns True. Every other racer (and every later caller) sees "1"
    already there and returns False. This mirrors set_mode(DESLIGADO)'s effects
    (mark_idle + enabled False + mode off) but stays REDIS-ONLY (no session=) so a DB
    hiccup can never break run completion, and does NOT import brave.tasks (D-18): the
    run_history finalize stays in the caller (pipeline.py).

    Returns True exactly once per run (the winning completion), False otherwise.
    """
    if get_inflight(redis) > 0 or not is_dispatch_done(redis):
        return False
    # Atomically CLAIM completion: set last_run_ended="1" and read the prior value.
    if _decode(redis.getset(_LAST_RUN_ENDED_KEY, "1")) == "1":
        return False  # another caller already completed this run
    mark_idle(redis)
    set_enabled(redis, False)
    redis.set(_MODE_KEY, DESLIGADO)  # redis-only DESLIGADO (no session side effects)
    return True


def set_depth(redis: Any, depth: str) -> None:
    """Persist the chosen pipeline depth. Rejects anything outside the contract.

    Invalid values raise ValueError and are never written — the engine must not
    silently spend on an unrecognized (possibly more expensive) reach. Kept
    orthogonal to start_run so lane (entity family) and depth (reach) stay
    independent; the API edge sets depth around start_run.
    """
    if depth not in _VALID_DEPTHS:
        raise ValueError(
            f"invalid depth {depth!r}; expected one of {sorted(_VALID_DEPTHS)}"
        )
    redis.set(_DEPTH_KEY, depth)


def get_depth(redis: Any) -> str | None:
    """Persisted depth, or None when absent/corrupt (unset → required at the edge)."""
    raw = _decode(redis.get(_DEPTH_KEY))
    return raw if raw in _VALID_DEPTHS else None


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
    When ``valid_sources`` is ``None`` the legacy in-kernel ``_VALID_SOURCES`` literal
    is used (back-compat for direct callers/tests). Mirrors set_depth otherwise.
    """
    allowed = _VALID_SOURCES if valid_sources is None else frozenset(valid_sources)
    if source not in allowed:
        raise ValueError(
            f"invalid source {source!r}; expected one of {sorted(allowed)}"
        )
    redis.set(_SOURCE_KEY, source)


def get_source(redis: Any) -> str | None:
    """Persisted source lane, or None when absent/corrupt (defaults to 'default' at /start)."""
    raw = _decode(redis.get(_SOURCE_KEY))
    return raw if raw in _VALID_SOURCES else None


def set_mode(redis: Any, mode: str, *, session: Any = None) -> None:
    """Persist the operator mode (Motor Pausado, phase C). Rejects unknown values.

    Mode is orthogonal to the runtime state (idle|running|stopping) and does NOT by
    itself drive state transitions — with one deliberate exception:

      - DESLIGADO is a hard off, so it ALSO returns the engine to idle (mark_idle),
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
    The snapshot cache is busted so the next effective-config read reflects the change.
    The Redis write happens FIRST (and the DESLIGADO side effects), so a DB hiccup can
    never lose the live mode. When ``session`` is None the behavior is exactly the
    Phase-C Redis-only path (unchanged).
    """
    if mode not in _VALID_MODES:
        raise ValueError(
            f"invalid mode {mode!r}; expected one of {sorted(_VALID_MODES)}"
        )
    redis.set(_MODE_KEY, mode)
    if mode == DESLIGADO:
        mark_idle(redis)
        set_enabled(redis, False)
        # Hard off must also zero the producer inflight counter. get_status derives
        # sync_phase="syncing" while get_inflight > 0, so without this the badge stays
        # "Sincronizando" after OFF — either through drain lag or, if a producer leaked a
        # +1 by never reaching its finally, permanently. decr_inflight clamps at 0, so a
        # still-draining producer that finishes after OFF cannot underflow this reset.
        redis.set(_INFLIGHT_KEY, "0")
    if session is not None:
        # Lazy import keeps brave.core.engine importable without brave.config.runtime
        # at module load (mirrors brave.core.dlq.service, which already depends on it).
        from brave.config.runtime import bust_config_snapshot, upsert_config

        upsert_config(session, {_ENGINE_MODE_CONFIG_KEY: mode}, updated_by="engine")
        bust_config_snapshot(redis)


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
    if raw in _VALID_MODES:
        return raw
    if session is not None:
        persisted = _read_persisted_mode(session)
        if persisted is not None:
            redis.set(_MODE_KEY, persisted)  # re-seed the fast path
            return persisted
    return LIGADO


# config_settings dotted key mirroring the Redis _MODE_KEY (durable store).
_ENGINE_MODE_CONFIG_KEY = "engine.mode"


def _read_persisted_mode(session: Any) -> str | None:
    """Return the durable ``engine.mode`` from config_settings, or None when absent/invalid.

    Reads the single row via the ORM; a missing row, a malformed value wrapper, or an
    out-of-contract mode all yield None so the caller falls back to the LIGADO default.
    Never raises on a read miss — durability must not make mode-reads fragile.
    """
    from brave.core.models import ConfigSetting  # lazy: same package, avoids import cost

    row = session.get(ConfigSetting, _ENGINE_MODE_CONFIG_KEY)
    if row is None or not isinstance(row.value, dict):
        return None
    value = row.value.get("v")
    return value if value in _VALID_MODES else None


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
        set (is_enabled), OR any producer task is still in flight (get_inflight > 0) —
        the last keeps the badge syncing even after the orchestrator's dispatch loop
        has returned but its fanned-out producers are still landing rows (live kanban).
      - "synced"  once a run has finished draining (the last_run_ended marker == "1").
      - "idle"    otherwise (fresh/flushed base, never run since the marker was cleared).
    """
    state = get_state(redis)
    enabled = is_enabled(redis)
    run_ended = _decode(redis.get(_LAST_RUN_ENDED_KEY)) == "1"
    if state == RUNNING or enabled or get_inflight(redis) > 0:
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
    }
