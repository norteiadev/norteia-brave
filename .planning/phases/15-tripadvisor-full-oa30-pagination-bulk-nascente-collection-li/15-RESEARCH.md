# Phase 15: TripAdvisor full oa30 pagination + bulk Nascente collection + live sweep dashboard panel - Research

**Researched:** 2026-06-26
**Domain:** Paginated HTML-SSR scraping over an already-solved offset mechanism; Redis-backed live progress; FastAPI status endpoint + Next.js polling panel; offline-by-default test seams. (norteia-brave Python collector + Next.js dashboard.)
**Confidence:** HIGH on every codebase-grounded answer (all file:line verified this session). MEDIUM on the embedded-JSON extraction technique (no real HTML in hand — must be pinned against a captured fixture in Wave 0). The pagination mechanism itself is LOCKED, not researched.

> The pagination MECHANISM (`-oa{N}-` path, N=page×30, 334 pages, reuse `_parse_attractions_page`) is ground truth from CONTEXT.md — this research does NOT re-derive or re-test it. It answers the seven OPEN implementation questions, grounded in the existing code.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **Pagination mechanism:** Offset is path-based (`-oa{N}-`, N=pageIndex×30), NOT a GraphQL variable — the `a5cb7fa004b5e4b5` persisted query rejects any offset field. `totalResults:10000`, `limit:30` → **334 pages** (oa0…oa9990).
- **Transport — HTML SSR extract (LOCKED):** `GET https://www.tripadvisor.com/Attractions-g{geoId}-Activities-a_allAttractions.true-oa{N}-Brazil.html` with the full operator cookie jar (datadome + TA session cookies) + captured UA → HTTP 200, ~1.5 MB HTML embedding the same 30 FlexCard `sections[]`. Extract the embedded JSON and feed the **existing `_parse_attractions_page`** unchanged. One transport for all pages.
- **Run scope — slice-first (LOCKED):** Validate a small slice (~5–10 pages / 150–300 attractions) end-to-end into Nascente FIRST, then scale to full 334. Fetch MUST be parameterized by page range / max-pages so slice and full run share ONE code path. Throttle between page requests, configurable. Resume-from-offset: persist last successfully-ingested offset; mid-run `SessionExpiredError` (403/429) must stop cleanly and record where it stopped (consistent with Phase 12 fail-fast `needs_bootstrap`).
- **Dashboard — NEW live progress panel (LOCKED):** Sweep writes live progress to a Redis key (e.g. `brave:ta:sweep:progress`): pages done/334, attractions ingested, current offset, error count, start time/rate. FastAPI status endpoint (bearer/steward auth, consistent with existing TA session endpoints) exposes it as JSON. New Next.js panel polls + renders progress bar, attractions ingested, current offset, errors, rate, terminal state (running / done / stopped-needs-bootstrap). Mirror existing Brave-monitor / EngineControl panel patterns, Bearer-header auth, MSW + Vitest.
- **Reuse / boundaries (LOCKED):** Reuse `_parse_attractions_page` (no new parser) and `_ingest_one` / existing Nascente ingest + §7.6 path (no new scoring). Honor engine depth gating + operator-gated posture; never auto-promote to Mar; TA attractions never enter WhatsApp. LGPD: only aggregate review fields (review_count, rating) — never author/text. Testing 100% offline by default; HTML transport mockable (respx / fake client).

### Claude's Discretion
- Exact Redis progress key name + schema, throttle delay default, batch/commit cadence to Nascente, the embedded-JSON extraction technique from the HTML (regex vs script-tag parse), and the precise dashboard panel layout — provided they satisfy the locked decisions.
- Whether pagination lives as a new `fetch_attractions_paginated` / a `page_range` arg on the client vs a thin loop in the sweep — provided slice and full run share one code path and the single-page `fetch_attractions` contract (Phase 13) is NOT silently broken.

### Deferred Ideas (OUT OF SCOPE)
- Per-UF attraction pagination (state-scoped geoIds).
- Destinos-lane pagination.
- Autonomous 24/7 TA scheduling on the beat.
- Residential-proxy automation / managed-browser session refresh.
- Going past the TA 10,000 display cap (oa9990).
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| TA-12 | Data-fetch correctness (extends Phases 12/13) | The new paginated HTML transport must yield the SAME normalized card dicts (`name, locationId, rating, review_count, category`) the existing `_parse_attractions_page` + `_ingest_one` consume. Correctness = (a) the embedded-JSON extractor produces the identical `sections[]` shape, (b) the single-page GraphQL `fetch_attractions` contract stays intact, (c) resume + fail-fast never silently drop or double-ingest pages. See Architecture Patterns + Common Pitfalls. |
</phase_requirements>

## Summary

Phase 15 is a **transport-extension + observability** phase, not a research-heavy one. The hard unknown (how to paginate) is already solved and locked. The real engineering risk lives in four codebase-specific seams that this research pins down with exact file:line references:

1. **The single-page contract is a tripwire.** `fetch_attractions` (`brave/lanes/tripadvisor/client.py:266`) raises `NotImplementedError` on `max_pages>1` (WR-02, lines 313-318) because the GraphQL listing query can't paginate. The plan must add a **separate** `fetch_attractions_paginated` method using a **different transport** (HTML GET, not GraphQL POST) and leave `fetch_attractions` byte-for-byte intact. The protocol (`brave/clients/base.py:260`), `NullTripAdvisorClient`, and `FakeTripAdvisorClient` all gain the new method.

2. **A national geoId (294280) breaks the per-UF ingest assumption** — this is the highest-impact finding and is NOT addressed by CONTEXT.md. `_ingest_one` (`atrativos.py:137`) takes a `uf` arg, resolves IBGE *within that UF*, and **quarantines every card that has no parent destino** (`parent_destino_absent`, lines 215-232) *before* `store_raw` ever runs (line 286). A whole-Brazil attraction-only run has no per-UF destino sweep and no single `uf` → as-is it would write **zero Nascente records** and 10,000 quarantine rows. The planner MUST resolve this (see Open Question 1). Recommended: derive UF from the geocoded IBGE code (first 2 digits) and relax/bypass the parent-destino gate for the bulk national path. **[ASSUMED]** — needs confirmation.

3. **Commit cadence must change for resume to work.** `sweep_tripadvisor` commits once at the very end (`pipeline.py:1030`). A 334-page run in one transaction means a mid-run 403 rolls back everything and resume has nothing persisted to resume *from*. Commit per-page (or per small batch) so the resume offset points at durably-stored records.

4. **Everything else has a clean mirror.** Redis progress → mirror `brave/core/engine.py` (pure-state module, fakeredis-testable). Status endpoint → mirror `session_status` in `tripadvisor_session.py:333`. Dashboard panel → mirror `EngineControl.tsx` + `engine-api.ts` + `mocks/handlers/engine.ts` + `EngineControl.test.tsx` (10s poll, Bearer via BFF). Fail-fast → reuse the existing `except (SessionMissingError, SessionExpiredError)` + `_mark_needs_bootstrap()` block (`pipeline.py:1032-1046`).

**Primary recommendation:** Add `fetch_attractions_paginated` as an async generator on the client (HTML transport), drive it from a new `produce_paginated` path on `TripAdvisorAtrativosIngest` that ingests + writes Redis progress + commits per page, gate the embedded-JSON extractor behind a **Wave-0 saved-HTML-fixture task**, and resolve the national-UF / parent-destino gap before writing any ingest code.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Paginated HTML fetch (oa30 loop, throttle, 403 stop) | Lane client (`brave/lanes/tripadvisor/client.py`) | — | Transport + DataDome politeness is a client/network-boundary concern; mirrors how `fetch_destinations` owns its own paging loop. |
| Embedded-JSON → `sections[]` extraction | Lane client (static helper, like `_parse_attractions_page`) | — | Pure parse; must be unit-testable against a saved fixture with no network. |
| Per-card ingest + §7.6 + IBGE/geo + parent linkage | Producer (`brave/lanes/tripadvisor/atrativos.py`) | — | `_ingest_one` already owns this; reuse unchanged where possible. |
| Page loop orchestration + progress write + commit cadence + resume | Celery task / producer (`brave/tasks/pipeline.py` `sweep_tripadvisor` + new producer method) | Redis | Long-running 24/7 orchestration belongs in the task layer; commit/resume is a worker concern. |
| Live progress state | Redis (new pure-state module, mirror `brave/core/engine.py`) | — | Shared between Celery worker (writer) and FastAPI endpoint (reader); same split as engine state. |
| Progress status endpoint | API / Backend (`brave/api/routers/tripadvisor_session.py`) | — | Read-only JSON over Redis; mirror `session_status`. |
| Progress panel (poll + render) | Frontend (Next.js `dashboard/components/...`) | BFF | Dashboard is the territorial CMS; polling + Bearer auth go through the BFF exactly like EngineControl. |

## Standard Stack

No new packages. Every library this phase needs is already pinned and in use.

### Core (already present — verified in repo)
| Library | Where used today | Purpose this phase |
|---------|------------------|--------------------|
| `httpx` (AsyncClient) | `client.py:233`, `:357` | HTML GET per page (cookies/proxy/UA reused verbatim). |
| `structlog` | `client.py:43` | Per-page progress + fail-fast logs (never log cookie values — T-12-02-01). |
| `redis` (sync client) | `pipeline.py:64`, `engine.py` | Progress state + resume offset + needs_bootstrap marker. |
| `pydantic` / `pydantic-settings` | `settings.py`, `tripadvisor_session.py` | New `BRAVE_TA_*` throttle config + the status-endpoint response model. |
| `@tanstack/react-query` + `sonner` | `EngineControl.tsx:3` | Panel polling + toasts. |
| `msw` + `vitest` | `mocks/handlers/engine.ts`, `EngineControl.test.tsx` | Offline dashboard tests. |

### Dev / test (already present)
| Tool | Where used today | Purpose this phase |
|------|------------------|--------------------|
| `respx` | `test_client.py:22`, used to mock `httpx` | Mock the HTML GET (return a saved HTML fixture body). |
| `fakeredis` | `test_client.py`, `test_sweep_tripadvisor.py` | Progress-state + resume unit tests. |
| `pytest` (asyncio) | throughout `tests/unit/lanes/tripadvisor/` | Extractor + paginated-fetch + progress tests. |

### HTML-extraction approach (no parser dep needed)
The embedded card data is **JSON inside a `<script>` blob** (Next.js `__NEXT_DATA__` / Apollo-style state), not HTML elements — so `re` + `json.loads` from stdlib suffice. **Do NOT add `lxml`/`beautifulsoup4`/`selectolax`** — you are not walking the DOM, you are recovering a JSON island. Adding an HTML parser is a "Don't Hand-Roll" inversion: simpler and more robust to `json.loads` the embedded state and recursively locate the `sections[]`. See Pattern 2.

**Installation:** none. (`grep -c playwright pyproject.toml` must stay 0 — `test_client.py:484` asserts no `scraper`/`playwright` in pyproject.)

## Package Legitimacy Audit

**N/A — this phase installs no external packages.** All transport, parsing, Redis, and dashboard libraries are already pinned in `pyproject.toml` / `dashboard/package.json` and exercised by existing tests. No registry verification required.

## Architecture Patterns

### System Architecture Diagram

```
                          OPERATOR (dashboard /processo)
                                   │  Bearer (BFF)
                                   ▼
   ┌──────────────────────────────────────────────────────────────┐
   │  POST /api/v1/engine/start  source=tripadvisor (existing)      │
   └───────────────┬──────────────────────────────────────────────┘
                   ▼
        engine_sweep_run (pipeline.py:1778)  ── per-UF loop ──┐
                   │ source=="tripadvisor": sweep_tripadvisor.delay(uf, depth)
                   ▼                                          
   ┌──────────────────────────────────────────────────────────────┐
   │ sweep_tripadvisor (pipeline.py:924)                           │
   │   ┌────────────────────────────────────────────────────────┐ │
   │   │ NEW bulk path (geoId 294280):                          │ │
   │   │  read resume_offset ← brave:ta:sweep:progress          │ │
   │   │  for page in [start … start+max_pages):                │ │
   │   │     cards = client.fetch_attractions_paginated(...) ───┼─┼──► GET .../-oa{N}-Brazil.html
   │   │        (HTML GET, cookie jar, proxy, UA)               │ │      (200, ~1.5MB) │
   │   │     ┌── extract embedded JSON → sections[] ◄───────────┼─┼──────────────────┘
   │   │     │   _parse_attractions_page(sections)  (REUSED)    │ │
   │   │     for card: _ingest_one(uf?, card)  (REUSED)         │ │
   │   │        store_raw → process_nascente_record (§7.6)      │ │──► Nascente (JSONB) + Rio
   │   │     session.commit()           ← PER PAGE (new)        │ │
   │   │     write progress + last_completed_offset ────────────┼─┼──► brave:ta:sweep:progress
   │   │     sleep(BRAVE_TA_PAGE_THROTTLE_SECONDS)              │ │
   │   │  on 403/429 → SessionExpiredError:                     │ │
   │   │     _mark_needs_bootstrap(); progress.state=stopped;   │ │
   │   │     return (no retry, no quarantine)  (REUSED block)   │ │
   │   └────────────────────────────────────────────────────────┘ │
   └──────────────────────────────────────────────────────────────┘
                   ▲ poll 10s (Bearer via BFF)
   ┌───────────────┴──────────────────────────────────────────────┐
   │ GET /api/v1/tripadvisor/sweep/progress  (NEW, mirror status)  │──► reads brave:ta:sweep:progress
   └──────────────────────────────────────────────────────────────┘
                   ▲
        TASweepProgress.tsx (NEW panel, mirror EngineControl) on /processo
```

### Recommended Project Structure (new/changed files)
```
brave/
├── lanes/tripadvisor/
│   ├── client.py              # + fetch_attractions_paginated() + _extract_sections_from_html()
│   ├── atrativos.py           # + produce_paginated() (drives generator, progress cb, per-page commit)
│   └── sweep_progress.py      # NEW: pure Redis-state module (mirror core/engine.py)
├── tasks/pipeline.py          # sweep_tripadvisor: bulk-path branch (national geoId) + resume read
├── config/settings.py         # TripAdvisorConfig + BRAVE_TA_PAGE_THROTTLE_SECONDS (+ batch size)
├── clients/base.py            # TripAdvisorClientProtocol + fetch_attractions_paginated
├── clients/null_tripadvisor.py# NullTripAdvisorClient + fetch_attractions_paginated (yields nothing)
└── api/routers/tripadvisor_session.py  # + GET /sweep/progress + TASweepProgressResponse

tests/
├── fixtures/tripadvisor/attractions_oa0.html   # NEW (Wave 0): scrubbed real HTML capture
├── fakes/fake_tripadvisor.py                    # + fetch_attractions_paginated (call-recording)
└── unit/lanes/tripadvisor/test_pagination.py    # extractor + paginated fetch + progress + resume

dashboard/
├── lib/ta-sweep-api.ts                          # NEW (mirror engine-api.ts)
├── components/engine/TASweepProgress.tsx        # NEW (mirror EngineControl.tsx)
├── components/engine/__tests__/TASweepProgress.test.tsx  # NEW
├── mocks/handlers/ta-sweep.ts                   # NEW (mirror engine.ts handler)
└── app/processo/page.tsx                         # mount <TASweepProgress/> beside <EngineControl/>
```

### Pattern 1: New paginated method, single-page contract untouched
**What:** Add a *separate* method on the client; do not touch `fetch_attractions`.
**When:** Always — WR-02 (`client.py:313-318`) deliberately fails loud on `max_pages>1`.
**Example (signature + transport, mirroring the existing httpx call at `client.py:357`):**
```python
# brave/lanes/tripadvisor/client.py  (NEW method — async generator)
_TA_HTML_URL = "https://www.tripadvisor.com/Attractions-g{geo_id}-Activities-a_allAttractions.true-oa{offset}-Brazil.html"

async def fetch_attractions_paginated(
    self,
    geo_id: int,
    start_page: int = 1,
    max_pages: int = 334,
) -> "AsyncIterator[tuple[int, list[dict[str, Any]]]]":
    """Yield (offset, parsed_cards) per page via the HTML SSR transport.

    Same code path for the slice (max_pages=5-10) and the full run (max_pages=334).
    Reuses _parse_attractions_page unchanged. Raises SessionExpiredError on 403/429.
    """
    session = self._get_session()
    cookies = session.get("cookies", {})
    user_agent = session.get("user_agent", "")
    headers = {"User-Agent": user_agent} if user_agent else {}
    proxy = self._config.proxy_url or None
    throttle = self._config.page_throttle_seconds  # NEW config field

    for page in range(start_page, start_page + max_pages):
        offset = (page - 1) * 30
        url = _TA_HTML_URL.format(geo_id=geo_id, offset=offset)
        async with httpx.AsyncClient(cookies=cookies, follow_redirects=True, proxy=proxy) as hc:
            resp = await hc.get(url, headers=headers)
        if resp.status_code in (403, 429):
            raise SessionExpiredError(
                f"TripAdvisor HTML returned {resp.status_code} — DataDome/session expired. Re-inject required."
            )
        resp.raise_for_status()
        sections = self._extract_sections_from_html(resp.text)  # NEW static helper
        cards = self._parse_attractions_page(sections)          # REUSED, unchanged
        yield offset, cards
        if page < start_page + max_pages - 1 and throttle > 0:
            await asyncio.sleep(throttle)
```
Note: `page 1 → offset 0`; the URL with `-oa0-` is confirmed valid (CONTEXT.md). Cookie/proxy/UA wiring is copied verbatim from the existing GraphQL path (`client.py:295-302, 357-359`).

### Pattern 2: Recover the embedded JSON by *content*, not by a hardcoded path
**What:** `json.loads` the embedded state blob, then **recursively find the list** whose items have `__typename == "WebPresentation_SingleFlexCardSection"`. Do not hardcode `data[0]["data"]["Result"][0]["sections"]` for HTML — the SSR embedding path may differ from the GraphQL envelope even though the card shape is identical.
**When:** The extractor, always.
**Example (resilient locator):**
```python
@staticmethod
def _extract_sections_from_html(html: str) -> list:
    """Return the FlexCard sections[] embedded in a TripAdvisor SSR page.

    Strategy: pull the JSON state island from its <script> blob, json.loads it,
    then recursively locate the list containing WebPresentation_SingleFlexCardSection
    items. Returns [] if not found (never raises — mirrors the safe-extract at client.py:376).
    """
    import re, json
    # Wave 0: pin the exact <script> marker against the saved fixture. Candidate markers
    # present in the page per CONTEXT.md: WebPresentation_SingleFlexCardSection / cardTitle.
    for m in re.finditer(r'<script[^>]*>(.*?)</script>', html, re.DOTALL):
        blob = m.group(1).strip()
        if "WebPresentation_SingleFlexCardSection" not in blob:
            continue
        # strip a leading assignment if present (e.g. window.__X = {...};)
        start = blob.find("{")
        if start == -1:
            continue
        try:
            data = json.loads(blob[start:].rstrip(";"))
        except json.JSONDecodeError:
            continue
        found = _find_flexcard_sections(data)  # recursive walk
        if found:
            return found
    return []
```
`_find_flexcard_sections` is a small recursive walk over dict/list looking for a list whose elements are dicts carrying that `__typename`. **This is the single highest-uncertainty piece** — it MUST be written test-first against a real saved fixture (Wave 0), because the precise `<script>` marker and JSON nesting are unknown until a real page is captured.

### Pattern 3: Pure Redis-state module (mirror engine.py)
**What:** A `sweep_progress.py` module of pure functions over a Redis client — no dispatch, no DB — exactly like `brave/core/engine.py:1-159`.
**Recommended schema (single hash `brave:ta:sweep:progress`, plus the existing `_TA_NEEDS_BOOTSTRAP_KEY`):**
```python
# brave/lanes/tripadvisor/sweep_progress.py
_PROGRESS_KEY = "brave:ta:sweep:progress"          # Redis HASH
# fields: state, pages_done, pages_total, attractions_ingested,
#         current_offset, last_completed_offset, error_count, started_at, updated_at
# state ∈ {running, done, stopped_needs_bootstrap, idle}

def start(redis, pages_total, resume_from_offset=0): ...   # HSET hash, state=running
def record_page(redis, offset, ingested_delta): ...        # HINCRBY pages_done/attractions; HSET last_completed_offset=offset
def record_error(redis): ...                                # HINCRBY error_count
def stop_needs_bootstrap(redis): ...                        # HSET state=stopped_needs_bootstrap
def mark_done(redis): ...                                   # HSET state=done
def get_progress(redis) -> dict: ...                        # snapshot for the endpoint (mirror engine.get_status)
def get_resume_offset(redis) -> int: ...                    # read last_completed_offset for resume
```
Naming follows the repo's `brave:ta:*` convention (`client.py:47` `brave:ta:session`, `geo.py:33` `brave:ta:geo:`, `pipeline.py:49` `brave:ta:needs_bootstrap`).

### Pattern 4: Status endpoint (mirror `session_status`)
```python
# brave/api/routers/tripadvisor_session.py  (ADD to the already-mounted router — main.py:67)
class TASweepProgressResponse(BaseModel):
    state: Literal["running", "done", "stopped_needs_bootstrap", "idle"]
    pages_done: int
    pages_total: int
    attractions_ingested: int
    current_offset: int
    error_count: int
    started_at: str | None = None

@router.get(
    "/api/v1/tripadvisor/sweep/progress",
    dependencies=[Depends(require_steward_or_bearer)],   # read-only; require_bearer also acceptable (matches engine_status)
    response_model=TASweepProgressResponse,
)
def sweep_progress(redis: Redis = Depends(get_redis)) -> TASweepProgressResponse:
    return TASweepProgressResponse(**sweep_progress_state.get_progress(redis))
```
Auth note: `engine_status` (`engine.py`) uses `require_bearer` (read). The existing TA `session_status` uses `require_steward_or_bearer`. Either satisfies "bearer/steward, consistent with existing TA endpoints"; `require_steward_or_bearer` matches the sibling TA endpoints most closely.

### Pattern 5: Dashboard panel (mirror EngineControl)
- `lib/ta-sweep-api.ts`: `fetchTASweepProgress()` via `apiFetch("api/v1/tripadvisor/sweep/progress")`; export `TASweepProgress` type + `taSweepKeys.status`; reuse `ENGINE_REFETCH_INTERVAL_MS` (10s).
- `TASweepProgress.tsx`: `useQuery({ queryKey, queryFn, refetchInterval: 10_000, refetchOnWindowFocus:false })`; render a progress bar `pages_done/pages_total` (copy the bar markup at `EngineControl.tsx:329-344`), an attractions-ingested tile, current offset, error count, and a terminal-state pill (mirror `sessionLabel`/`sessionColor` at `EngineControl.tsx:73-84`).
- `mocks/handlers/ta-sweep.ts`: `http.get("http://localhost:3000/api/api/v1/tripadvisor/sweep/progress", ...)` — **double `/api/api/` prefix** is mandatory (BFF rule, `engine.ts:11-16`).
- Mount in `app/processo/page.tsx` right after `<EngineControl />` (line 96).

### Anti-Patterns to Avoid
- **Modifying `fetch_attractions` to paginate.** It cannot — the GraphQL query rejects offsets. WR-02 fails loud on purpose. Add a new method.
- **Hardcoding the GraphQL envelope path for HTML extraction.** Locate sections by `__typename` content (Pattern 2).
- **One giant commit for 334 pages.** A mid-run 403 rolls it all back; resume then resumes from nothing. Commit per page/batch.
- **Adding an HTML/DOM parser.** You want the embedded JSON island; `json.loads` it.
- **Polling faster than 10s.** Matches the rest of `/processo`; faster polling adds no value and more load.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Card normalization from sections | A new parser for HTML cards | `_parse_attractions_page` (`client.py:126`) | Locked reuse; already handles malformed/ad/pagination sections + LGPD-aggregate fields. |
| Per-card ingest + §7.6 + IBGE + geo | A bulk ingest writer | `_ingest_one` (`atrativos.py:137`) | Owns Pydantic LGPD enforcement, geo-enrichment (Phase 14), parent linkage, store_raw, Rio routing. |
| Engine/progress Redis state | Ad-hoc `redis.set` calls scattered in the task | A pure-state module mirroring `core/engine.py` | Testable with fakeredis; one writer surface; matches repo convention. |
| 403/429 fail-fast handling | New error flow | Existing `except (SessionMissingError, SessionExpiredError)` + `_mark_needs_bootstrap()` (`pipeline.py:1032-1046`) | Already does "no retry, no quarantine, set marker, return". |
| HTML JSON extraction | Regex-slicing 1.5 MB of nested JSON by hand | `json.loads` the script island + recursive `__typename` search | Regex-slicing nested JSON is brittle; full parse + walk is robust to whitespace/escaping. |
| Bearer auth on the new endpoint | New auth logic | `require_steward_or_bearer` (`deps.py:85`) | Constant-time, fail-closed, already the TA-endpoint standard. |

**Key insight:** This phase is ~80% reuse. The genuinely new code is: one client method, one extractor helper, one progress module, one endpoint, one TS panel — plus the per-page commit + national-UF resolution that make the reuse actually work.

## Runtime State Inventory

This is a feature-extension phase (not a rename), but it **introduces new runtime state** that the planner must account for.

| Category | Items Found | Action Required |
|----------|-------------|------------------|
| Stored data | New Redis HASH `brave:ta:sweep:progress` (pages/attractions/offset/errors/state) + resume offset. Nascente rows for ~10k attractions land in Postgres via `store_raw`. | New pure-state module + per-page commit. No migration of existing data. |
| Live service config | Existing `brave:ta:session` cookie jar (operator-injected, NOT in git) is consumed by the new HTML GET path too. The HTML surface is a **different DataDome endpoint** than the GraphQL POST — a session that passes the GraphQL canary may still 403 on HTML (see Open Question 2). | None to migrate; flag the risk. |
| OS-registered state | None — no Task Scheduler / cron / launchd entries. TA stays operator-gated, off the beat. | None — verified: `engine_sweep_run` only dispatches `sweep_tripadvisor` on explicit operator start (`pipeline.py:1831-1835`). |
| Secrets/env vars | New `BRAVE_TA_PAGE_THROTTLE_SECONDS` (+ optional `BRAVE_TA_PAGE_BATCH_SIZE`) on `TripAdvisorConfig` (`settings.py:232`). No new secret. | Add config field(s); CR-02 rule: no `Field(alias=...)`. |
| Build artifacts | None — no package rename, no egg-info impact. | None. |

**Nothing found for OS-registered state and build artifacts** — verified by reading `engine_sweep_run` (only operator-triggered) and confirming no pyproject/package changes.

## Common Pitfalls

### Pitfall 1: National geoId 294280 has no UF → mass `parent_destino_absent` quarantine, zero Nascente
**What goes wrong:** The phase goal is "all ~10,000 Brazil attractions into Nascente," but `_ingest_one` requires (a) a `uf` to resolve IBGE and (b) a parent destino in `destino_rio_map`, else it calls `quarantine_poison` and **returns before `store_raw`** (`atrativos.py:203-232`). A national attraction-only run supplies neither → 0 Nascente, 10k quarantine rows.
**Why it happens:** Phase 11/13 built `_ingest_one` for per-UF sweeps where a destino producer runs first in the same sweep and builds `destino_rio_map` (`pipeline.py:998-1017`). The national 294280 path skips that.
**How to avoid:** Decide the linkage strategy (Open Question 1). Recommended: geocode each card (Phase 14 path already runs in `_ingest_one`), derive `uf = ibge_code[:2]`-mapped from the resolved município, and **bypass the parent-destino gate for the bulk national lane** so records reach Nascente + §7.6 + DLQ (the canonical gate), accepting that parent linkage is filled later. **[ASSUMED]** — confirm with operator/planner before coding.
**Warning signs:** A slice run shows Nascente count flat at 0 while PoisonQuarantine climbs.

### Pitfall 2: HTML DataDome surface ≠ GraphQL DataDome surface
**What goes wrong:** The session canary (`tripadvisor_session.py:117-189`) validates via the **GraphQL** `fetch_attractions`. The bulk run uses **HTML GET**. DataDome can wall the HTML page even when the GraphQL POST passes (CONTEXT.md itself notes the older "HTML navigation → 403" finding, now claimed solved *with the full jar*). If the captured jar is missing an HTML-relevant cookie, the slice 403s immediately.
**How to avoid:** Slice-first (already locked) surfaces this on page 1. On 403 the existing fail-fast sets `needs_bootstrap`. Document in the runbook that the operator capture must be from a real HTML attractions page navigation, not only a GraphQL XHR.
**Warning signs:** First page of the slice returns 403 despite a green session pill.

### Pitfall 3: One-transaction sweep loses everything on mid-run expiry
**What goes wrong:** `sweep_tripadvisor` commits once (`pipeline.py:1030`). A 403 on page 200 of 334 raises `SessionExpiredError`, the `except` block does `session.rollback()` (`pipeline.py:1037`) → all 200 pages of ingest vanish; resume offset points at data that was rolled back.
**How to avoid:** Commit per page (or per `BRAVE_TA_PAGE_BATCH_SIZE`) inside the loop, write `last_completed_offset` only after the commit succeeds. The fail-fast path then leaves durable records and an accurate resume point.
**Warning signs:** After a mid-run stop, the resume re-fetches from page 1 / Nascente count drops to 0 after a 403.

### Pitfall 4: Geocoding throttle makes the full run multi-hour and may breach Nominatim policy
**What goes wrong:** `_ingest_one` geocodes every coordless card via Nominatim with a ≥1.1s min interval (`settings.py:318`, Phase 14). AttractionsFusion cards carry **no lat/lng** (CONTEXT/ROADMAP Phase 14), so nearly all 10k are cache-miss on first run → ~10,000 × 1.1s ≈ **3+ hours** of geocoding alone, against the public OSM instance.
**Why it happens:** Phase 14 caching is by `locationId`; a first national run is all misses.
**How to avoid:** Slice-first proves the rate. For the full run, budget the duration explicitly, keep the public-instance politeness, and treat self-hosted Nominatim as a documented future op (Phase 14 already deferred it). The page throttle (TA) and the Nominatim interval (geo) compound — account for both in the time estimate. Flag in the runbook.
**Warning signs:** Slice of 5 pages (150 cards) takes minutes; extrapolated full run exceeds the session TTL (`session_ttl` default 1800s = 30 min, `settings.py:256`) — meaning the session WILL expire mid-run and resume is mandatory, not optional.

### Pitfall 5: Session TTL (30 min) is far shorter than a full 334-page run
**What goes wrong:** `BRAVE_TA_SESSION_TTL` defaults to 1800s. With per-page throttle + geocoding, the full run vastly exceeds 30 min, so the Redis session key **expires** mid-run → `SessionMissingError`/expiry. Resume is therefore the primary completion mechanism, not an edge case.
**How to avoid:** Design resume as the happy path for the full run: operator re-injects a fresh session, re-triggers the sweep, it continues from `last_completed_offset`. Consider documenting a longer operator TTL for bulk runs. Make the slice prove a full resume cycle (stop → re-inject → continue).
**Warning signs:** Full run never completes in one shot; treated as a bug instead of the expected multi-session flow.

### Pitfall 6: Forgetting to widen the protocol breaks structural typing
**What goes wrong:** Adding `fetch_attractions_paginated` only to the real client leaves `TripAdvisorClientProtocol`, `NullTripAdvisorClient`, and `FakeTripAdvisorClient` non-compliant; `_check_protocol_compliance()` (`client.py:409`, `null_tripadvisor.py:66`, `fake_tripadvisor.py:98`) will fail or the sweep will `AttributeError` under `run_real_externals=False`.
**How to avoid:** Add the method to all four in the same wave: protocol (`base.py:260`), real, null (yield nothing), fake (record calls + return fixtures per page).
**Warning signs:** `test_*_protocol_compliance` fails; sweep crashes when `NullTripAdvisorClient` is selected.

## Code Examples

### Reuse the existing fail-fast block for the HTML path (already in the task)
```python
# brave/tasks/pipeline.py:1032 — REUSE verbatim; HTML 403/429 raises the same SessionExpiredError
except (SessionMissingError, SessionExpiredError) as exc:
    session.rollback()
    _mark_needs_bootstrap()
    sweep_progress.stop_needs_bootstrap(rc)        # NEW: record terminal state for the panel
    logger.warning("sweep_tripadvisor_session_fail_fast", uf=uf, error_type=type(exc).__name__)
    return  # no retry, no quarantine
```

### Add the throttle config (mirror existing TripAdvisorConfig fields)
```python
# brave/config/settings.py — inside TripAdvisorConfig (CR-02: NO alias)
page_throttle_seconds: float = Field(
    default=2.0,
    description=(
        "Seconds to sleep between sequential -oa{N}- page GETs (BRAVE_TA_PAGE_THROTTLE_SECONDS). "
        "DataDome endurance + politeness over a long sequential run."
    ),
)
```

### MSW handler (double-prefix BFF rule)
```ts
// dashboard/mocks/handlers/ta-sweep.ts — mirror engine.ts:64-73
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

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| GraphQL persisted-query POST paginates via `offset` variable | GraphQL listing query **rejects** offsets; pagination is path-based via HTML SSR `-oa{N}-` | Phase 15 (this) | New transport (`httpx.get`) alongside the GraphQL POST; same parser. |
| `fetch_attractions` single-page only (WR-02 `NotImplementedError`) | `fetch_attractions_paginated` async generator for slice + full run | Phase 15 | Single-page contract preserved; multi-page is a distinct method. |
| Sweep commits once at end | Per-page commit for durability + resume | Phase 15 | Mid-run expiry no longer loses progress. |

**Deprecated/outdated:** Playwright/auto-bootstrap (removed Phase 12 — `test_client.py:108,302` assert it stays gone). Do not reintroduce any browser automation; the operator-cURL-capture model is the locked acquisition path.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | The national bulk run should bypass/relax the `parent_destino_absent` gate and derive UF from the geocoded IBGE code | Pitfall 1 / Open Q1 | If wrong, either 0 Nascente (gate kept) or orphaned attractions with no destino linkage (gate dropped without a plan). HIGH — must confirm before coding. |
| A2 | The embedded card data is a JSON island in a `<script>` tag (Next.js/Apollo-style), recoverable via `json.loads` + `__typename` search | Pattern 2 | If the data is rendered as HTML elements instead, the extractor needs a DOM parser. MEDIUM — pin against the Wave-0 fixture before committing the approach. |
| A3 | The captured operator cookie jar that passes the GraphQL canary also passes the HTML GET | Pitfall 2 | If the HTML surface needs extra cookies, the slice 403s on page 1 and the runbook capture instructions must change. MEDIUM. |
| A4 | A `BRAVE_TA_PAGE_THROTTLE_SECONDS` default of ~2s is enough for DataDome endurance | Throttle config | Too low → 403 storms; too high → multi-hour runs. LOW (tunable; slice-first calibrates it). |
| A5 | Per-page commit cadence is acceptable for the §7.6/Rio pipeline (no batch-only invariant) | Pitfall 3 | If Rio routing assumes a whole-sweep transaction, per-page commit could split a unit of work. LOW — `process_nascente_record` is already called per record. |

## Open Questions

1. **National geoId 294280 → UF + parent-destino linkage (BLOCKING).**
   - What we know: `_ingest_one` quarantines (no Nascente) without a `uf` + parent destino (`atrativos.py:203-232`); the bulk run has neither natively.
   - What's unclear: Should the planner (a) bypass the parent gate for the bulk lane and derive UF from geocoded IBGE, (b) run a national destinos sweep first to build a full `destino_rio_map`, or (c) accept Nascente-only records with deferred linkage?
   - Recommendation: (a) — derive `uf` from `ibge_code[:2]` after geo-enrichment and write to Nascente without requiring a parent, letting §7.6 + DLQ gate the records. Confirm before coding (A1).

2. **Does the operator session that passes the GraphQL canary also pass the HTML GET?** (A3)
   - Recommendation: make the slice-first run the empirical test; if it 403s, update `data/tripadvisor/README` capture instructions to require an HTML-page navigation in the capture session. No code change needed beyond the fail-fast that already exists.

3. **Resume granularity vs. session TTL.** With a 30-min default TTL and a multi-hour full run (Pitfall 4/5), is resume-from-offset across multiple operator re-injections the accepted completion model, or should bulk runs use a longer operator-set TTL?
   - Recommendation: document multi-session resume as the happy path AND let the operator raise `BRAVE_TA_SESSION_TTL` for bulk runs.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python venv + pytest | All backend tests | ✓ (per MEMORY: `.venv/bin/python -m pytest`) | 3.12 | — |
| Bun + Vitest | Dashboard panel tests | ✓ (per MEMORY: `cd dashboard && bun run test`) | bun 1.3.x | — |
| PostgreSQL (`BRAVE_DB_URL`) | `@pytest.mark.integration` sweep tests (per MEMORY: skip silently without it) | ✗ at research time | — | Unit-test the pieces (extractor, progress, paginated fetch) with respx+fakeredis; integration sweep needs `BRAVE_DB_URL` set. |
| Redis | Progress state, resume, fail-fast marker | ✓ in unit tests via `fakeredis` | — | `fakeredis` for unit; real Redis for integration. |
| TripAdvisor (live) | Only the real run | ✗ (operator-gated, `RUN_REAL_EXTERNALS` opt-in) | — | respx + saved HTML fixture; `NullTripAdvisorClient` when flag unset. |
| Saved HTML fixture | Extractor unit tests | ✗ (does not exist yet) | — | **Wave 0:** operator captures one real `-oa0-` page → scrub cookies/PII → `tests/fixtures/tripadvisor/`. No fallback — the extractor cannot be correctly written without it. |

**Missing dependencies with no fallback:**
- The **saved HTML fixture** — the embedded-JSON extractor (Pattern 2) cannot be verified offline without one real captured page. This is a Wave-0 blocker for the extractor task.

**Missing dependencies with fallback:**
- Postgres / real Redis / live TA — all covered by respx + fakeredis + fixtures for the offline suite (MEMORY: unset `RUN_REAL_EXTERNALS`, set `BRAVE_DB_URL` for integration).

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework (backend) | pytest 9.0.x + pytest-asyncio + respx + fakeredis |
| Framework (dashboard) | Vitest 4.x + MSW 2.x + @testing-library/react |
| Config file | `pyproject.toml` (pytest), `dashboard/vitest.config.*` |
| Quick run command | `.venv/bin/python -m pytest tests/unit/lanes/tripadvisor/ -x` |
| Full backend suite | `.venv/bin/python -m pytest` (per MEMORY: ensure `RUN_REAL_EXTERNALS` unset; set `BRAVE_DB_URL` for `@integration`) |
| Dashboard suite | `cd dashboard && bun run test` |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| TA-12 | Embedded-JSON extractor yields the same `sections[]` shape `_parse_attractions_page` consumes | unit | `pytest tests/unit/lanes/tripadvisor/test_pagination.py -k extract -x` | ❌ Wave 0 (needs HTML fixture) |
| TA-12 | `fetch_attractions_paginated` GETs `-oa{N}-` per page, throttles, raises `SessionExpiredError` on 403/429 | unit (respx) | `pytest tests/unit/lanes/tripadvisor/test_pagination.py -k paginated -x` | ❌ Wave 0 |
| TA-12 | `fetch_attractions` single-page contract still raises `NotImplementedError` on `max_pages>1` | unit | `pytest tests/unit/lanes/tripadvisor/test_client.py -x` | ✅ (existing — must stay green) |
| TA-12 | Progress module: start/record_page/stop/resume over fakeredis | unit | `pytest tests/unit/lanes/tripadvisor/test_sweep_progress.py -x` | ❌ Wave 0 |
| TA-12 | Sweep resume reads `last_completed_offset`; mid-run 403 sets `needs_bootstrap` + terminal progress state | unit | `pytest tests/unit/tasks/test_sweep_tripadvisor.py -x` | ✅ partial (extend existing) |
| TA-12 | Status endpoint returns progress JSON under Bearer/steward auth | unit | `pytest tests/unit/api/test_tripadvisor_session.py -x` | ✅ partial (extend existing) |
| TA-12 | Panel renders progress bar / counts / terminal pill; polls; 401 safe | unit (vitest) | `cd dashboard && bun run test TASweepProgress` | ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** `pytest tests/unit/lanes/tripadvisor/ -x` (+ `bun run test TASweepProgress` for panel tasks)
- **Per wave merge:** full backend `pytest` + `cd dashboard && bun run test`
- **Phase gate:** full suites green before `/gsd:verify-work`; plus an operator Level-3 slice run (5–10 pages) proving Nascente count > 0 and the panel updating live.

### Wave 0 Gaps
- [ ] `tests/fixtures/tripadvisor/attractions_oa0.html` — scrubbed real HTML capture (BLOCKS the extractor; covers TA-12)
- [ ] `tests/unit/lanes/tripadvisor/test_pagination.py` — extractor + paginated-fetch tests (TA-12)
- [ ] `tests/unit/lanes/tripadvisor/test_sweep_progress.py` — progress + resume (TA-12)
- [ ] `dashboard/mocks/handlers/ta-sweep.ts` + `TASweepProgress.test.tsx` — panel coverage
- [ ] Resolve Open Question 1 (national UF / parent gate) before writing ingest tests — the expected Nascente assertion depends on it

## Security Domain

`security_enforcement` is absent in `.planning/config.json` → treated as enabled.

### Applicable ASVS Categories
| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | yes | New status endpoint uses `require_steward_or_bearer` (`deps.py:85`) — constant-time `hmac.compare_digest`, fail-closed. |
| V3 Session Management | no | No user sessions; the "session" here is the operator DataDome cookie jar (already in Redis with TTL, never logged — T-12-02-01). |
| V4 Access Control | yes | Read-only progress endpoint; mutations (start/stop) stay on the existing engine endpoints. No new write surface. |
| V5 Input Validation | yes | Pydantic `TASweepProgressResponse` (output) + `extra="forbid"` discipline on any new request model; throttle config validated by pydantic-settings. |
| V6 Cryptography | no | No new crypto; auth reuses the existing constant-time compare. |
| V7 Logging | yes | structlog: never log cookie values or `session_id` (T-12-02-01 / T-13-01-01); log only page offsets, counts, error class names (mirror `pipeline.py:1042-1045`). |

### Known Threat Patterns for this stack
| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Cookie-jar leakage into logs over a long sequential run | Information Disclosure | Log offsets/counts/error-class only; reuse the T-12-02-01 logging discipline already in the lane. |
| LGPD drift: scraping author names/text from 1.5 MB HTML | Compliance / Info Disclosure | Extractor feeds the SAME `_parse_attractions_page`, which only reads aggregate `bubbleRating`/`reviewCount`; `TripAdvisorReviewSignals` enforces `extra="forbid"` (`atrativos.py:13-14`). Never widen the parser. |
| Unauthenticated read of sweep progress | Information Disclosure | Bearer/steward dependency on the endpoint; BFF validates the operator token before forwarding. |
| ToS / scraping legal risk over a 10k-page bulk run | Repudiation / legal | Operator-gated (`RUN_REAL_EXTERNALS` + explicit start), throttled, documented in `data/tripadvisor/README`; never on the autonomous beat. |
| SSRF via the page URL template | Tampering | URL is built from a fixed template + integer offset + integer geoId — no user-controlled host; mirror the fixed-base discipline of the BFF (`route.ts`). |

## Sources

### Primary (HIGH confidence — read this session)
- `brave/lanes/tripadvisor/client.py` (`:47` session key, `:126` `_parse_attractions_page`, `:266-387` `fetch_attractions` + WR-02 contract, `:357` httpx call shape)
- `brave/lanes/tripadvisor/atrativos.py` (`:108` `produce`, `:137` `_ingest_one`, `:203-232` quarantine gates, `:286` `store_raw`)
- `brave/tasks/pipeline.py` (`:49` needs_bootstrap key, `:924-1085` `sweep_tripadvisor` incl. fail-fast block, `:1778-1860` `engine_sweep_run` dispatch)
- `brave/api/routers/tripadvisor_session.py` (`:103` status model, `:333-369` `session_status`) and `brave/api/main.py:65-67` (router registration)
- `brave/core/engine.py:1-159` (pure Redis-state module pattern, `brave:engine:*` keys, `get_status`)
- `brave/config/settings.py:232-287` (`TripAdvisorConfig`, `BRAVE_TA_` prefix, CR-02 no-alias rule)
- `brave/clients/base.py:239-294` (`TripAdvisorClientProtocol`), `brave/clients/null_tripadvisor.py`, `tests/fakes/fake_tripadvisor.py`
- `brave/api/deps.py:52-121` (`require_bearer`, `require_steward_or_bearer`)
- `dashboard/components/engine/EngineControl.tsx`, `dashboard/lib/engine-api.ts`, `dashboard/mocks/handlers/engine.ts`, `dashboard/components/engine/__tests__/EngineControl.test.tsx`, `dashboard/lib/api-client.ts`, `dashboard/app/api/[...path]/route.ts`, `dashboard/app/processo/page.tsx`
- `tests/unit/lanes/tripadvisor/test_client.py` (respx mock pattern, fixtures, contract tests), `tests/unit/tasks/test_sweep_tripadvisor.py`
- `.planning/phases/15-.../15-CONTEXT.md`, `.planning/ROADMAP.md:61-75` (Phase 15 locked decisions)
- `CLAUDE.md` (project constraints), MEMORY (test commands, RUN_REAL_EXTERNALS, BRAVE_DB_URL, tripadvisor-graphql-real-shape)

### Secondary / Tertiary
- None — this research is entirely codebase-grounded; no web sources needed (mechanism is locked, stack is pinned in CLAUDE.md).

## Project Constraints (from CLAUDE.md)
- Python collector stack: FastAPI · Celery+Redis · LangGraph · Pydantic+instructor · PG/pgvector; psycopg 3 (never psycopg2).
- Testing: **no test hits TripAdvisor by default**; real = opt-in `RUN_REAL_EXTERNALS=1`; CI keyless. Logic in code, mockable transport.
- LGPD / Meta ToS / Google Places ToS / source-scraping legal risk documented per source; TA scraping operator-acknowledged (`data/tripadvisor/README`).
- §7.6 score + DLQ is the canonical gate — not human-approve-everything; TA never auto-promotes to Mar; TA attractions never enter WhatsApp.
- pydantic config: CR-02 — NO `Field(alias=...)` on any config field; resolve only from the exact `BRAVE_TA_*` name.
- GSD workflow: edits go through a GSD command (this is plan-phase research, no edits).

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — no new deps; all libraries verified in-repo and exercised by existing tests.
- Architecture / reuse seams: HIGH — every mirror target read at file:line this session.
- Embedded-JSON extraction technique: MEDIUM — approach is sound but the exact `<script>` marker/JSON nesting is unverifiable until a real HTML page is captured (Wave 0).
- National-UF / parent-destino gap: HIGH that the gap exists (read the code); the *resolution* is ASSUMED (A1) and needs operator/planner confirmation.
- Pitfalls (TTL/throttle/commit cadence): HIGH — derived directly from the code's commit point, TTL default, and Phase 14 geocoding interval.

**Research date:** 2026-06-26
**Valid until:** ~2026-07-26 for the codebase findings (stable); the live HTML extraction detail should be re-validated whenever the operator captures the Wave-0 fixture (TripAdvisor markup can drift).
