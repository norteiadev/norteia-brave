---
name: run-uf-sync
description: >-
  Run a UF sync through the Brave in production mode: pre-flight, the seven
  stage monitors armed, TripAdvisor sweep, backfill gap check, describe,
  promote-bulk and the push to norteia-api, ending with the UF summary. Use
  whenever the user wants to sync, sweep, collect or "rodar" one or more UFs
  (e.g. "roda a sync de BA", "faz o sweep de PE e AL", "popula a API com RN",
  "próxima UF"), or to monitor/resume a UF run that is already going.
---

# Run a UF sync

One UF = sweep → backfill gap check → describe → promote → push, watched end to end by the seven monitors in `scripts/monitors/`. The detailed operations manual is `docs/RUNBOOK-EXECUCAO.md`; this skill is the order of operations and the done-criteria. Scripts below are relative to this skill's folder; run them from the repo root.

**Reference baseline (SE, 19/10/2026, 288 atrativos):** sweep 24 min (~2.5 min per TA page of 30 cards), Places US$0.048 per atrativo, describe ~US$0.002 per atrativo and ~12 s per chunk of 25, 84% described, 10% reach score 65. Use it to size a UF before starting and to spot an outlier while it runs.

## 1. Pre-flight

```bash
.claude/skills/run-uf-sync/scripts/preflight.sh
```

Done when it prints `PREFLIGHT PASSED`. A `FAIL` stops the sync: fix it via `references/events.md` (TA session, token) and re-run. Read the `WARN` lines to the user, especially Places credit: with ~US$0.05 per atrativo, check the remaining credit covers the UF.

## 2. Arm the monitors

Arm all seven with the Monitor tool, `timeout_ms: 1800000`, in one message. Replace `<UF>` (for several UFs, drop `MONITOR_UF=` so the funnel counts all):

| description | command |
|---|---|
| Brave engine lifecycle | `cd <repo> && scripts/monitors/engine.sh` |
| Brave funnel <UF> | `cd <repo> && MONITOR_UF=<UF> MONITOR_INTERVAL=60 scripts/monitors/funnel.sh` |
| Brave stage + failure logs | `cd <repo> && scripts/monitors/logs.sh` |
| Brave stack + Docker VM | `cd <repo> && scripts/monitors/stack.sh` |
| TripAdvisor session | `cd <repo> && scripts/monitors/ta-session.sh` |
| Brave spend | `cd <repo> && MONITOR_INTERVAL=60 scripts/monitors/cost.sh` |
| norteia-api ingest | `cd <repo> && scripts/monitors/api-ingest.sh` |

Each prints a baseline burst, then one line per change. A monitor expires after 30 min: re-arm it while a run of this sync is still active; let it lapse once the sync is finished. On each event, match it in `references/events.md`: benign rows get one short line to the user, action rows get the action. The user can watch the same scripts in tmux with `MONITOR_UF=<UF> scripts/monitors/tmux.sh`.

## 3. Sweep

```bash
.claude/skills/run-uf-sync/scripts/api.sh POST /api/v1/engine/start '{"source":"tripadvisor","depth":"nascente_rio","ufs":["<UF>"]}'
```

Several UFs go in the same `ufs` list. Add `"max_atrativos_per_uf": N` only when the user asks for a capped test. Done when the engine monitor shows `producers inflight=0 ... last_run_ended=1` and the `runs_history` line reads `concluido`. `parcial` means it stopped early: find why in the logs monitor before going on.

## 4. Backfill gap check

```bash
docker compose exec -T postgres psql -U brave -d norteia_brave -Atc "select count(*) from rio_records where uf='<UF>' and canonical_key like 'tripadvisor:%' and routing<>'descarte' and normalized->>'ta_backfilled_at' is null"
```

Done when it returns 0. Otherwise run `scripts/api.sh POST /api/v1/tripadvisor/backfill`, wait for `ta_backfill_batch outcome=done` and the `ta_backfill` run `concluido`, and check again. Records without that stamp are invisible to describe.

## 5. Describe

```bash
.claude/skills/run-uf-sync/scripts/api.sh POST /api/v1/engine/start '{"action":"describe","ufs":["<UF>"]}'
```

Done when the `descricao` run reads `concluido` and the funnel's `described` stops moving. A 409 means a description flag or the search key is off: pre-flight shows which.

## 6. Promote

Dry-run first and show the numbers to the user:

```bash
.claude/skills/run-uf-sync/scripts/api.sh POST /api/v1/atrativos/promote-bulk '{"uf":"<UF>","min_score":65,"require_description":true,"limit":200,"dry_run":true}'
```

`min_score` 65 is the runbook default; use the user's value when they give one. Promotion adds the human-validation criterion (weight 15), which is what lifts a 65 over the Mar threshold of 80. Then run it with `"dry_run":false`, repeating while `remaining > 0`. Done when `promoted` equals the dry-run `would_promote` and `failed` is empty; report any `held` with its reason.

## 7. Push check

Done when the funnel shows `mar ... pending=0 REJECTED=0` and the api-ingest `attractions` count rose by the number promoted (destinations rise by the distinct parent municípios). Pushes take ~1 s each; a `pending` that stays above 0 for more than a few minutes, or any `REJECTED`, is an action row in `references/events.md`.

## 8. Summary

```bash
.claude/skills/run-uf-sync/scripts/uf_summary.sh <UF>
```

Report to the user from its output: run durations, atrativos collected / described / promoted / pushed, gate and DLQ breakdown, poison, and cost (Places today and credit, LLM, searches), compared with the SE baseline. When the user asks for an HTML report, the monitor outputs (the Monitor tasks' output files) and this summary are the data; the SE report (https://claude.ai/artifact/31YSP9mKYtEhKmxQnZyD3u) is the model to follow.
