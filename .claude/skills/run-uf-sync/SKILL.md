---
name: run-uf-sync
description: >-
  Run a UF sync through the Brave in production mode: pre-flight, the sweep
  monitor armed, TripAdvisor sweep, backfill gap check, describe,
  promote-bulk and the push to norteia-api, ending with the UF summary. Code fixes go through a PR reviewed by another agent, merged and restarted (PR flow). Use
  whenever the user wants to sync, sweep, collect or "rodar" one or more UFs
  (e.g. "roda a sync de BA", "faz o sweep de PE e AL", "popula a API com RN",
  "próxima UF"), or to monitor/resume a UF run that is already going.
---

# Run a UF sync

One UF = sweep → backfill gap check → describe → promote → push, watched by the quiet sweep monitor `scripts/monitors/sweep.sh`. The detailed operations manual is `docs/RUNBOOK-EXECUCAO.md`; this skill is the order of operations and the done-criteria. Scripts below are relative to this skill's folder; run them from the repo root.

**Reference baseline (SE, 19/10/2026, 288 atrativos):** sweep 24 min (~2.5 min per TA page of 30 cards), Places US$0.048 per atrativo, describe ~US$0.002 per atrativo and ~12 s per chunk of 25, 84% described, 10% reach score 65. Use it to size a UF before starting and to spot an outlier while it runs.

## 1. Pre-flight

```bash
.claude/skills/run-uf-sync/scripts/preflight.sh
```

Done when it prints `PREFLIGHT PASSED`. A `FAIL` stops the sync: fix it via `references/events.md` (TA session, token) and re-run. Read the `WARN` lines to the user, especially Places credit: with ~US$0.05 per atrativo, check the remaining credit covers the UF.

## 2. Arm the monitor

Arm **one** Monitor, `timeout_ms: 1800000`, before starting the run:

| description | command |
|---|---|
| Brave sweep <UF> | `cd <repo> && scripts/monitors/sweep.sh` |

It stays quiet on purpose, to keep the context small. It prints a `RELATÓRIO` line every 10 min (`MONITOR_REPORT`, in seconds) and one when the run ends, then exits:

```
RELATÓRIO run=1a2b3c4d running · SC pág 81/134 · atrativos 1781 (+320) · destinos 120 (+12) · falhas 0
```

`pág p/x` is the TA listing size, read from `totalResults` on the first page the producer fetches, before any card is processed. A resumed run shows the true total too. The other line it prints is `FALHA <what>`, once per new failure: engine pause reason, beat errors, a failed producer, a dead TA session, a stopped service, the norteia-api down, new poison rows, or error lines in the logs (at most one aggregated line per 5 min).

On each `FALHA`, look it up in `references/events.md` (section **Sweep monitor failures**) and act. If the fix needs code, config or a new session, pause the engine first: `scripts/api.sh POST /api/v1/engine/mode '{"mode":"PAUSADO"}'`. Then tell the user what failed and what you changed. A pause ends the run as `parcial`, and resuming means a new `engine/start`. **Ask the user before resuming** (no sweep without their go-ahead). Already-synced cards are skipped and the per-UF sweep picks up at its page. The monitor exits with the run: re-arm it for the resumed run, and whenever it expires (30 min) while the run is still going.

Use the seven stage monitors (`engine`, `funnel`, `logs`, `stack`, `ta-session`, `cost`, `api-ingest` in `scripts/monitors/`) only to dig into a `FALHA`, as a one-off, or in tmux for the user (`MONITOR_UF=<UF> scripts/monitors/tmux.sh`). Do not arm them as Monitors for a whole run: they print one line per change and flood the context.

## 3. Sweep

**Only with the user's explicit go-ahead.** That covers a new UF and resuming a paused run.

```bash
.claude/skills/run-uf-sync/scripts/api.sh POST /api/v1/engine/start '{"source":"tripadvisor","depth":"nascente_rio","ufs":["<UF>"]}'
```

Several UFs go in the same `ufs` list. Add `"max_atrativos_per_uf": N` only when the user asks for a capped test. Done when the sweep monitor prints `FIM run … status=concluido`. `parcial` means it stopped early: read the `FALHA` lines (or run `scripts/monitors/logs.sh` for a minute) before going on.

## 4. Backfill gap check

```bash
docker compose exec -T postgres psql -U brave -d norteia_brave -Atc "select count(*) from rio_records where uf='<UF>' and canonical_key like 'tripadvisor:%' and routing<>'descarte' and normalized->>'ta_backfilled_at' is null"
```

Done when it returns 0. Otherwise run `scripts/api.sh POST /api/v1/tripadvisor/backfill`, wait for `ta_backfill_batch outcome=done` and the `ta_backfill` run `concluido`, and check again. Records without that stamp are invisible to describe.

## 5. Describe

```bash
.claude/skills/run-uf-sync/scripts/api.sh POST /api/v1/engine/start '{"action":"describe","ufs":["<UF>"]}'
```

Watch it with `MONITOR_SOURCE=descricao scripts/monitors/sweep.sh` (same reports and failures). Done when it prints `FIM run … status=concluido`. A 409 means a description flag or the search key is off: pre-flight shows which.

## 6. Promote

Dry-run first and show the numbers to the user:

```bash
.claude/skills/run-uf-sync/scripts/api.sh POST /api/v1/atrativos/promote-bulk '{"uf":"<UF>","require_description":true,"limit":200,"dry_run":true}'
```

Leave `min_score` out: it defaults to `threshold_mar` minus the human-validation weight (15), the lowest score promotion can still lift into Mar — the dry-run echoes it. The cut is moved with `score.threshold_mar` on the Painel, never with a `min_score` below that floor (those rows are only held). Pass `min_score` only when the user asks for a stricter cut. Then run it with `"dry_run":false`, repeating while `remaining > 0`. Done when `promoted` equals the dry-run `would_promote` and `failed` is empty; report any `held` with its reason.

## 7. Push check

Done when `select count(*) filter (where pushed_at is null), count(*) filter (where push_error is not null) from mar_records` returns `0|0` and the norteia-api `attractions` count (one-off: `timeout 20 scripts/monitors/api-ingest.sh`) rose by the number promoted (destinations rise by the distinct parent municípios). Pushes take ~1 s each; a `pending` that stays above 0 for more than a few minutes, or any `REJECTED`, is an action row in `references/events.md`.

## 8. Summary

```bash
.claude/skills/run-uf-sync/scripts/uf_summary.sh <UF>
```

Report to the user from its output: run durations, atrativos collected / described / promoted / pushed, gate and DLQ breakdown, poison, and cost (Places today and credit, LLM, searches), compared with the SE baseline. When the user asks for an HTML report, the monitor outputs (the Monitor tasks' output files) and this summary are the data; the SE report (https://claude.ai/artifact/31YSP9mKYtEhKmxQnZyD3u) is the model to follow.

## Code changes (PR flow)

When a sync needs a code fix, or the user asks for a change, you are authorized to open the PR and take it to `main` without asking. A sweep still needs the user's go-ahead.

1. **Branch and PR.** Branch from `main` (`fix/…` or `feat/…`), commit, push, `gh pr create`.
2. **Review by another agent.** Hand the PR to a separate agent (Agent tool) to review the diff, fix what it finds, run the tests and push. Use `.venv/bin/pytest` and `.venv/bin/ruff`, never `uv run`, which rewrites `uv.lock`; if `uv.lock` changes anyway, `git checkout uv.lock`. Read its report before merging. A finding it could not fix blocks the merge: tell the user.
3. **Merge.** `gh pr merge <n> --merge`, then `git checkout main && git pull`.
4. **Restart what runs the code.** The repo is mounted in the containers, but the processes do not reload. Run `docker compose restart worker api beat` only with the engine PAUSADO or DESLIGADO: pause it first if a run is going, and ask the user before resuming the sweep.
5. **Verify.** Check that the restarted processes see the change before using it (`docker compose exec -T worker /app/.venv/bin/python -c "import …"`, `api.sh GET /api/v1/engine/status`). On 24/10, a stale worker ran the old Places gate behind a new endpoint.
