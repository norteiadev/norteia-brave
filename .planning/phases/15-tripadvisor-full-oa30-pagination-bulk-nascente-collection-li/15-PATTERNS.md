# Phase 15: TripAdvisor full oa30 pagination + bulk Nascente collection + live sweep dashboard panel - Pattern Map

**Mapped:** 2026-06-26
**Files analyzed:** 13 (8 backend create/modify, 5 dashboard create/modify) + 3 test files
**Analogs found:** 13 / 13 (every file has an in-repo mirror — this phase is ~80% reuse)

> This map builds on `15-RESEARCH.md` (which already carries file:line analogs). It does NOT re-derive the pagination mechanism (LOCKED in CONTEXT) — it pins, per new/modified file, the exact analog excerpt to copy and the conventions/landmines to replicate.

---

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `brave/lanes/tripadvisor/client.py` → `fetch_attractions_paginated()` | client / lane transport | streaming (async iterator, paged HTTP GET) | same file `fetch_destinations` (paged loop) + `fetch_attractions` (httpx/cookies/proxy wiring) | exact (same file, same role) |
| `brave/lanes/tripadvisor/client.py` → `_extract_sections_from_html()` | client / parser helper | transform (HTML→JSON island→sections[]) | same file `_parse_attractions_page` (static, never-raises parser) | exact (sibling static parser) |
| `brave/clients/base.py` → `TripAdvisorClientProtocol.fetch_attractions_paginated` | protocol / interface | request-response | same file `fetch_attractions` protocol stub (lines 260-278) | exact |
| `brave/clients/null_tripadvisor.py` → `fetch_attractions_paginated` | client / null stub | streaming (yields nothing) | same file `fetch_attractions` (returns []) lines 39-51 | exact |
| `tests/fakes/fake_tripadvisor.py` → `fetch_attractions_paginated` | test fake / call-recorder | streaming (yields fixtures per page) | same file `fetch_attractions` (records calls, returns fixture) lines 69-82 | exact |
| `brave/lanes/tripadvisor/atrativos.py` → bulk ingest path (`produce_paginated` / `_ingest_one_bulk`) | service / producer | batch (per-page ingest + commit) | same file `_ingest_one` lines 137-301 (minus parent-destino gate) | role-match (gate diverges) |
| `brave/lanes/tripadvisor/sweep_progress.py` (NEW) | store / pure Redis-state module | event-driven (writer=worker, reader=endpoint) | `brave/core/engine.py` lines 1-159 (pure-state, fakeredis-testable) | exact (mirror module) |
| `brave/tasks/pipeline.py` → `sweep_tripadvisor` bulk branch | task / orchestration | batch + streaming (page loop, per-page commit, resume, fail-fast) | same fn lines 924-1085 (esp. fail-fast block 1032-1046) | exact |
| `brave/api/routers/tripadvisor_session.py` → `GET /sweep/progress` + `TASweepProgressResponse` | route / controller | request-response (read-only JSON over Redis) | same file `session_status` lines 333-369 + `TASessionStatusResponse` lines 103-109 | exact |
| `brave/config/settings.py` → `TripAdvisorConfig` throttle field(s) | config | n/a | same class lines 232-287 (`session_ttl` Field) | exact |
| `dashboard/lib/ta-sweep-api.ts` (NEW) | provider / data layer | request-response (BFF fetch + query keys) | `dashboard/lib/engine-api.ts` (esp. `fetchTASessionStatus` lines 112-127) | exact |
| `dashboard/components/engine/TASweepProgress.tsx` (NEW) | component / panel | request-response (10s poll + render) | `dashboard/components/engine/EngineControl.tsx` (poll + progress bar + status pill) | exact |
| `dashboard/mocks/handlers/ta-sweep.ts` (NEW) | test mock / MSW handler | request-response | `dashboard/mocks/handlers/engine.ts` (esp. `taSessionStatus` lines 64-73) | exact |
| `dashboard/components/engine/__tests__/TASweepProgress.test.tsx` (NEW) | test | request-response | `EngineControl.test.tsx` (server.use + renderWithClient) | exact |
| `dashboard/app/processo/page.tsx` → mount `<TASweepProgress/>` | component / page mount | n/a | same file, `<EngineControl />` at line 96 | exact |

---

## Pattern Assignments

### `brave/lanes/tripadvisor/client.py` — `fetch_attractions_paginated()` (transport, streaming)

**Analog:** `fetch_destinations` (paged loop, lines 185-264) for the loop shape; `fetch_attractions` (lines 287-364) for the cookie/proxy/UA/httpx wiring and the 403/429 raise.

**Session + cookie/proxy/UA wiring to copy verbatim** (`fetch_attractions` lines 294-302, 357-359):
```python
session = self._get_session()
cookies = session.get("cookies", {})
user_agent = session.get("user_agent", "")
headers: dict[str, str] = {}
if user_agent:
    headers["User-Agent"] = user_agent
# CR-02 / T-11-01-01: proxy_url never logged.
proxy = self._config.proxy_url or None
...
async with httpx.AsyncClient(cookies=cookies, follow_redirects=True, proxy=proxy) as hc:
    resp = await hc.get(url, headers=headers)   # GET (HTML), not POST (GraphQL)
```

**403/429 fail-fast raise to copy verbatim** (lines 366-370 — keep the SAME `SessionExpiredError`; the task's existing `except` already catches it):
```python
if resp.status_code in (403, 429):
    raise SessionExpiredError(
        f"TripAdvisor HTML returned {resp.status_code} — DataDome/session expired. Re-inject required."
    )
resp.raise_for_status()
```

**Per-page yield + throttle** (mirror `fetch_destinations`'s `for page_num in range(...)` paged loop, lines 225-263; offset formula from CONTEXT `oa = (page-1)*30`). Use a module-level URL template constant beside `_TA_GRAPHQL_URL` (line 50):
```python
_TA_HTML_URL = (
    "https://www.tripadvisor.com/Attractions-g{geo_id}-Activities-"
    "a_allAttractions.true-oa{offset}-Brazil.html"
)
```

**LANDMINE — do NOT touch `fetch_attractions`.** Lines 304-318 (the WR-02 `NotImplementedError` on `max_pages>1`) are a deliberate tripwire. The new method is a separate transport (HTML GET vs GraphQL POST). Keep `fetch_attractions` byte-for-byte; `tests/unit/lanes/tripadvisor/test_client.py` asserts it still raises on `max_pages>1`.

**Conventions to replicate:** `structlog` per-page logs must log **offset/counts/error-class only** — never `cookies`, `user_agent`, `session_id` (T-11-01-02 / T-13-01-01, see docstring lines 22-26). `proxy` never appears in a log call (T-11-01-01).

---

### `brave/lanes/tripadvisor/client.py` — `_extract_sections_from_html()` (parser, transform)

**Analog:** `_parse_attractions_page` (lines 126-179) — a `@staticmethod`, never-raises, skip-with-debug-log parser. Mirror that posture exactly.

**Output contract (HARD):** must return the SAME `raw_sections` list shape that `_parse_attractions_page` consumes — a list of dicts where the FlexCards carry `__typename == "WebPresentation_SingleFlexCardSection"` and `singleFlexCardContent` (lines 145-147). The extractor feeds `self._parse_attractions_page(sections)` unchanged.

**Safe-extract posture to mirror** (lines 376-380 of `fetch_attractions` + the per-card `try/except … logger.debug(...); continue` at lines 176-178):
```python
sections: list = []
try:
    sections = data[0]["data"]["Result"][0]["sections"]
except (IndexError, KeyError, TypeError):
    sections = []
```
For HTML, do NOT hardcode that GraphQL envelope path — recover the JSON island via `re` + `json.loads`, then **recursively locate** the list whose items carry the FlexCard `__typename` (RESEARCH Pattern 2). Return `[]` on any miss; never raise.

**LANDMINE — no new HTML/DOM parser.** `tests/unit/lanes/tripadvisor/test_client.py:484` asserts no `scraper`/`playwright` in pyproject; RESEARCH bars `lxml`/`beautifulsoup4`/`selectolax`. Use stdlib `re` + `json` only — you are recovering a JSON island, not walking the DOM.

**LANDMINE — Wave-0 fixture gates this.** Write test-first against `tests/fixtures/tripadvisor/attractions_oa0.html` (scrubbed real capture). The exact `<script>` marker + JSON nesting are unknown until the fixture exists. MEDIUM-confidence piece (A2).

---

### `brave/clients/base.py` — `TripAdvisorClientProtocol.fetch_attractions_paginated`

**Analog:** the `fetch_attractions` protocol stub (lines 260-278). Add a sibling stub with a matching docstring (geo_id, start_page/max_pages, yields `(offset, cards)`). Return type is an async-iterator of `tuple[int, list[dict[str, Any]]]`.

**LANDMINE (Pitfall 6):** all FOUR implementers must gain the method in the same wave or structural typing + `_check_protocol_compliance()` break: protocol (this file), real (`client.py:409`), null (`null_tripadvisor.py:66`), fake (`fake_tripadvisor.py:98`).

---

### `brave/clients/null_tripadvisor.py` — `fetch_attractions_paginated`

**Analog:** `fetch_attractions` (lines 39-51) — returns `[]`, no network. The paginated null variant must be an async generator that **yields nothing**:
```python
async def fetch_attractions_paginated(self, geo_id, start_page=1, max_pages=334):
    """Offline stub — yields nothing (no scraping)."""
    return
    yield  # pragma: no cover  (makes this an async generator)
```
Keep the docstring + "no Playwright / no network" posture (T-11-01-03).

---

### `tests/fakes/fake_tripadvisor.py` — `fetch_attractions_paginated`

**Analog:** `fetch_attractions` (lines 69-82) — appends to a call-recording list, returns fixture. The paginated fake records calls and yields fixture pages:
```python
self.paginated_calls: list[dict[str, Any]] = []   # add in __init__ beside attractions_calls
...
async def fetch_attractions_paginated(self, geo_id, start_page=1, max_pages=334):
    self.paginated_calls.append({"geo_id": geo_id, "start_page": start_page, "max_pages": max_pages})
    for offset, cards in self._fixture_pages.get(geo_id, []):
        yield offset, cards
```
Add a `fixture_pages` constructor arg mirroring `fixture_attractions` (lines 32-50). Update the `_check_protocol_compliance()` (line 98).

---

### `brave/lanes/tripadvisor/atrativos.py` — bulk ingest path (producer, batch)

**Analog:** `_ingest_one` (lines 137-301). REUSE the whole body — review-signals (lines 145-155), §7.6 criterion calls (158-159), IBGE resolve (162-168), Phase-14 geo-enrichment (170-201), `completude_entity` mapping (194-201), Pydantic LGPD payload (236-251), `store_raw` (286-293), `process_nascente_record` (295-300).

**THE DIVERGENCE (blocker A1, CONTEXT resolution):** the bulk national lane must **bypass the parent-destino gate** (lines 213-232, the `parent_destino_absent` quarantine) and **derive `uf` from the geocoded IBGE code** rather than receiving a single per-UF `uf` arg. Concretely:
- Drop the `map_entry = self._destino_rio_map.get(...)` lookup + its quarantine-and-return (lines 214-232).
- After `ibge_match` resolves, derive `uf = ibge_uf_from_code(ibge_match.ibge_code)` (first 2 digits → UF) instead of trusting an input `uf`.
- Build the Pydantic payload with `parent_rio_id=None` / `parent_source_ref=None` (confirm `TripAdvisorAtrativoPayload` allows null parents; if not, that schema change is part of this file's plan).

**KEEP the existing `_ingest_one` intact** — the per-UF destinos-driven path (parent linkage) must remain for the existing lane. The bulk path is a NEW method (e.g. `_ingest_one_bulk` + `produce_paginated`), not a mutation of the old contract (CONTEXT decision).

**Per-page commit + progress** (NEW, lives in `produce_paginated` driving the async iterator):
```python
async for offset, cards in self._client.fetch_attractions_paginated(geo_id, start_page, max_pages):
    ingested = 0
    for card in cards:
        try:
            await self._ingest_one_bulk(card, run_rio=run_rio)
            ingested += 1
        except Exception as exc:  # mirror produce() lines 127-135 quarantine
            quarantine_poison(session=self._session, nascente_id=None,
                              task_name="brave.ta.atrativos.produce_paginated",
                              error=str(exc), payload={"offset": offset, "error": str(exc)})
    self._session.commit()                              # PER PAGE (Pitfall 3)
    sweep_progress.record_page(redis, offset, ingested) # progress + last_completed_offset
```

**LANDMINE — LGPD aggregate-only.** Never widen beyond `review_count` / `rating` / `most_recent_review_at`. `TripAdvisorReviewSignals` enforces `extra="forbid"` (docstring lines 11-14). The reused `_parse_attractions_page` already only reads aggregate fields — do not add author/text.

---

### `brave/lanes/tripadvisor/sweep_progress.py` (NEW) — pure Redis-state module (store, event-driven)

**Analog:** `brave/core/engine.py` lines 1-159 — pure functions over a Redis client, no dispatch, no DB. Copy its structure exactly:
- `_decode(value)` helper (lines 55-60) verbatim (bytes/None-safe).
- Module-level key constants (lines 42-47 pattern). Use the repo `brave:ta:*` convention (`client.py:47` `brave:ta:session`): `_PROGRESS_KEY = "brave:ta:sweep:progress"` (a Redis HASH).
- `get_status`-style snapshot (lines 149-158) → `get_progress(redis) -> dict` returning the exact field set the endpoint serializes.

**Recommended functions** (from RESEARCH Pattern 3): `start(redis, pages_total, resume_from_offset=0)`, `record_page(redis, offset, ingested_delta)` (HINCRBY pages_done/attractions, HSET `last_completed_offset`), `record_error(redis)`, `stop_needs_bootstrap(redis)`, `mark_done(redis)`, `get_progress(redis)`, `get_resume_offset(redis) -> int`. State ∈ `{idle, running, done, stopped_needs_bootstrap}`.

**Testable with `fakeredis`** exactly like engine state — no DB, one writer surface.

---

### `brave/tasks/pipeline.py` — `sweep_tripadvisor` bulk branch (orchestration, batch)

**Analog:** `sweep_tripadvisor` itself (lines 924-1085). Reuse the client-selection branch verbatim (lines 956-978: real `TripAdvisorClient` vs `NullTripAdvisorClient`, gated on `app_config.run_real_externals`) and the geocoder selection (970-978).

**REUSE the fail-fast block verbatim** (lines 1032-1046) — HTML 403/429 raises the SAME `SessionExpiredError`, so this block already does "rollback, `_mark_needs_bootstrap()`, log error-class only, return (no retry, no quarantine)". Add ONE line inside it: `sweep_progress.stop_needs_bootstrap(rc)` to set the panel's terminal state.

**THE CADENCE CHANGE (Pitfall 3):** the existing path commits once at line 1030. The bulk branch must commit **per page** inside the loop (done in the producer, above) so a mid-run 403 leaves durable records and an accurate `last_completed_offset`. Do not wrap 334 pages in one transaction.

**Resume read:** before the loop, `start_page = sweep_progress.get_resume_offset(rc) // 30 + 1` so a re-run continues from where it stopped (Session TTL 30 min ≪ full run — resume is the happy path, Pitfall 5).

**Bulk vs per-UF branch:** the national geoId (294280) path is a distinct branch — it does NOT run the destinos producer / build `destino_rio_map` (lines 988-1028). It calls the new `produce_paginated`. Keep `run_rio = depth != collection_engine.NASCENTE` (line 949) and depth-gating intact (never auto-promote to Mar; TA never enters WhatsApp — CONTEXT locked).

**Conventions:** log only `error_type=type(exc).__name__` (lines 1042-1044) — exc str may carry cookie fragments (T-12-04-01).

---

### `brave/api/routers/tripadvisor_session.py` — `GET /sweep/progress` + `TASweepProgressResponse` (route, request-response)

**Analog:** `session_status` (lines 333-369) + `TASessionStatusResponse` (lines 103-109).

**Endpoint to mirror** (copy the decorator + auth dep + `Depends(get_redis)` shape):
```python
@router.get(
    "/api/v1/tripadvisor/sweep/progress",
    dependencies=[Depends(require_steward_or_bearer)],   # same dep as sibling TA endpoints
    response_model=TASweepProgressResponse,
)
def sweep_progress(redis: Redis = Depends(get_redis)) -> TASweepProgressResponse:
    return TASweepProgressResponse(**sweep_progress_state.get_progress(redis))
```

**Response model to mirror** `TASessionStatusResponse` (lines 103-109) — Pydantic `BaseModel`, `Literal` for the enum state field (mirror `reason: Literal["needs_bootstrap"] | None`). Fields: `state`, `pages_done`, `pages_total`, `attractions_ingested`, `current_offset`, `error_count`, `started_at: str | None`.

**Auth note:** the sibling TA endpoints all use `require_steward_or_bearer` (deps.py:85, constant-time fail-closed) — use the same for consistency. Read-only; no new write surface. Router is already mounted (`main.py:67`) — just add to it.

**LANDMINE:** never include cookie/session values in the response or logs.

---

### `brave/config/settings.py` — `TripAdvisorConfig` throttle field (config)

**Analog:** `session_ttl` Field in `TripAdvisorConfig` (lines 256-262). Add beside it:
```python
page_throttle_seconds: float = Field(
    default=2.0,
    description=(
        "Seconds to sleep between sequential -oa{N}- page GETs "
        "(BRAVE_TA_PAGE_THROTTLE_SECONDS). DataDome endurance + politeness."
    ),
)
```
**LANDMINE (CR-02):** NO `Field(alias=...)` — the class `model_config` is `env_prefix="BRAVE_TA_"` (line 286) and resolves only the exact `BRAVE_TA_PAGE_THROTTLE_SECONDS` name. Update the docstring env-var list (lines 241-246).

---

### `dashboard/lib/ta-sweep-api.ts` (NEW) — data layer (provider, request-response)

**Analog:** `dashboard/lib/engine-api.ts` — specifically `fetchTASessionStatus` (lines 124-127) and the `taSessionKeys` / `ENGINE_REFETCH_INTERVAL_MS` (line 74) patterns.
```ts
import { apiFetch } from "@/lib/api-client";

export interface TASweepProgress {
  state: "running" | "done" | "stopped_needs_bootstrap" | "idle";
  pages_done: number; pages_total: number; attractions_ingested: number;
  current_offset: number; error_count: number; started_at?: string;
}
export const taSweepKeys = { status: ["ta", "sweep", "progress"] as const };
export function fetchTASweepProgress(): Promise<TASweepProgress> {
  return apiFetch<TASweepProgress>("api/v1/tripadvisor/sweep/progress");
}
```
Reuse the 10s interval constant. `apiFetch` takes the FastAPI path (`api/v1/...`); the `bff()` helper (`api-client.ts:34-38`) maps it to the BFF mount — callers never write the double prefix.

---

### `dashboard/components/engine/TASweepProgress.tsx` (NEW) — panel (component, request-response)

**Analog:** `EngineControl.tsx`. Copy:
- The `useQuery` poll config (lines 92-97): `queryKey: taSweepKeys.status, queryFn: fetchTASweepProgress, refetchInterval: ENGINE_REFETCH_INTERVAL_MS, refetchOnWindowFocus: false`.
- The progress-bar markup (lines 329-344) — swap `ufs_done/ufs_total` for `pages_done/pages_total`.
- The terminal-state pill pattern (`sessionLabel`/`sessionColor`, lines 73-84) for the `state` enum (running/done/stopped_needs_bootstrap).
- `CountTile` (lines 368-375) for attractions-ingested / current-offset / errors tiles.
- `"use client"` + `@tanstack/react-query` + `data-testid` discipline (every readable element has a testid).

---

### `dashboard/mocks/handlers/ta-sweep.ts` (NEW) — MSW handler (test mock)

**Analog:** `dashboard/mocks/handlers/engine.ts` — specifically `taSessionStatus` (lines 64-73) and the `TA_BASE` constant (line 16).

**LANDMINE — double `/api/api/` BFF prefix is mandatory** (engine.ts lines 15-16):
```ts
const TA_BASE = "http://localhost:3000/api/api/v1/tripadvisor";
export function taSweepProgress(overrides: Partial<TASweepProgress> = {}) {
  const status: TASweepProgress = {
    state: "running", pages_done: 5, pages_total: 334,
    attractions_ingested: 150, current_offset: 120, error_count: 0,
    started_at: "2026-06-26T12:00:00Z", ...overrides,
  };
  return http.get(`${TA_BASE}/sweep/progress`, () => HttpResponse.json(status));
}
```
Browser → `/api/api/...` because the catch-all BFF maps `/api/<rest>` → FastAPI `/<rest>` (api-client.ts:10-13). A single `/api/` would 404 in tests.

---

### `dashboard/components/engine/__tests__/TASweepProgress.test.tsx` (NEW) — test

**Analog:** `EngineControl.test.tsx` (lines 1-90). Copy: `server.resetHandlers()` in `beforeEach` (lines 33-35), `server.use(taSweepProgress({...}))` per-test override, `renderWithClient(<TASweepProgress/>)` (from `../../cms/__tests__/test-utils`), `screen.findByTestId` + `waitFor` assertions. Cover: progress bar `5/334`, attractions count, terminal-state pill, 401-safe (mirror an `engineUnauthorized`-style handler).

---

### `dashboard/app/processo/page.tsx` — mount

**Analog:** the `<EngineControl />` mount at line 96. Add `<TASweepProgress />` immediately after it (import at top alongside `EngineControl`).

---

## Shared Patterns

### Offline-by-default external transport
**Source:** `pipeline.py` lines 956-978 (real-vs-null client branch on `app_config.run_real_externals`).
**Apply to:** the bulk sweep branch. `RUN_REAL_EXTERNALS` unset → `NullTripAdvisorClient` (yields nothing); set → real `TripAdvisorClient`. No test hits TripAdvisor by default. New transport mockable via `respx` (HTML body fixture) + fixture HTML.

### Cookie / session logging discipline (security)
**Source:** docstring `client.py:22-27`; `pipeline.py:1042-1044`; `tripadvisor_session.py:127,159,167`.
**Apply to:** every new structlog call (client, producer, task, endpoint). Log offsets / counts / `type(exc).__name__` only. NEVER log `cookies`, `user_agent`, `session_id`, `proxy_url` (T-11-01-01/02, T-12-04-01, T-13-01-01).

### Fail-fast on session expiry → operator re-inject
**Source:** `pipeline.py:1032-1046` (`except (SessionMissingError, SessionExpiredError)` → rollback + `_mark_needs_bootstrap()` + return).
**Apply to:** the bulk loop. Reuse verbatim; add `sweep_progress.stop_needs_bootstrap(rc)`. HTML 403/429 raises the same `SessionExpiredError` (client.py new method) so the block needs no widening.

### Pure Redis-state module (writer=worker, reader=endpoint)
**Source:** `brave/core/engine.py:1-159` (`_decode`, key constants, `get_status` snapshot).
**Apply to:** `sweep_progress.py`. `brave:ta:*` key convention; `fakeredis`-testable; no DB, no dispatch.

### Bearer/steward auth on read endpoints
**Source:** `tripadvisor_session.py:333-336` (`dependencies=[Depends(require_steward_or_bearer)]`, `deps.py:85`).
**Apply to:** `GET /sweep/progress`. Constant-time, fail-closed, matches sibling TA endpoints.

### BFF double-prefix + `apiFetch`
**Source:** `api-client.ts:10-38` (`bff()` maps `api/v1/...` → `/api/api/v1/...`); `engine.ts:15-16`.
**Apply to:** `ta-sweep-api.ts` (pass bare FastAPI path) + `ta-sweep.ts` MSW handler (hardcode the double prefix). Single `/api/` 404s.

### CR-02 config discipline
**Source:** `settings.py:286-287` (`env_prefix="BRAVE_TA_"`, no `Field(alias=...)`).
**Apply to:** the new throttle field — resolve only from the exact `BRAVE_TA_*` name.

---

## No Analog Found

None. Every file has a close in-repo mirror. The single genuinely-new shape is the **HTML embedded-JSON extractor** (`_extract_sections_from_html`): its analog `_parse_attractions_page` covers the static/never-raises *posture*, but the precise `<script>` marker + JSON nesting are **unverifiable until the Wave-0 HTML fixture exists** (RESEARCH A2, MEDIUM confidence). Treat the extractor as test-first against `tests/fixtures/tripadvisor/attractions_oa0.html`; until that fixture lands, the extractor cannot be correctly written.

---

## Metadata

**Analog search scope:** `brave/lanes/tripadvisor/`, `brave/clients/`, `brave/core/`, `brave/api/routers/`, `brave/config/`, `brave/tasks/`, `tests/fakes/`, `dashboard/lib/`, `dashboard/components/engine/`, `dashboard/mocks/handlers/`, `dashboard/app/processo/`.
**Files scanned (read in full or targeted):** client.py, atrativos.py, engine.py, base.py, tripadvisor_session.py, pipeline.py (sweep_tripadvisor), settings.py (TripAdvisorConfig), null_tripadvisor.py, fake_tripadvisor.py, engine-api.ts, engine.ts, EngineControl.tsx, EngineControl.test.tsx, processo/page.tsx, api-client.ts.
**Pattern extraction date:** 2026-06-26

---

## PATTERN MAPPING COMPLETE

**Phase:** 15 - TripAdvisor full oa30 pagination + bulk Nascente collection + live sweep dashboard panel
**Files classified:** 13 create/modify (+3 test files)
**Analogs found:** 13 / 13

### Coverage
- Files with exact analog: 12
- Files with role-match analog: 1 (`atrativos.py` bulk path — same `_ingest_one` body, parent-destino gate deliberately diverges)
- Files with no analog: 0 (extractor flagged as Wave-0 fixture-gated, MEDIUM confidence)

### Key Patterns Identified
- New paginated client method is a SEPARATE transport (HTML GET) — the single-page GraphQL `fetch_attractions` WR-02 contract stays byte-for-byte intact; the new method reuses the exact cookie/proxy/UA/403-raise wiring.
- Pure Redis-state modules mirror `core/engine.py` (`_decode`, `brave:ta:*` keys, `get_*` snapshot, fakeredis-testable); the fail-fast `SessionExpiredError` block in `sweep_tripadvisor` is reused verbatim.
- Dashboard panels follow EngineControl: 10s `useQuery` poll, `apiFetch` via BFF, mandatory double `/api/api/` MSW prefix, `data-testid` discipline, MSW+Vitest offline.
- Security/compliance is shared cross-cutting: never log cookies/session/proxy; LGPD aggregate-only review fields (`extra="forbid"`); CR-02 no config aliases; depth-gating + operator-gated posture preserved (no auto-Mar, no WhatsApp).
- The national-UF / parent-destino gate is the one real divergence: bulk path bypasses the `parent_destino_absent` quarantine and derives UF from the geocoded IBGE code — a NEW method, not a mutation of `_ingest_one`.

### File Created
`.planning/phases/15-tripadvisor-full-oa30-pagination-bulk-nascente-collection-li/15-PATTERNS.md`

### Ready for Planning
Pattern mapping complete. The planner can reference each analog file:line + excerpt directly in PLAN.md action sections.
