---
quick_id: 260710-opo
description: Reset producer inflight counter on motor OFF so the sync badge leaves "Sincronizando"
status: complete
date: 2026-07-10
commit: 89c0cce
---

# Quick Task 260710-opo — Summary

## What changed

`brave/core/engine.py` — `set_mode`'s `DESLIGADO` branch now also runs
`redis.set(_INFLIGHT_KEY, "0")` (alongside the existing `mark_idle` + `set_enabled(False)`).
Docstring updated to reflect the third effect.

`tests/unit/test_engine_state.py` — added
`test_set_mode_desligado_zeroes_inflight_and_clears_syncing`: seeds a stale inflight
count during a run, calls `set_mode(DESLIGADO)`, asserts the counter is 0 and
`get_status().sync_phase != "syncing"`.

## Why

The OFF toggle routes through `POST /engine/mode DESLIGADO` → `set_mode(DESLIGADO)`, which
never reset the producer inflight counter. `get_status` derives `sync_phase == "syncing"`
whenever `get_inflight > 0`, so after OFF the badge stayed "Sincronizando" — through drain
lag, or permanently if a producer leaked a `+1` by never reaching its `finally`
(DB down at session acquire, worker OOM, dropped task). Only `start_run` previously cleared
the counter. Now OFF clears it too. `decr_inflight` clamps at 0, so a still-draining
producer finishing after OFF cannot underflow the reset.

## Scope honored

Reset-counter-only. No Celery `revoke`, no `/engine/stop` wiring, no dashboard or log
lifecycle changes (all explicitly out of scope per the user).

## Known residual (out of scope, by design)

- Logs keep streaming after OFF: the log buffer/poller is a general structlog sink
  independent of engine state, and background beats (e.g. `ta_keepalive`) keep appending.
  User chose "leave logs as-is".
- Already-queued producers still drain (self-halt at their next page boundary); the badge
  now goes honest immediately, but a few rows may still land during the drain.

## Verification

`.venv/bin/python -m pytest tests/unit/test_engine_state.py` — all pass (incl. new test).
`test_engine_mode_persist.py`, `test_engine_sweep_mode.py`, `test_engine_latch.py` — all pass.
