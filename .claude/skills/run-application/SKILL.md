---
name: run-application
description: Start/stop/restart the full norteia-brave local stack — FastAPI API, Celery worker, Celery beat, and the Next.js dashboard. Use whenever the user wants to "run the app", "start the application", "sobe a aplicação", "restart the services", "stop everything", or check whether the stack is up. Wraps scripts/app.sh (the single source of truth) and verifies health after starting.
---

# Run the norteia-brave application

The whole local stack is managed by **`scripts/app.sh`** — always drive it through that
script, never launch the services ad-hoc (ad-hoc processes aren't tracked and cause port
conflicts). Postgres + Redis are **external dependencies** the script only checks; they must
already be running (Homebrew or Docker).

Services managed: `api` (uvicorn `brave.api.main:app`, :8000) · `worker` (Celery) ·
`beat` (Celery beat / RedBeat) · `dashboard` (Next.js dev, :3000). PIDs + logs live under
`.run/` (gitignored). Env is loaded from `./.env`.

## Commands

```bash
scripts/app.sh start           # start all four (default)
scripts/app.sh start api worker
scripts/app.sh stop            # stop all (also reaps orphan/ad-hoc processes by pattern)
scripts/app.sh restart
scripts/app.sh status          # up/down + pid + port per service
scripts/app.sh logs worker     # last 60 lines;  add -f to follow
```

## How to run it (for the agent)

1. Run `scripts/app.sh start` from the repo root.
2. **Verify health — don't trust pids alone.** Poll until ready (up to ~40s):
   ```bash
   curl -s http://127.0.0.1:8000/api/v1/health        # → {"status":"ok","db":"ok","redis":"ok"}
   curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:3000   # → 307 (redirects to /painel)
   ```
   For worker/beat, confirm boot in the logs: `celery@… ready.` in `.run/logs/worker.log`
   and `beat: Starting...` in `.run/logs/beat.log`.
3. Report the URLs (API `http://127.0.0.1:8000`, dashboard `http://localhost:3000/painel`)
   and each service's status. If a service failed, `tail .run/logs/<svc>.log` for the cause.

## Notes

- **Preflight** warns if Redis/Postgres aren't reachable — if so, start them
  (`brew services start redis postgresql@16`, or the project's Docker) before retrying.
- On a **fresh DB** (after `reset-brave-db`), the engine mode is `DESLIGADO` (idle) — beat
  fires but sweeps gate off until the operator sets LIGADO in `/painel`. Atrativos need a
  destinos/mTur seed sweep first, else they DLQ as `parent_destino_absent` (expected cold start).
- The **dashboard test suite needs Node 22** (repo target); Node ≥26 breaks jsdom localStorage
  (polyfilled in `dashboard/vitest.setup.ts`, but prefer node 22 locally).
- Overridable ports: `BRAVE_API_PORT`, `BRAVE_DASH_PORT`; host: `BRAVE_API_HOST`.
