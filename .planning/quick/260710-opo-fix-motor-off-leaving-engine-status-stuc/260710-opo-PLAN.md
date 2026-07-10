---
quick_id: 260710-opo
description: Fix motor-off leaving engine status stuck on "Sincronizando" by resetting the inflight producer counter on DESLIGADO
status: ready
---

# Quick Task 260710-opo — Motor OFF clears "Sincronizando"

## Problem

When the operator turns the motor OFF, the sync badge stays "Sincronizando" (syncing)
indefinitely.

Root cause: the OFF toggle routes through `POST /engine/mode DESLIGADO` →
`engine.set_mode(redis, DESLIGADO)` (`brave/core/engine.py:354-356`), which runs
`mark_idle` + `set_enabled(False)` but does **not** reset the producer inflight counter
`_INFLIGHT_KEY`. `get_status` (`brave/core/engine.py:440`) derives
`sync_phase == "syncing"` whenever `get_inflight(redis) > 0`. So after OFF the badge is
held ON solely by a non-zero inflight count:

- **Drain lag** — already-queued producers keep the counter > 0 until they self-halt at
  the next page boundary.
- **Permanent leak** — a producer that never reaches its `finally` (DB down at session
  acquire, worker OOM, dropped task) leaks +1 that only `start_run` ever clears — so OFF
  wedges "syncing" forever.

## Scope (locked with user)

RESET COUNTER ONLY. Do NOT add Celery revoke. Do NOT change the log lifecycle.

## Task 1 — Reset inflight on DESLIGADO + test

- **files:** `brave/core/engine.py`, `tests/unit/test_engine_state.py`
- **action:**
  - In `set_mode`'s `DESLIGADO` branch, after `mark_idle` + `set_enabled(False)`, add
    `redis.set(_INFLIGHT_KEY, "0")` so a hard off zeroes the producer counter. `decr_inflight`
    already clamps at 0, so still-draining producers hitting their `finally` after OFF cannot
    underflow. Update the `set_mode` docstring's DESLIGADO bullet to note the counter reset.
  - Add a unit test asserting `set_mode(DESLIGADO)` zeroes a stale inflight counter AND that
    `get_status` no longer reports `sync_phase == "syncing"` after OFF with a leaked count.
- **verify:** `.venv/bin/python -m pytest tests/unit/test_engine_state.py -q`
- **done:** new test passes; existing engine_state tests stay green.

## Non-goals

- No `/engine/stop` wiring, no Celery `revoke`, no producer halt-check changes.
- No dashboard changes, no log poller / buffer changes.
