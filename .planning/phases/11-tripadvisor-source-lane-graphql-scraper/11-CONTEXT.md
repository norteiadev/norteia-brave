# Phase 11 — TripAdvisor source lane · CONTEXT

> Seeded from the Ultraplan-refined, user-approved plan (`~/.claude/plans/minha-preocupa-o-o-google-snuggly-church.md`). These are LOCKED — the planner designs around them, does not re-litigate.

## Goal

Add `brave/lanes/tripadvisor/` — a self-hosted GraphQL-hybrid scraper producing **destinos and atrativos** per UF, scoring reviews into §7.6, and an **audited human promote-override** so review-validated attractions reach Mar by operator action without weakening the canonical ≥85 gate for everything else. First **free** attraction source (no per-record API fee; infra cost only).

## Why (core tension, grounded in code)

- Attractions today only have a paid source (Places, ~1000/mo free tier); they land cold with `corroboracao=0`+`atualidade=0` (`discovery_agent.py:296-299`) → DLQ, and quarantine when no parent destino is in Mar (`_resolve_parent_destino`, Mar-only).
- Mar push needs depth `nascente_rio_mar` AND `routing=='mar'` (score ≥85) — `pipeline.py::push_mar/push_attraction_task` (`if rio.routing != 'mar': return`). Even `validate_and_promote_rio` (val=100) only promotes if re-score crosses 85. A typical TA attraction scores ~67 (~82 with val=100) → **cannot reach Mar by score** → the audited promote-override is the reconciliation.

## LOCKED decisions

### Acquisition (TA-01)
- Direct **GraphQL hybrid**: Playwright bootstraps a DataDome session → extract cookies → inject into `httpx` → POST persisted queries to `https://www.tripadvisor.com/data/graphql/ids`.
- `queryId` **rotates** → never hardcoded; Playwright intercepts the live GraphQL request to capture `queryId`+shape; `query_id_override` config escape hatch.
- Residential-proxy seam behind the client (`config.proxy_url`; dev runs without proxy). Try VPS datacenter IP first; add proxy only if DataDome blocks.
- Playwright **lazy-imported** inside the bootstrap method only — CI/dashboard never load a browser. `scraper` optional dep group (`playwright`, `undetected-chromedriver`), not in dev/CI.
- `403/429/captcha` → `SessionExpiredError` → one re-bootstrap via `tenacity` → persistent fail raises so the producer quarantines. Session (cookie jar + queryId map) cached in Redis `brave:ta:session` TTL `session_ttl`.

### Per-UF (TA-01)
- Resolve UF → TripAdvisor internal `geoId` (Rio=303506) via typeahead/search GraphQL; cache Redis-primary + seed `data/tripadvisor/uf_geoids.json` (27 UFs).

### IBGE linkage (TA-03)
- Table-only `data/ibge/ibge_municipios.csv` (5570: name, uf, ibge_code, lat, lng). `resolve_municipio` = rapidfuzz `token_sort_ratio` ≥ `ibge_match_threshold`(88), haversine <15km fallback, else `None` → quarantine `ibge_unmatched`.
- **Parent = destino `RioRecord` produced earlier in the SAME sweep** (destinos run before atrativos in one `sweep_tripadvisor`). Carry `parent_rio_id` + `parent_source_ref`; `parent_mar_id` only if that destino already in Mar. Quarantine `parent_destino_absent` **only** when no destino RioRecord exists for the município. **Deliberately diverges** from `discovery_agent.py`'s Mar-only resolution (which would quarantine ~100% under realistic `nascente_rio` depth).

### Scoring (TA-04)
- `origem_value=65.0` (>Places 60, <gov 100 — firewall: TA never crosses 85 on origem alone).
- `completude_from_fields` ×20 coverage (destino cap 80 / atrativo cap 100).
- `corroboracao_from_reviews(count,rating)` — log curve saturating ~500 reviews × rating gate.
- `atualidade_from_recency` — ≤30d→100 / ≤180d→70 / ≤365d→40 / ≤730d→20 / else 0.
- `validacao_humana=0` at ingest. Feeds the **existing** `compute_score` via `*_value` payload keys (`routing.py:144-148`) — no score-engine change.
- Proof to assert in tests: 200 reviews/4.5★/~5mo → **67.06 → dlq**; sparse/no-review floor 27.5 → descarte; val=100 → ~82 < 85 (proves override required).

### mar_ready + promote-override (TA-05)
- New column `rio_records.mar_ready` (boolean, indexed, default false; Alembic **0006**, `down_revision=0005`).
- Set in `route_by_score`: `entity_type=="attraction"` AND `canonical_key.startswith("tripadvisor:")` AND `atualidade_value≥70` AND `corroboracao_value≥mar_ready_corrob_bar`(60.0). False for every other source.
- `promote_override(session, rio, reason)`: guard `if not rio.mar_ready: raise PromoteNotAllowed` (→409); set `validacao_humana=100` (flag_modified + reassign, pattern from `dlq/service.py`), `reprocess_record`, then `rio.routing="mar"` + `promote_to_mar` **directly** (bypass gate); provenance `promotion_reason="steward_override_review_validated"`; audit `atrativo_promoted_override`. This bypasses the ≥85 gate **only** for operator-authorized `mar_ready` records; the canonical gate is untouched for everything else.

### Engine + API (TA-06)
- `sweep_tripadvisor` task (mirror `sweep_uf`): `run_rio = depth != NASCENTE`; real client iff `run_real_externals` else `NullTripAdvisorClient`; destinos then atrativos same task; no WhatsApp, no auto-push.
- `engine_sweep_run` gains `source: str = "default"`; `=="tripadvisor"` → dispatch `sweep_tripadvisor.delay(uf, depth)` per UF (honor Stop-drain + `nascente`-only branch).
- Engine Redis `brave:engine:source` + `set_source/get_source` (whitelist `{"default","tripadvisor"}`, fail-closed); `source` in `get_status`. `/engine/start` reads+validates `source` (422 before `start_run`), echoes it.
- Promote API: `PATCH /api/v1/atrativos/{rio_id}/promote` + `POST /api/v1/atrativos/promote-batch?uf=&source=tripadvisor&limit=`, both `require_steward_or_bearer`; plus dedicated `GET /api/v1/atrativos/mar-ready` for the dashboard list. Mirror `dlq.py` validate single/batch (broker-down 503 contract, per-record audit).

### Dashboard (TA-07)
- EngineControl: source radiogroup (`data-testid="engine-source"`) + UF multi-select chips when `tripadvisor`; thread `startEngine({depth,source,ufs})`; active-source read-back.
- New `/mar-ready` route + `SURFACES` nav entry; optimistic single + **bulk multi-select** promote mirroring `dlq-actions.ts` (remove + rollback, confirm dialog for batch).

### Compliance / LGPD (TA-08)
- Store only aggregate review fields (`review_count`, `rating`, `most_recent_review_at`) — **never** author/text — enforced at the `schemas.py` boundary.
- `data/tripadvisor/README` legal-risk note (scraping violates TA ToS; mitigations: low rate, residential proxy, no author PII, operator-gated not on autonomous beat). Lane docstring note. Root `SOURCES.md` index (mtur/places/tripadvisor).

## Defaults (reversible)
- TA allowed under `Apenas nascente` (no per-record fee; infra cost only) — docstring note, no dashboard cost warning this phase.
- TA attractions never enter WhatsApp; review-validated → `mar_ready`, rest → ordinary DLQ.
- Live Playwright bootstrap/scrape only via opt-in `@pytest.mark.real_browser`, never CI.

## Out of scope (future)
Structured hours/price; multichannel contact; auto-scheduling TA on the autonomous beat.

## Conventions to mirror
- Producer: `brave/lanes/destinos/mtur.py` (`MturSeedIngest.produce(uf, *, run_rio=True)`, `*_value` payload keys, `store_raw`→`process_nascente_record`).
- Null/Fake clients: `brave/clients/null_places.py`, `tests/fakes/`. Config: `AppConfig` sub-config pattern (`score`/`llm`/`whatsapp`/`ramp`), **no `Field(alias=...)`** (CR-02).
- Migration: `alembic/versions/0005_*` shape. DLQ promote/audit: `brave/core/dlq/service.py`, `brave/api/routers/dlq.py`. Dashboard optimistic actions: `dashboard/components/dlq/dlq-actions.ts`.

## Tests (100% offline default)
Producers (Fake + JSON fixtures), scoring proofs, IBGE resolver, geo-cache, respx GraphQL, session-expiry, parent-via-RioRecord, `engine_sweep_run(source)` dispatch, `/engine/start` 202/422, promote single→Mar+provenance+push / non-`mar_ready`→409 / batch, migration up/down round-trip, dashboard Vitest+MSW. Real path opt-in only.
