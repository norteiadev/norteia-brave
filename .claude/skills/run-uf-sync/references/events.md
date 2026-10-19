# Monitor events → action

Read the line, find its row, act. Rows marked **benign** need no action; say so to the user in one line and keep watching.

## Benign (expected noise)

| Event | Why it is benign |
|---|---|
| `ta_paginated_gql_page ... page=1 offset=0` on a different `ForkPoolWorker` every 10 min | The `ta-keepalive` beat pinging MG to slide the TA session TTL. The sweep's own pages carry increasing `offset`. |
| `engine_uf_dispatched depth=nascente_rio_mar ... source=default` during a describe run | Internal labels of the describe action. `runs_history` records it as `descricao`. |
| `engine/status` keeps `source=tripadvisor depth=nascente_rio` during describe/backfill | Describe/backfill do not overwrite those Redis keys. |
| `[stack] api/worker/beat: NOT RUNNING` → `running` within ~30 s | A planned restart (`docker compose restart worker beat` or `up -d`). |
| `norteia-api: health=502` / `norteia_api_down` for < 1 min | A DEV deploy restarting the API container. Repush (every 15 min) recovers any skipped push. |
| funnel `rio` grows past the TA card count | Parent destinos `ibge:<UF>:<ibge>` are synthesized per município. They stay in DLQ (score 38) and reach the API inside the attraction push. |
| `places_enrich_hard_descarte` | Google marks the place CLOSED_PERMANENTLY → `descarte` (`closed_place`). Correct verdict. |
| DLQ reason `closed_temporarily`, `descricao_nao_fundamentada`, `municipio_nao_confirmado` | Liveness and description gates holding a record for steward review. |
| `describe_uf` keeps chaining after the expected chunk count | Records refused by a gate earlier are retried while `descricao_attempts < 3`. Cost ~US$0.002 each. |

## Act on these

| Event | Cause | Action |
|---|---|---|
| `sweep_tripadvisor_session_fail_fast`, engine → DESLIGADO | TA 403/captcha: session dead | Ask the user for a fresh listing cURL (see **TA session** below), inject, restart the sweep. Already-ingested cards are skipped. |
| `PersistedQueryNotFound`, `ta_keepalive_error error_type=SourceSchemaError`, inject returns 503 `canary_unverified` | TA rotated the listing qid | Ask the user for a listing cURL; take the `preRegisteredQueryId` of the batch item whose variables carry `request.routeParameters.pagee`; set `BRAVE_TA_QUERY_ID_OVERRIDE={"listing":"<qid>"}` in `.env`; `docker compose up -d api worker beat`; inject the session. Then update `_LISTING_QID_GQL` in `brave/domains/tripadvisor/client.py` in a PR. |
| `sweep_tripadvisor_rate_limited` | TA 429 | Nothing: the task re-enqueues itself with `Retry-After` (max 5 without progress). Escalate only if it repeats with no new pages. |
| `PAUSE_REASON {reason: daily_budget}` | Cost guard of the UTC day | Tell the user. Either wait for the UTC day to turn (beat resumes) or the user raises the cap. |
| `PAUSE_REASON {reason: provider_balance}` / `task_provider_balance` | A provider's credit is exhausted (Places key US$300) | Stop. The user swaps `BRAVE_PLACES_API_KEY`, then `docker compose up -d api worker beat`, then resume. |
| `places ... credit at 80%+` (cost monitor) | Places key near its US$300 credit | Warn the user before starting another UF; ~US$0.05 per atrativo. |
| `publish_rejected status=4xx` / funnel `REJECTED>0` / `last push_error` | norteia-api refused the payload (contract) | Read `push_error`; it is a contract bug, not transient. Report it; repush happens only after 24 h or via `POST /api/v1/mar/repush`. |
| api-ingest `ingest_probe=401` | `brave-ingest` Sanctum token expired (30-day TTL) | Renew (see **Token** below). |
| `laravel: ... ERROR` | norteia-api raised during ingest | Read it. A 1062 duplicate on `destinations` should no longer happen (api#105); anything else is an API bug to report. |
| funnel `poison_quarantine` rising | A card failed parsing/validation | `scripts/api.sh GET /api/v1/failures`. Does not block the run. |
| `[stack] docker VM: ... LOW` / `CRITICAL` or swap growing | Docker VM memory pressure | Tell the user: stop other ddev projects, or raise Docker Desktop memory (restarts every container, other projects included, so it is the user's call). |
| `[stack] ... restarts=N` growing | Crash loop / OOM | `docker compose logs --tail 100 <svc>`. |
| summary `sem_backfill > 0` after a sweep | TA part of some cards incomplete; describe skips them | `scripts/api.sh POST /api/v1/tripadvisor/backfill`, wait for `ta_backfill_batch outcome=done`, re-run describe. |
| `Traceback`, `WorkerLostError`, `TimeLimitExceeded` | Worker failure | Read the surrounding log; the sweep re-enqueues on its own time budget (~55 min). Report anything else. |

## TA session

The user captures it (only they can): Chrome logged in on TripAdvisor → the UF's attractions listing → DevTools › Network (clear) → click **next page** → Cmd+F `pagee` → the `POST /data/graphql/ids` whose payload has `routeParameters.pagee` → Copy as cURL (bash). Save it to `scripts/single_attraction/ta_session.curl` (gitignored), then:

```bash
set -a; . ./.env; set +a
.venv/bin/python scripts/ta_bootstrap.py --curl scripts/single_attraction/ta_session.curl --endpoint http://localhost:8000
```

`Session injected — canary result: ready` = done. The first-page load batch of the listing is telemetry only and fails the canary.

## Token

```bash
ssh norteia-dev 'docker exec dev-norteia-api php artisan tinker --execute="echo \App\Models\User::find(1)->createToken(\"brave-ingest\", [\"territorial:ingest\"])->plainTextToken;"'
```

Write it QUOTED into `.env` as `BRAVE_NORTEIA_API_SERVICE_TOKEN="<id|secret>"` without printing it, then `docker compose up -d api worker beat`.
