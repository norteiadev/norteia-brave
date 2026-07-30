---
name: sync-test-atrativo
description: >-
  Run the end-to-end Brave→norteia-api sync test for ONE atrativo, one completude
  degrau at a time (base TripAdvisor card → Google Places enrichment →
  descricao_editorial), printing the exact push payload and the API's HTTP result
  at each degrau. Use whenever the user wants to check, validate, debug or
  "see what the API receives" for an atrativo sync, e.g. "roda o teste de sync dos
  atrativos", "testa o sync com a norteia-api", "valida as etapas de completude",
  "por que esse atrativo não chegou na API", "o push está dando 422", or after
  touching build_push_payload, the Places enrichment agent, promote_to_mar, or the
  norteia-api ingest contract. Requires a fresh TripAdvisor cookie jar from the
  user (DevTools "Copy as cURL").
---

# Sync test — atrativo, degrau a degrau

Drives ONE real TripAdvisor attraction through the real pipeline
(Nascente → Rio → steward validate → Mar → push) and stops at each **completude
degrau** so you can see exactly which fields norteia-api gains at each step.

This is an **operator probe, not a test**: it hits TripAdvisor, Nominatim, Google
Places, Anthropic and the real norteia-api. It spends real money (see Cost below).

## When to use

Any "does the atrativo sync work / what does the API actually receive" question, and
as the regression check after changing `build_push_payload`, `PlacesEnrichmentAgent`,
`promote_to_mar`, the reliability score, or `IngestAttractionRequest` on the API side.

Not for offline verification — that is the unit suite
(`env -u RUN_REAL_EXTERNALS .venv/bin/python -m pytest -q`). Run the suite first;
use this probe to prove the wire contract against a live API.

## Prerequisites

1. **A fresh TripAdvisor cookie jar.** TA is DataDome-protected and the session
   expires in hours. Ask the user to open TripAdvisor, DevTools → Network → any
   `graphql/ids` request → *Copy as cURL (bash)*, and paste it. Save it verbatim to
   `tmp/ta_session.curl` (repo root). `tmp/` and `*.curl` are gitignored **because
   that jar is a live credential** — never echo the cookie values back into the
   terminal, never commit it.
2. **The stack up**: `docker compose up` (postgres, redis, worker, api).
3. **Env in the worker**: `RUN_REAL_EXTERNALS`, `BRAVE_DB_URL`, `BRAVE_DB_REDIS_URL`,
   `BRAVE_PLACES_API_KEY`, `BRAVE_LLM_ANTHROPIC_API_KEY`, `BRAVE_NORTEIA_API_URL`,
   `BRAVE_NORTEIA_API_SERVICE_TOKEN`. Run **inside the worker container** — that is
   where the norteia-api hostname resolves.

## How to run

```bash
docker exec norteia-brave-worker-1 /app/.venv/bin/python \
  /app/.claude/skills/sync-test-atrativo/scripts/sync_test_atrativo.py \
  --curl /app/tmp/ta_session.curl \
  --location-id 554128 --uf RJ --name "Cristo Redentor" \
  --lat -22.951916 --lng -43.210487 \
  --stage base
```

Stages (`--stage`), run one at a time so each degrau is legible:

| stage | what it does | degrau |
|---|---|---|
| `base` | ingest the TA card → rio → steward validate → promote → push | completude = TA field coverage |
| `places` | `PlacesEnrichmentAgent` **without** the copywriter → re-score → push | gains hours, Google coords, place_id, address, phone/website/price_level, business_status, distrito |
| `descricao` | same agent **with** the copywriter → push | completude 90 (`descricao_editorial`) |
| `push` | re-push the current Mar record — no external SKU spent | — |
| `all` | the three in sequence | — |

`--list-geoid <geoId>` prints page-1 attraction cards for a geo and exits — use it to
pick a target (name + locationId + lat/lng) without guessing.

## Picking a target — the idempotency trap

`process_nascente_record` returns the **existing** Rio for a known `canonical_key`, so
re-running against an already-ingested atrativo silently re-uses the old record and the
run proves nothing. Either pick a fresh `--location-id`, or delete the three rows first:

```sql
delete from mar_records     where source_ref    like 'tripadvisor:attraction:<ID>%';
delete from rio_records     where canonical_key like 'tripadvisor:attraction:<ID>%';
delete from nascente_records where source_ref   like 'tripadvisor:attraction:<ID>%';
```

(`docker exec norteia-brave-postgres-1 psql -U brave -d norteia_brave`. The database is
`norteia_brave`, not `brave`.) **Say what you are deleting and why before doing it** —
it is destructive, even if scoped.

## Reading the output

Each stage prints the per-criterion score table (value × weight = contribution), the
full push payload, and the API result. **A 422 body is printed verbatim** — that is the
single most useful signal this probe produces, because a 422 rejects the *whole*
attraction, so one bad field silently blocks the entire record.

Verify the API side afterwards:

```bash
docker exec ddev-norteia-api-db mysql -uroot -proot db -e "
select a.id, a.name, d.tourist_name as destino, char_length(a.description) as descr_len,
       a.reliability_score, a.address, a.opening_hours
from attractions a left join destinations d on d.id = a.destination_id
where a.source_ref='tripadvisor:attraction:<ID>'\G
select * from attraction_place_details where attraction_id=<ID>\G"
```

## Cost per run

- `base`: TripAdvisor + Nominatim only — free.
- `places`: 1 Places Text Search + 1 Place Details (skips Text Search when
  `place_id_cache` is already set).
- `descricao`: 1 Anthropic Sonnet call with server-side `web_search`
  (**~US$ 0.07–0.10 per atrativo** — the web-search results dominate the input tokens).
- `push`: free.

Report the actual spend back to the user; do not loop stages "just to be sure".

## Known gates that stop a record before the push

- `promote_to_mar` needs `normalized["most_recent_review_at"]` within **90 days**
  (attractions only) — otherwise DLQ `no_recent_reviews`, nothing is pushed. The TA lane
  only fills that date on the `enrich_reviews` path.
- The TA lane ceiling sits below `threshold_mar=80`; the probe applies the steward
  validate (`validacao_humana=100`) because that is the production path to Mar.
- `business_status` CLOSED_* on a confident Places match → hard `descarte`.
- Already-`google_enriched` records skip the **paid** Places sub-step; the description
  sub-step still runs (bounded to 3 attempts via `descricao_attempts`).
