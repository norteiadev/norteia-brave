---
name: run-uf-sync
description: >-
  Run a UF sync through the Brave in production mode: pre-flight, the sweep
  monitor armed, TripAdvisor sweep, backfill gap check, describe,
  promote-bulk and the push to norteia-api, ending with the UF summary. A failure is fixed without stopping the sweep: capture, analyze, fix, review by another agent, PR, merge, pull, restart only what needs it. Use
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

On each `FALHA`, look it up in `references/events.md` (section **Sweep monitor failures**) and follow **Failures and code fixes** below. Do not pause the engine to fix: the run goes on, and the fix goes live without interrupting it. The monitor exits with the run: re-arm it for a resumed run, and whenever it expires (30 min) while the run is still going.

Use the seven stage monitors (`engine`, `funnel`, `logs`, `stack`, `ta-session`, `cost`, `api-ingest` in `scripts/monitors/`) only to dig into a `FALHA`, as a one-off, or in tmux for the user (`MONITOR_UF=<UF> scripts/monitors/tmux.sh`). Do not arm them as Monitors for a whole run: they print one line per change and flood the context.

## 3. Sweep

**Only with the user's explicit go-ahead** for each new UF. That go-ahead covers finishing the UF: resuming it after a blocking failure needs no new ask (see **Failures and code fixes**). A run the user paused needs their go-ahead again.

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

## Failures and code fixes

**Rule: a UF sweep must run to the end without interruption.** The next UFs (BA, MG, PR, RJ, RS, SP) are bigger and take hours, so a sweep that stops halfway costs a TA session, a long resume and the user's time. A failure gets fixed while the run goes on. Pausing the engine is not a step of the fix.

You are authorized to carry a fix all the way to `main` without asking:

1. **Capture the exception.** A `FALHA` line, a traceback in the logs, a poison row, a failed producer. Get the full traceback (`docker compose logs --since 10m worker | grep -B5 -A30 Traceback`) and the record or card it hit.
2. **Analyze.** Find the root cause in the code, not the symptom: reproduce it in a unit test when you can. Decide which case it is, step 9 depends on it: it **blocks** the run (the engine stopped or paused itself, the producer died), it **hits every card** while the run goes on, or it hits **one card** (quarantined, the run keeps going).
3. **Fix.** On a branch from `main` (`fix/…`), with a test that fails without the fix. Run `.venv/bin/pytest` and `.venv/bin/ruff`, never `uv run` (it rewrites `uv.lock`; if it changes, `git checkout uv.lock`).
4. **Code review by another agent.** Push the branch and hand it to a separate agent (Agent tool, `isolation: "worktree"`): it reviews the diff and reports findings. In the worktree it tests with `PYTHONPATH=$PWD <repo>/.venv/bin/pytest`, because the venv of the main checkout points at the main checkout. The branch stays checked out in the main checkout, so git refuses it there: the agent resets its own worktree branch to `origin/<branch>` and pushes with `git push origin HEAD:<branch>`. Run `git pull` in the main checkout after it pushes.
5. **Correction.** Apply the findings (the reviewer may push the fixes itself), re-run the tests. A finding nobody can fix blocks the merge: tell the user.
6. **PR.** `gh pr create --base main`.
7. **Merge.** `gh pr merge <n> --merge`. Remove the agent's worktree and its branch (`git worktree remove --force <path>`, `git branch -D worktree-agent-…`).
8. **Update local `main`.** `git checkout main && git pull`.
9. **Restart only what needs it.** The repo is mounted. The sweep runs in `worker`, never in `api` or `beat`:
   - `api` runs `uvicorn --reload`: it reloads itself on the pull, no restart needed.
   - `beat` does not reload: `docker compose restart beat` at any time. The schedule lives in Redis (celery-redbeat).
   - `worker` does not reload. `docker compose restart worker` sends SIGTERM, a warm shutdown: the sweep chunk in flight runs to its time budget (up to ~1 h, `stop_grace_period: 65m`), hands its continuation over to the queue and exits, and the new worker carries on with the fix. The run is not interrupted, but the command blocks until then: run it with `run_in_background`. Never `docker compose kill worker` or `restart -t <short>` while a run is going: SIGKILL strands the in-flight message (acks_late) for the 3 h `visibility_timeout` and the run stalls.
   - **Blocks** (the engine stopped or paused itself, the producer died): no sweep chunk is in flight, so the restart is quick. Restart the worker right after the merge and resume the **same** UF with `engine/start`. The user's go-ahead for that UF covers finishing it; a new UF still needs their go-ahead.
   - **Every card**: restart the worker right after the merge, in the background. The cards the old code quarantines until the hand-over come back with a re-sweep of that UF after `FIM run`.
   - **One card**: restart the worker after `FIM run … status=concluido`, before the describe (the describe runs in the worker too). A quarantined card has no Rio row, so a re-sweep of that UF retries it and skips the synced cards. An `ibge_unmatched` card is retried only when the fix bumps `IBGE_RESOLVER_VERSION`.
   - Verify that the restarted process sees the change (`docker compose exec -T worker /app/.venv/bin/python -c "import …"`). On 24/10, a stale worker ran the old Places gate behind a new endpoint.

Tell the user, in one short message per fix: what failed, the cause, the PR, and when the fix went live.
