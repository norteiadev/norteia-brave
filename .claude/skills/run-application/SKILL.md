---
name: run-application
description: Start/stop/restart the full norteia-brave local stack — postgres + redis + FastAPI API + Celery worker + Celery beat + Next.js dashboard. Use whenever the user wants to "run the app", "start the application", "sobe a aplicação", "restart the services", "stop everything", or check whether the stack is up. DEFAULT is Docker (`docker compose up`, zero host deps); host-native via scripts/app.sh is the fallback. Verifies health after starting.
---

# Run the norteia-brave application

**Default: Docker.** Run the stack with `docker compose` unless the user explicitly asks for
the host-native path. Docker needs zero host deps (no local Python/Node/Postgres/Redis) and is
how the team runs it. `scripts/app.sh` (host-native) is the fallback for faster native debugging.

## Docker (default)

`docker compose up` runs all seven services: postgres (pgvector) + redis + a one-shot `migrate`
(alembic + config seed) + api (:8000) + worker + beat + dashboard (:3000). Live source is
bind-mounted with hot-reload; deps live in named volumes.

```bash
docker compose up            # foreground (whole stack; migrate runs first)
docker compose up -d         # detached
docker compose ps            # status
docker compose logs -f api   # follow one service (api|worker|beat|dashboard|migrate)
docker compose down          # stop (keep data);  down -v also wipes DB/redis volumes
docker compose build         # rebuild images after Dockerfile / lockfile changes
```

How to run it (for the agent):
1. `docker compose up -d` from the repo root.
2. **Verify health — don't trust "Started".** Poll until ready (~40s):
   ```bash
   curl -s http://localhost:8000/api/v1/health              # → {"status":"ok","db":"ok","redis":"ok"}
   curl -s -o /dev/null -w '%{http_code}' http://localhost:3000   # → 307 (→ /painel)
   docker compose ps -a migrate --format '{{.Status}}'      # → Exited (0)
   ```
   For worker/beat confirm boot: `docker compose logs worker` → `celery@… ready.`
3. Report the URLs (API `http://localhost:8000`, dashboard `http://localhost:3000/painel`).
   On failure: `docker compose logs <svc>`.

Notes:
- **Real external calls are OFF by default** (`RUN_REAL_EXTERNALS=0`, no keys needed). Enable
  per run: `RUN_REAL_EXTERNALS=1 OPENROUTER_API_KEY=… ANTHROPIC_API_KEY=… docker compose up`.
- **Port conflicts** (host already runs Postgres/Redis/api/dashboard): override with
  `BRAVE_PG_PORT` / `BRAVE_REDIS_PORT` / `BRAVE_API_PORT` / `BRAVE_DASH_PORT`
  (e.g. `BRAVE_PG_PORT=5433 BRAVE_API_PORT=8001 docker compose up`). Internal service
  networking is unchanged — only host mappings move.
- **Reset the DB** (cold start): `docker compose exec api /app/.venv/bin/python .claude/skills/reset-brave-db/scripts/reset_db.py --yes`.
- Engine mode comes up `DESLIGADO` on a fresh base — set LIGADO in `/painel` to collect.
  Atrativos need a destinos/mTur seed sweep first, else they DLQ as `parent_destino_absent`.
- DB/redis URLs are fixed to the compose service names inside containers — the host `.env`'s
  `localhost` URLs are intentionally NOT used there (secrets/keys still come from `.env`).

## Host-native fallback (scripts/app.sh)

Only when the user explicitly wants it (needs Python/uv + Node/bun + Postgres + Redis on the
host). Drive everything through `scripts/app.sh` — never launch services ad-hoc (untracked,
port conflicts). PIDs/logs under `.run/` (gitignored); env from `./.env`.

```bash
scripts/app.sh start [service...]     # default: all four (api worker beat dashboard)
scripts/app.sh stop  [service...]     # also reaps orphan/ad-hoc processes by pattern
scripts/app.sh restart [service...]
scripts/app.sh status
scripts/app.sh logs <service> [-f]
```

Verify the same way (health endpoint + logs, not just pids). Preflight warns if Redis/Postgres
aren't reachable — start them (`brew services start postgresql@16 redis`) first.
Node 22 is the dashboard target; Node ≥26 breaks jsdom localStorage in tests (polyfilled in
`dashboard/vitest.setup.ts`, but prefer node 22 locally).
