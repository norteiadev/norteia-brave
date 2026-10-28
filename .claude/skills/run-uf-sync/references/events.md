# Monitor events → action

## Sweep monitor failures

`scripts/monitors/sweep.sh` prints `FALHA <what>`. The run goes on while you fix. Follow **Failures and code fixes** in `SKILL.md` (capture → analyze → fix → review → correction → PR → merge → update `main` → restart only what needs it). A fix to the sweep path is loaded with a short pause, a worker restart and an immediate resume of the same UF; any other fix needs no pause.

| `FALHA …` | Action |
|---|---|
| `motor pausado: {reason: …}` | The engine paused itself: `daily_budget` / `provider_balance` rows below. A pause the user asked for needs nothing. |
| `sessão TA ausente` | Session dead: **TA session** below (reinject the saved cURL with `scripts/ta_bootstrap.py` first; ask for a new one only if the canary fails). |
| `internet caiu (desde …)` (a notice, not a `FALHA`) | Nothing to do: every TA request waits (polling every 15 s, the session TTL kept alive) and resends once the internet is back, on the same card. The monitor prints `internet voltou` when it does. Hours offline only cycle the producer through its time-budget hand-overs at the same page. |
| `páginas TA bloqueadas (403)` (a notice) | DataDome 403s the attraction pages while the listing answers: the sweep goes on, placing cards by name without `ta_backfilled_at`, and retries one page every 5 min (`páginas TA voltaram` when one answers). Ask the user for a fresh listing cURL now; after the sweep, the **Backfill gap check** (step 4) redoes those pages. A 403 on the listing itself still stops the sweep (`sessão TA ausente`). |
| `produtor falhou` | Blocking. Get the traceback (`docker compose logs --since 10m worker`), fix through **Failures and code fixes**, restart the worker and resume the same UF. |
| `serviço parado: …` | `docker compose up -d <svc>`. A restart you planned is benign. |
| `norteia-api fora` | Benign for < 1 min (a DEV deploy). Longer: tell the user. Pushes wait for repush. |
| `quarentena +N (última: …ibge_unmatched…)` | Agency or shop names and cards TA places in the wrong UF: expected, a handful per UF. Dozens point to the resolver: fix it while the run goes on, bumping `IBGE_RESOLVER_VERSION` so a re-sweep retries them, then pause, restart the worker and resume. |
| `logs: N linha(s) de erro; 1ª: …` | Read the first line. Match it in the tables below. A traceback that repeats on every card: fix it now, then pause, restart the worker and resume (sweep path in **Failures and code fixes**). |
| `api: engine/status inacessível` | API container down or bearer wrong: `docker compose ps`, `scripts/monitors/stack.sh`. |
| `postgres: runs_history ilegível` | Postgres (or `docker compose exec`) unreachable. The monitor keeps watching instead of ending: `docker compose ps postgres`. |

The tables below match lines from the seven stage monitors, used to dig into a `FALHA`.

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
| `places_enrich_hard_descarte reason=…` | Google marks the place closed or a business on a non-attraction card → `descarte` (`closed_place` / `nao_atrativo`). Correct verdict. |
| `places_enrich_dlq_review reason=…` | Google says closed/business but TA files it as a place to visit → DLQ for a steward (`google_tipo_negocio` / `google_fechado_revisao`). |
| DLQ reason `closed_temporarily`, `descricao_nao_fundamentada`, `municipio_nao_confirmado` | Liveness and description gates holding a record for steward review. |
| `describe_uf` keeps chaining after the expected chunk count | Records refused by a gate earlier are retried while `descricao_attempts < 3`. Cost ~US$0.002 each. |

## Act on these

| Event | Cause | Action |
|---|---|---|
| `sweep_tripadvisor_session_fail_fast`, engine → DESLIGADO | TA 403/captcha: session dead | Ask the user for a fresh listing cURL (see **TA session** below), inject, restart the sweep. Already-ingested cards are skipped. |
| `PersistedQueryNotFound`, `ta_keepalive_error error_type=SourceSchemaError`, inject returns 503 `canary_unverified` | TA rotated the listing qid | Ask the user for a listing cURL; take the `preRegisteredQueryId` of the batch item whose variables carry `request.routeParameters.pagee`; set `BRAVE_TA_QUERY_ID_OVERRIDE={"listing":"<qid>"}` in `.env`; `docker compose up -d api worker beat`; inject the session. Then update `_LISTING_QID_GQL` in `brave/domains/tripadvisor/client.py` in a PR. |
| `sweep_tripadvisor_rate_limited` | TA 429 | Nothing: the task re-enqueues itself with `Retry-After` (max 5 without progress). Escalate only if it repeats with no new pages. |
| `PAUSE_REASON {reason: daily_budget}` | Cost guard of the UTC day | Tell the user. Either wait for the UTC day to turn (beat resumes) or the user raises the cap. |
| `PAUSE_REASON {reason: provider_balance}` / `task_provider_balance` | A provider's credit is exhausted (Places key `total_cap_usd`, or Apify `APIFY_TOTAL_USD_CAP`) | Stop. Places: the user swaps `BRAVE_PLACES_API_KEY`. Apify: the user recharges the account or swaps `BRAVE_APIFY_API_KEY` and raises `APIFY_TOTAL_USD_CAP` in `.env`. Then `docker compose up -d api worker beat` and resume. |
| `places ... credit at 80%+` (cost monitor) | Places key near its credit | Warn the user before starting another UF. Hybrid lane: ~US$0.007 Places + ~US$0.007 Apify per atrativo; check the Apify credit too (`/api/v1/cost` `.apify`). |
| `publish_rejected status=4xx` / funnel `REJECTED>0` / `last push_error` | norteia-api refused the payload (contract) | Read `push_error`; it is a contract bug, not transient. Report it; repush happens only after 24 h or via `POST /api/v1/mar/repush`. |
| api-ingest `ingest_probe=401` | `brave-ingest` Sanctum token expired (30-day TTL) | Renew (see **Token** below). |
| `laravel: ... ERROR` | norteia-api raised during ingest | Read it. A 1062 duplicate on `destinations` should no longer happen (api#105); anything else is an API bug to report. |
| funnel `poison_quarantine` rising | A card failed parsing/validation | `scripts/api.sh GET /api/v1/failures`. Does not block the run. |
| `[stack] docker VM: ... LOW` / `CRITICAL` or swap growing | Docker VM memory pressure | Tell the user: stop other ddev projects, or raise Docker Desktop memory (restarts every container, other projects included, so it is the user's call). |
| `[stack] ... restarts=N` growing | Crash loop / OOM | `docker compose logs --tail 100 <svc>`. |
| summary `sem_backfill > 0` after a sweep | TA part of some cards incomplete; describe skips them | `scripts/api.sh POST /api/v1/tripadvisor/backfill`, wait for `ta_backfill_batch outcome=done`, re-run describe. |
| `Traceback`, `WorkerLostError`, `TimeLimitExceeded` | Worker failure | Read the surrounding log; the sweep re-enqueues on its own time budget (~55 min). Report anything else. |

## TA session

Only the user can capture it. Pre-flight requires **two** cURLs, both gitignored in `scripts/single_attraction/`. Capture them in Chrome, logged in on TripAdvisor, in this order:

1. **Paginação → `ta_paginacao.curl`.** Open a UF's attractions listing. Open DevTools › Network and clear it. Click **next page**, then Cmd+F `pagee`. Find the `POST /data/graphql/ids` whose payload has `routeParameters.pagee` and Copy as cURL (bash). Pre-flight compares its qid with the one the sweep sends: a mismatch is a WARN, and you act on it only on `PersistedQueryNotFound` (row above).
2. **Página Brazil → `ta_brazil.curl`.** Load `https://www.tripadvisor.com.br/Attractions-g294280-Activities-Brazil.html`. Copy any `POST /data/graphql/ids` it makes as cURL. Its fresh `datadome` cookie is the one the attraction pages need. It is the cURL to inject:

```bash
set -a; . ./.env; set +a
.venv/bin/python scripts/ta_bootstrap.py --curl scripts/single_attraction/ta_brazil.curl --endpoint http://localhost:8000
```

`Session injected — canary result: ready`, then a pre-flight `OK    TA probe` and `OK    cURL` ×2 = done. On 28/10 a listing cURL injected at 15:52 got its attraction pages 403'd by DataDome at 16:08. A Brazil-page cURL then unblocked them, while re-injecting the old cURL did not. The canary checks the listing only: on 24/10 a 14 h-old cURL passed it while DataDome 403'd every attraction page, and the sweep stopped 5 min in. The first-page load batch of the listing is telemetry only and fails the canary.

## Token

```bash
ssh norteia-dev 'docker exec dev-norteia-api php artisan tinker --execute="echo \App\Models\User::find(1)->createToken(\"brave-ingest\", [\"territorial:ingest\"])->plainTextToken;"'
```

Write it QUOTED into `.env` as `BRAVE_NORTEIA_API_SERVICE_TOKEN="<id|secret>"` without printing it, then `docker compose up -d api worker beat`.
