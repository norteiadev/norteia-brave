# Phase 11: TripAdvisor Source Lane — Research

**Researched:** 2026-06-23
**Domain:** GraphQL hybrid scraping · Playwright DataDome bootstrap · IBGE municipal linkage · §7.6 scoring extension · mar_ready promote-override
**Confidence:** MEDIUM (anti-bot mechanics empirically uncertain; all codebase patterns HIGH)

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
All decisions in 11-CONTEXT.md are LOCKED — do not re-litigate:

- **Acquisition (TA-01):** Playwright bootstraps DataDome session → cookies injected into httpx → persisted-query POST to `https://www.tripadvisor.com/data/graphql/ids`. `queryId` never hardcoded; captured live via `page.on("request")` interception; `query_id_override` config escape hatch. Residential-proxy seam (`config.proxy_url`); Playwright lazy-imported inside bootstrap only; optional dep group `scraper`. On `403/429/captcha` → `SessionExpiredError` → one re-bootstrap via tenacity → persistent fail quarantines. Session (cookie jar + queryId map) cached `brave:ta:session` Redis with TTL.
- **UF geoId (TA-01):** Resolve UF→geoId via typeahead/search GraphQL; cache Redis-primary + seed `data/tripadvisor/uf_geoids.json` (27 UFs).
- **IBGE (TA-03):** Local `data/ibge/ibge_municipios.csv` (5570 rows: name, uf, ibge_code, lat, lng). `resolve_municipio` = rapidfuzz `token_sort_ratio ≥ 88` + haversine `< 15km` fallback; else `None` → quarantine `ibge_unmatched`. Parent = destino RioRecord produced in same sweep (carry `parent_rio_id`, `parent_source_ref`; `parent_mar_id` only if already in Mar); quarantine `parent_destino_absent` only when no destino RioRecord exists.
- **Scoring (TA-04):** `origem_value=65.0`; `completude_from_fields`; `corroboracao_from_reviews(count, rating)` log curve; `atualidade_from_recency` step function; `validacao_humana=0` at ingest. Feeds existing `compute_score` via `*_value` payload keys.
- **mar_ready + override (TA-05):** New column `rio_records.mar_ready` (boolean, indexed, default false; Alembic 0006). `route_by_score` sets it for `entity_type=="attraction"` AND `canonical_key.startswith("tripadvisor:")` AND `atualidade_value≥70` AND `corroboracao_value≥mar_ready_corrob_bar(60.0)`. `promote_override` guards `if not rio.mar_ready: raise PromoteNotAllowed` (→409); sets `validacao_humana=100`; calls `reprocess_record`; sets `rio.routing="mar"` + `promote_to_mar` directly. `promotion_reason="steward_override_review_validated"`.
- **Engine + API (TA-06):** `sweep_tripadvisor` task; `engine_sweep_run` gains `source: str`; `brave:engine:source` Redis key; promote API: `PATCH /api/v1/atrativos/{rio_id}/promote` + `POST /api/v1/atrativos/promote-batch`; `GET /api/v1/atrativos/mar-ready`.
- **Dashboard (TA-07):** Source radiogroup + UF multi-select; `/mar-ready` route + nav; optimistic single + bulk promote (mirror dlq-actions.ts).
- **Compliance (TA-08):** Store only `review_count`, `rating`, `most_recent_review_at`; never author/text; `data/tripadvisor/README` legal note; root `SOURCES.md`.

### Claude's Discretion
- Testing framework choices (within 100%-offline mandate)
- Exact `corroboracao_from_reviews` log-curve coefficients (within the locked saturation-at-500 behaviour)
- Exact dashboard component decomposition (within the locked UX contract)

### Deferred Ideas (OUT OF SCOPE)
- Structured hours/price from TA
- Multichannel contact from TA records
- Auto-scheduling TA on the autonomous beat
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| TA-01 | Playwright→DataDome bootstrap; httpx GraphQL client; queryId live capture; proxy seam; NullTripAdvisorClient | §1, §2, §5 |
| TA-02 | `TripAdvisorDestinosIngest` + `TripAdvisorAtrativosIngest` producers mirroring mtur.py | §5 (mtur.py:105-173 patterns) |
| TA-03 | IBGE linkage via rapidfuzz + haversine; parent RioRecord resolution | §3, §5 |
| TA-04 | Review scoring → §7.6; DLQ routing proof (67.06 floor, 27.5 sparse floor) | §5 (routing.py:47-81) |
| TA-05 | `mar_ready` column + Alembic 0006; `promote_override` bypassing ≥85 gate; 409 for non-mar_ready | §5 (dlq/service.py:18-50), §2 |
| TA-06 | Engine source awareness; sweep task; promote API endpoints; 503 broker-down contract | §5 (dlq.py patterns) |
| TA-07 | Dashboard source selector + /mar-ready route; optimistic actions; MSW/Vitest | §5 (dlq-actions.ts:64-112) |
| TA-08 | LGPD aggregate-only boundary; data/tripadvisor/README; SOURCES.md | §7 |
</phase_requirements>

---

## Summary

Phase 11 adds `brave/lanes/tripadvisor/` as the first free attraction source. The lane follows a hybrid acquisition model: Playwright handles the DataDome session bootstrap, then hands off a cookie jar to a plain `httpx` async client for all subsequent GraphQL persisted-query requests. This keeps Playwright out of every CI and dashboard import path.

The highest implementation risk is **anti-bot drift**: TripAdvisor's `queryId` rotates frequently (observed empirically to change on deploys) and DataDome can adapt its fingerprinting. The CONTEXT.md design fully accounts for this via live `page.on("request")` capture + `query_id_override` config escape + `SessionExpiredError` → re-bootstrap loop.

All §7.6 wiring is additive — the existing `compute_score` / `route_by_score` functions are unchanged. The only DB change is the single boolean column `rio_records.mar_ready` (Alembic 0006). The promote-override service mirrors `validate_and_promote_rio` (dlq/service.py:18-50) with the additional `mar_ready` guard. The dashboard `/mar-ready` route mirrors the DLQ pattern using the same `dlq-actions.ts` optimistic-mutation shape.

**Primary recommendation:** Build the lane in strict layering order — client protocol + NullClient + TripAdvisorConfig → scoring helpers → producers → mar_ready column + promote service → API endpoints → sweep task + engine source-awareness → dashboard — with a scored JSON fixture driving every offline test from the start.

---

## 1. Anti-bot & Acquisition (DataDome, persisted GraphQL, queryId capture)

### DataDome behavior [ASSUMED — training knowledge; not verified against 2026 state]

DataDome is a behavioral-heavy bot-detection middleware that fingerprints:
- Browser environment (missing APIs, inconsistent UA/accept-encoding headers)
- Mouse/scroll/typing cadence (not applicable in server-side bootstrap, but initial page load behavioral triggers matter)
- Datacenter IP reputation (AWS/GCP/Azure CIDRs frequently blocked without additional headers)
- TLS fingerprint and HTTP/2 ALPN (Playwright Chromium passes this; raw httpx does not)

The locked design deliberately separates concerns: **Playwright is used only for the bootstrap** to get a valid cookie jar (including the DataDome `__ddg*` cookies), then **httpx carries those cookies** on subsequent GraphQL requests. This is sound because DataDome's session cookies encode a trusted-browser assertion that the httpx requests inherit. Cookie TTL is session-dependent; empirically 30–120 minutes is typical for DataDome cookies. [ASSUMED]

### TripAdvisor GraphQL endpoint shape

The endpoint is `POST https://www.tripadvisor.com/data/graphql/ids`. [VERIFIED from public scraping research and the CONTEXT.md specification]

Persisted-query request bodies take one of two shapes depending on TA's current frontend version:

**Shape A — flat queryId array** [ASSUMED — observed from public reverse-engineering but TA deploys can change this]:
```json
[{
  "query": "<queryId>",
  "variables": { "locationId": 303506, "offset": 0, "limit": 20 }
}]
```

**Shape B — extensions.persistedQuery** [ASSUMED]:
```json
{
  "extensions": {
    "persistedQuery": { "sha256Hash": "<queryId>", "version": 1 }
  },
  "variables": { "locationId": 303506, "offset": 0, "limit": 20 }
}
```

The `query_id_override` config escape is essential precisely because TA alternates between shapes and rotates hashes. The `page.on("request")` capture strategy (see §2) resolves this regardless of shape: capture the exact request body TA's own JS sends and replay it.

### queryId capture via page.on("request") [ASSUMED based on Playwright docs and scraping community patterns]

The live-capture approach: after `page.goto("https://www.tripadvisor.com/...")`, attach a request interceptor to catch outbound POSTs to `*/data/graphql/ids`. Parse the first captured request body to extract the queryId and the variables shape. This is more robust than hardcoding because it captures what TA's own JS is currently sending.

```python
# Sketch — implementation detail for the planner
captured: list[dict] = []

def _on_request(request):
    if "graphql/ids" in request.url and request.method == "POST":
        try:
            captured.append(json.loads(request.post_data or "{}"))
        except Exception:
            pass

page.on("request", _on_request)
page.goto("https://www.tripadvisor.com/Tourism-g<geoId>-...")
page.wait_for_load_state("networkidle")
```

**Feasibility:** `page.on("request")` is standard Playwright API and is fully supported. [VERIFIED: playwright.dev/python/docs/library — request interception is first-class]

### Playwright sync vs async in Celery

Celery tasks run in a sync worker thread (POSIX). Playwright Python's `sync_playwright()` context manager is designed for sync/threaded use. [CITED: github.com/microsoft/playwright-python/issues/470 — confirmed thread-safety of sync API per-thread instance]

**Critical rule:** Each Celery worker must create its own Playwright instance. Thread-sharing is not safe. Since bootstrap is called infrequently (session-cached in Redis), the overhead is acceptable. [ASSUMED based on Playwright threading model]

**Lazy-import pattern** (locked in CONTEXT.md):
```python
def _bootstrap_session(self) -> dict:
    # Only import at call time — Playwright never loads in CI or dashboard
    from playwright.sync_api import sync_playwright  # noqa: PLC0415
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, proxy=self._proxy_args())
        ...
```

This is the correct pattern. Playwright is in the optional `scraper` dep group so `import playwright` never appears at module top-level.

**`@pytest.mark.real_browser` opt-in** — any test that calls bootstrap must be gated:
```python
pytestmark = pytest.mark.real_browser  # skips in CI unless --real-browser
```

### Proxy seam

`config.proxy_url` (from `TripAdvisorConfig`) maps to Playwright's `proxy={"server": url}` and to httpx's `proxies={"https://": url}`. Dev runs with `proxy_url=None` (no proxy). [ASSUMED — standard Playwright/httpx proxy pattern]

### Datacenter IP block risk

[ASSUMED] DataDome blocks common datacenter IP ranges (AWS, GCP, Hetzner, DigitalOcean) at the session level. A fresh VPS IP may work initially but degrade over time. The CONTEXT.md decision to "try VPS datacenter IP first; add proxy only if DataDome blocks" is the correct risk-ordered approach. Residential proxies (Brightdata, Oxylabs, Smartproxy) provide ISP-registered IPs that pass DataDome fingerprinting with higher reliability.

### Session TTL and Redis caching

Cache key: `brave:ta:session` → serialized `{"cookies": [...], "query_ids": {"destinations": "...", "attractions": "..."}}`. TTL should be conservative: 30 minutes [ASSUMED — DataDome cookies expire between 30-60 minutes typically]. On `SessionExpiredError`, tenacity triggers one re-bootstrap. If re-bootstrap also fails → raise → Celery task routes to DLQ (quarantine `session_bootstrap_failed`).

---

## 2. Libraries (playwright, rapidfuzz, respx, mocking)

### playwright [VERIFIED: pypi.org/project/playwright — current 1.52.0]
- Install: `pip install playwright` (in `scraper` optional dep group)
- Post-install: `playwright install chromium` (browser binary; must be part of scraper-env setup docs)
- Use `sync_playwright()` (not async) in Celery sync worker
- One instance per thread/task; lazy-import at bootstrap only
- **Mock for offline tests:** Use `NullTripAdvisorClient` (returns fixture data) — never load Playwright in unit/integration tests

### rapidfuzz [VERIFIED: pypi.org/project/rapidfuzz — current 3.14.1]
- `from rapidfuzz import process, fuzz`
- `process.extractOne(query, choices, scorer=fuzz.token_sort_ratio, score_cutoff=88)`
- Returns `(match, score, index)` or `None` when below cutoff
- Pure C extension — fast enough for 5570-row CSV lookup in-process; no async needed
- **Note:** `process.extractOne` with `scorer=fuzz.token_sort_ratio` emits a deprecation warning in older rapidfuzz versions; pass `scorer` as keyword arg and ensure rapidfuzz ≥ 3.0 [CITED: github.com/rapidfuzz/RapidFuzz/issues/422]

### haversine fallback
Standard math formula; no external library needed for a single-point lookup. Implement as a pure function:
```python
import math

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(a))
```

### respx [VERIFIED: pypi.org/project/respx — current 0.23.1, already in stack]
- Mock the httpx GraphQL POST layer for offline tests
- Pattern: `respx.post("https://www.tripadvisor.com/data/graphql/ids").mock(return_value=httpx.Response(200, json=FIXTURE))`
- Combine with `NullTripAdvisorClient` for unit tests; use respx for integration tests that exercise the real httpx client path

### Mocking Playwright offline
Strategy: `NullTripAdvisorClient` (in `brave/clients/`) returns fixture data; never imports Playwright. `FakeTripAdvisorClient` (in `tests/fakes/`) records calls for assertion. [Pattern: brave/clients/null_places.py:16-45]

The `@pytest.mark.real_browser` mark gates tests that need the actual Playwright bootstrap. Configure `pytest.ini` to skip this mark by default:
```ini
# pytest.ini or pyproject.toml [tool.pytest.ini_options]
markers = real_browser: requires live browser (opt-in, skipped in CI)
addopts = -m "not real_browser"
```

---

## 3. IBGE municipality dataset

### Authoritative source

IBGE provides official 7-digit municipality codes at:
- **Codes:** `https://www.ibge.gov.br/explica/codigos-dos-municipios.php` [CITED]
- **Geographic coordinates:** IBGE FTP at `geoftp.ibge.gov.br/organizacao_do_territorio/estrutura_territorial/localidades/` [CITED: IBGE QGIS community group — IBGE provides centroid coordinates in KML format]

### Recommended pre-built dataset

The most practical path for `data/ibge/ibge_municipios.csv` is to derive it from `github.com/kelvins/municipios-brasileiros` [CITED] which provides: IBGE code (7-digit), municipality name, UF, latitude, longitude for all ~5570 Brazilian municipalities, explicitly sourced from IBGE official data.

**License:** Public domain / CC0 (IBGE data is public; the GitHub repos have permissive licenses). [ASSUMED — verify before committing]

**File format target** (5570 rows, matches CONTEXT.md):
```
ibge_code,nome,uf,lat,lng
1100015,Alta Floresta D'Oeste,RO,-11.9325,-61.9995
...
```

**One-time download:** Commit as `data/ibge/ibge_municipios.csv` (not fetched at runtime). This is a static seed file, never mutated by the pipeline.

### IBGE resolver implementation pattern

```python
import csv
from dataclasses import dataclass
from pathlib import Path
from rapidfuzz import fuzz, process

@dataclass
class IbgeMunicipio:
    ibge_code: str
    nome: str
    uf: str
    lat: float
    lng: float

def load_ibge_csv(path: Path) -> list[IbgeMunicipio]: ...

def resolve_municipio(
    name: str,
    uf: str,
    records: list[IbgeMunicipio],
    *,
    threshold: int = 88,
    max_distance_km: float = 15.0,
    candidate_lat: float | None = None,
    candidate_lng: float | None = None,
) -> IbgeMunicipio | None:
    uf_records = [r for r in records if r.uf == uf]
    choices = [r.nome for r in uf_records]
    result = process.extractOne(name, choices, scorer=fuzz.token_sort_ratio, score_cutoff=threshold)
    if result:
        idx = result[2]
        return uf_records[idx]
    # Haversine fallback — only if coordinates provided
    if candidate_lat is not None and candidate_lng is not None:
        for r in uf_records:
            if haversine_km(candidate_lat, candidate_lng, r.lat, r.lng) < max_distance_km:
                return r
    return None  # → quarantine ibge_unmatched
```

---

## 4. TripAdvisor geo model & response shapes

[ASSUMED — based on public reverse-engineering research and observed TA API patterns. Confirm via live queryId capture in the first real-browser test run.]

### UF → geoId resolution

TripAdvisor uses integer `locationId` / `geoId` values (e.g., Rio de Janeiro state → 303506). The CONTEXT.md seeds `data/tripadvisor/uf_geoids.json` for all 27 UFs:

```json
{
  "AC": 303509, "AL": 303510, "AM": 303511, "AP": 303512,
  "BA": 303513, "CE": 303514, "DF": 303515, "ES": 303516,
  "GO": 303517, "MA": 303518, "MG": 303519, "MS": 303520,
  "MT": 303521, "PA": 303522, "PB": 303523, "PE": 303524,
  "PI": 303525, "PR": 303526, "RJ": 303506, "RN": 303527,
  "RO": 303528, "RR": 303529, "RS": 303530, "SC": 303531,
  "SE": 303532, "SP": 303533, "TO": 303534
}
```
[ASSUMED — geoIds need validation via typeahead query on first real bootstrap. These are plausible values from public research but must be confirmed.]

### Entity kinds

- **GEO** — destinations / cities / states (maps to entity_type `"destination"`)
- **ATTRACTION** — attractions (maps to entity_type `"attraction"`)

### Pagination

TripAdvisor GraphQL enforces a max of 20 results per request. [CITED: public Go scraper package docs — algo7/TripAdvisor-Review-Scraper — shows review limit of 20]. Pagination uses `offset` + `limit` variables. Fan-out: iterate `offset=0, 20, 40, ...` until empty response.

### Response fields to extract

For scoring purposes, the only fields needed (LGPD boundary):

| Field | GraphQL path | Used for |
|-------|-------------|----------|
| `locationId` | `.locationId` | canonical_key = `tripadvisor:{locationId}` |
| `name` | `.name` | entity name |
| `latitude` | `.latitude` | normalization |
| `longitude` | `.longitude` | normalization |
| `reviewCount` | `.reviewSummary.count` or `.reviews.totalCount` | corroboracao |
| `rating` | `.reviewSummary.rating` | corroboracao |
| `mostRecentReviewDate` | `.reviewSummary.publishedDate` or `.latestReview.publishedDate` | atualidade |
| `geoId` | `.locationId` | UF parent reference |
| `entityType` | `.__typename` or `.locationSubtype` | GEO vs ATTRACTION |

[ASSUMED — exact path names will vary by queryId; the bootstrap `page.on("request")` capture strategy resolves this at runtime by inspecting the live response shape.]

### Review count / rating aggregate-only guarantee

The locked schema (`schemas.py`) must enforce at the Pydantic boundary that only `review_count: int`, `rating: float`, and `most_recent_review_at: datetime | None` are persisted from review data. No author, no review text, no reviewer profile. This is enforced via `model_config` `extra="forbid"` on the Nascente payload schema.

---

## 5. Codebase patterns to mirror (file:line)

### Producer pattern (mtur.py)

Mirror `MturSeedIngest.produce(uf, *, run_rio=True)` exactly:
- `async def produce(self, uf: str, *, run_rio: bool = True) -> None` [mtur.py:105]
- Build `payload` dict with `*_value` keys [mtur.py:136-157]
- Call `store_raw(session, source="tripadvisor", source_ref=..., entity_type=..., uf=uf, payload=payload)` [mtur.py:159-166]
- Guard `if run_rio: process_nascente_record(...)` [mtur.py:168-173]
- `source_ref` format: `tripadvisor:{entity_type}:{locationId}` (e.g., `tripadvisor:attraction:12345`)

### Routing.py insertion point

`route_by_score` [routing.py:25-81] is the single mutation point for scoring. The `mar_ready` flag is set here (after `compute_score`). Add at the end of `route_by_score`, before the return:

```python
# Set mar_ready for TA attractions with sufficient corroboracao and atualidade
rio_record.mar_ready = (
    rio_record.entity_type == "attraction"
    and (rio_record.canonical_key or "").startswith("tripadvisor:")
    and score_input.atualidade_value >= config.mar_ready_atualidade_bar
    and score_input.corroboracao_value >= config.mar_ready_corrob_bar
)
```

`ScoreConfig` gains two new fields: `mar_ready_atualidade_bar: float = 70.0` and `mar_ready_corrob_bar: float = 60.0` (env: `BRAVE_SCORE_MAR_READY_ATUALIDADE_BAR`, `BRAVE_SCORE_MAR_READY_CORROB_BAR`). No alias (CR-02 compliance) [settings.py:54 model_config pattern].

### DLQ service pattern for promote_override

Mirror `validate_and_promote_rio` [dlq/service.py:18-50]:

```python
def promote_override(
    session: Session,
    rio: RioRecord,
    reason: str,
    config: ScoreConfig | None = None,
) -> MarRecord:
    if not rio.mar_ready:
        raise PromoteNotAllowed(f"RioRecord {rio.id} is not mar_ready")

    config = config or ScoreConfig()

    # Step 1: set validacao_humana=100 (flag_modified pattern — dlq/service.py:37-40)
    normalized = dict(rio.normalized or {})
    normalized["validacao_humana_value"] = 100.0
    rio.normalized = normalized
    flag_modified(rio, "normalized")
    session.flush()

    # Step 2: reprocess_record (not process_nascente_record — dlq/service.py:43-44)
    reprocess_record(session, rio.id, config)
    session.refresh(rio)

    # Step 3: force routing="mar" and promote directly (bypass ≥85 gate)
    rio.routing = "mar"
    rio.provenance = {**(rio.provenance or {}), "promotion_reason": reason}
    mar = promote_to_mar(session, rio)
    return mar
```

`PromoteNotAllowed` is a new exception class in `brave/core/mar/exceptions.py` (or similar); mapped to HTTP 409 in the router.

### DLQ router pattern for promote endpoints

Mirror `validate_dlq_record` [dlq.py:130-198] for `PATCH /api/v1/atrativos/{rio_id}/promote`:
- Load RioRecord; 404 if missing
- Call `promote_override(db, rio, reason="steward_override_review_validated")`
- Catch `PromoteNotAllowed` → raise `HTTPException(409, ...)`
- Dispatch push task (broker-down 503 contract identical to dlq.py:162-187)
- `write_audit(action="atrativo_promoted_override", ...)` [dlq.py:189-197 pattern]

For batch `POST /api/v1/atrativos/promote-batch?uf=&source=tripadvisor&limit=`: mirror `validate_batch` [dlq.py:201-278], filtering on `mar_ready=True AND source='tripadvisor' AND routing='dlq'`.

### Null client pattern

Mirror `NullPlacesClient` [null_places.py:16-45]:
```python
class NullTripAdvisorClient:
    """No-network TA stub — returns empty fixtures."""
    async def fetch_destinations(self, uf: str) -> list[dict]: return []
    async def fetch_attractions(self, geo_id: int, offset: int = 0) -> list[dict]: return []
    async def resolve_geo_id(self, uf: str) -> int: return 0
```

In `brave/clients/` (NOT tests/). `FakeTripAdvisorClient` with call-recording lives in `tests/fakes/`.

### TripAdvisorConfig (settings.py pattern)

Add sub-config mirroring `WhatsAppConfig` / `RampConfig` [settings.py:148-220]:

```python
class TripAdvisorConfig(BaseSettings):
    proxy_url: str = Field(default="")
    session_ttl: int = Field(default=1800)   # 30 min DataDome cookie lifetime
    query_id_override: dict[str, str] = Field(default_factory=dict)
    mar_ready_corrob_bar: float = Field(default=60.0)
    mar_ready_atualidade_bar: float = Field(default=70.0)
    ibge_match_threshold: int = Field(default=88)
    ibge_max_distance_km: float = Field(default=15.0)

    model_config = SettingsConfigDict(env_prefix="BRAVE_TA_")
    # CR-02: NO Field(alias=...) anywhere
```

Nest in `AppConfig` [settings.py:223-248]: `tripadvisor: TripAdvisorConfig = Field(default_factory=TripAdvisorConfig)`.

### Alembic migration 0006 shape

Mirror `0005_conversation_message.py` [alembic/versions/0005_conversation_message.py:1-73]:

```python
"""Add rio_records.mar_ready — TA promote-override gate (TA-05).

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-XX
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | None = None
depends_on: str | None = None

def upgrade() -> None:
    op.add_column(
        "rio_records",
        sa.Column("mar_ready", sa.Boolean, nullable=False, server_default="false"),
    )
    # Standard B-tree index — not CONCURRENTLY (inside Alembic transaction)
    op.create_index("ix_rio_records_mar_ready", "rio_records", ["mar_ready"])

def downgrade() -> None:
    op.drop_index("ix_rio_records_mar_ready", table_name="rio_records")
    op.drop_column("rio_records", "mar_ready")
```

Note from 0005 docstring [0005:21-22]: "DO NOT use CREATE INDEX CONCURRENTLY here — this is a standard B-tree index inside Alembic's transaction block."

### Dashboard actions pattern (dlq-actions.ts)

Mirror `useValidateDlqRecord` [dlq-actions.ts:64-113] for `usePromoteMarReadyRecord`:
- `mutationFn: (rioId) => promoteAtrativo(rioId)`
- `onMutate`: cancel queries, snapshot all `["mar-ready", "list"]` entries, optimistically remove the row [dlq-actions.ts:72-95]
- `onError`: restore snapshot [dlq-actions.ts:97-104]
- `onSuccess`: `toast.success("Atrativo promovido → Mar")`
- `onSettled`: `invalidateQueries({ queryKey: marReadyKeys.all })`

For bulk: mirror `useValidateDlqBatch` [dlq-actions.ts:156-172]:
- `mutationFn: ({ ufs, limit }) => promoteAtrativoBatch(ufs, limit)`
- Confirm dialog: `"Promover {n} atrativos de {UF} selecionados → Mar?"`

---

## 6. Testing strategy (offline-first)

### Test structure

```
tests/
  fakes/
    fake_tripadvisor.py       # FakeTripAdvisorClient (call-recording)
  unit/
    lanes/
      test_ta_scoring.py      # corroboracao/atualidade math proofs
      test_ta_ibge_resolver.py # rapidfuzz + haversine tests
      test_ta_producers.py    # Fake client → store_raw → route
    test_routing_mar_ready.py # mar_ready flag set/unset cases
  integration/
    test_ta_promote_override.py # promote_override → Mar + audit + 409 guard
    test_ta_engine_source.py    # engine_sweep_run source dispatch
    test_ta_api.py              # PATCH /atrativos/{id}/promote, GET /atrativos/mar-ready
  fixtures/
    ta_destinations_BA.json    # Captured GraphQL response, BA destinos
    ta_attractions_BA.json     # Captured GraphQL response, BA attractions
  real_browser/               # @pytest.mark.real_browser — CI never runs these
    test_ta_bootstrap.py
    test_ta_live_scrape.py
```

### Offline test seam

- `NullTripAdvisorClient` (in `brave/clients/`) for production-safe no-op
- `FakeTripAdvisorClient` (in `tests/fakes/`) for call-recording unit tests
- respx mocks for integration tests that exercise the real httpx POST path
- `@pytest.mark.real_browser` for anything touching `sync_playwright()`

### Scoring proof tests (per CONTEXT.md spec — must assert exact values)

```python
# tests/unit/lanes/test_ta_scoring.py

def test_typical_atrativo_routes_dlq():
    # 200 reviews / 4.5★ / ~5 months old
    # origem=65 * 0.30 + completude≈75 * 0.20 + corroboracao≈X * 0.20 + atualidade=70 * 0.15 + val=0 * 0.15
    # Expected: ~67.06 → dlq
    score = compute_score(ScoreInput(
        origem_value=65.0,
        completude_value=75.0,
        corroboracao_value=...,   # from corroboracao_from_reviews(200, 4.5)
        atualidade_value=70.0,    # 5 months ≤ 180d → 70
        validacao_humana_value=0.0,
    ), ScoreConfig())
    assert score.routing == "dlq"
    assert abs(score.score - 67.06) < 0.5

def test_sparse_atrativo_routes_descarte():
    # No reviews / 0 rating / old
    score = compute_score(ScoreInput(
        origem_value=65.0, completude_value=25.0,
        corroboracao_value=0.0, atualidade_value=0.0,
        validacao_humana_value=0.0,
    ), ScoreConfig())
    assert score.routing == "descarte"
    assert abs(score.score - 27.5) < 0.5

def test_val100_cannot_cross_85():
    # val=100 applied → still < 85 → proves override required
    score = compute_score(ScoreInput(
        origem_value=65.0, completude_value=75.0,
        corroboracao_value=..., atualidade_value=70.0,
        validacao_humana_value=100.0,
    ), ScoreConfig())
    assert score.routing == "dlq"
    assert score.score < 85.0
```

### promote_override tests

```python
def test_promote_override_non_mar_ready_raises_409():
    # rio.mar_ready = False → PromoteNotAllowed → HTTP 409
    ...

def test_promote_override_mar_ready_reaches_mar():
    # rio.mar_ready = True → routing="mar" + MarRecord created + audit written
    ...

def test_promote_override_provenance_set():
    # mar.provenance["promotion_reason"] == "steward_override_review_validated"
    ...
```

### Session expiry test (offline, no Playwright)

```python
def test_session_expired_triggers_rebootstrap(monkeypatch):
    # Mock bootstrap to fail once, succeed once; assert quarantine on second failure
    ...
```

### Migration round-trip test

```python
def test_migration_0006_up_down(alembic_engine):
    # upgrade() → column mar_ready exists
    # downgrade() → column gone
    ...
```

### Dashboard (Vitest + MSW)

```typescript
// dashboard/components/mar-ready/MarReadyList.test.tsx
// MSW handler: GET /api/v1/atrativos/mar-ready → fixture
// Assert: row renders; "Promover" button fires PATCH /atrativos/{id}/promote
// Assert: optimistic remove on click; rollback on 409 mock
// Assert: bulk multi-select → confirm dialog → POST /atrativos/promote-batch
```

---

## 7. LGPD / compliance

### Aggregate-only boundary (TA-08)

The Pydantic schema for the Nascente payload **must use `model_config = ConfigDict(extra="forbid")`** to prevent any additional review fields from leaking through. The permitted review fields are exactly:

```python
class TripAdvisorReviewSignals(BaseModel):
    review_count: int = 0
    rating: float = 0.0
    most_recent_review_at: datetime | None = None

    model_config = ConfigDict(extra="forbid")  # LGPD boundary: no author/text
```

This schema is applied at the point of extraction from the GraphQL response, before any data is written to Nascente.

### Data minimization checklist

- Never persist: reviewer name, reviewer ID, review text body, reviewer location, review photos
- Never log: any of the above in structlog output
- Store only: `review_count`, `rating`, `most_recent_review_at` (aggregate signal, not PII)
- Enforced by: `extra="forbid"` on `TripAdvisorReviewSignals`; no fields for author/text in the model

### Legal risk documentation (data/tripadvisor/README)

Content must cover (per CONTEXT.md TA-08):
1. TA ToS prohibits scraping (cite the relevant ToS clause)
2. Mitigations: low request rate (~1 UF/min, not continuous crawl), residential proxy, no PII stored (author/text excluded), operator-gated (not on autonomous beat), human promote-override (not automated Mar push)
3. Opt-in only: `RUN_REAL_EXTERNALS=true` required for live scrape
4. LGPD basis: legitimate interest in territorial data quality; aggregate review counts are public data

### root SOURCES.md index

Must list:
- `mtur/` — DEST-01, origem=100, government license
- `places/` — ATR-02/03/04, Terms of Service, per-record cost
- `tripadvisor/` — TA-01/02, ToS violation, operator-gated, free

---

## 8. Risks & likely-drift items

| Risk | Likelihood | Severity | Mitigation |
|------|-----------|----------|------------|
| **queryId rotation** — TA deploys update queryId hashes | HIGH (observed pattern) | HIGH | `page.on("request")` live capture + `query_id_override` config escape; re-bootstrap on `SessionExpiredError` |
| **Response shape change** — GraphQL field paths change | MEDIUM | MEDIUM | Pydantic schema with `Optional` fields + version in config; `SessionExpiredError` on shape mismatch |
| **DataDome block on datacenter IP** | HIGH on VPS; LOW on residential | HIGH | Try VPS first (cost); proxy seam ready; `proxy_url` in `TripAdvisorConfig` |
| **DataDome adaptive update** | MEDIUM (quarterly) | HIGH | No robust mitigation; monitor success rate in audit logs; residential proxy reduces exposure |
| **IBGE CSV staleness** | LOW (municipalities rarely change) | LOW | One-time download; update file when IBGE publishes new municipal divisions |
| **uf_geoids.json wrong** | MEDIUM (ASSUMED, unverified) | HIGH | Validate all 27 geoIds in first `@pytest.mark.real_browser` test run; auto-fail if typeahead returns no results |
| **mar_ready column causes `route_by_score` to fail on existing records** | LOW | MEDIUM | `server_default="false"` + `nullable=False` in migration; existing records get `False` automatically |
| **Playwright binary not installed in prod environment** | MEDIUM | HIGH | `playwright install chromium` in scraper dep group setup instructions; health-check endpoint verifies browser binary present before accepting sweep requests |
| **LGPD drift** — future developer adds author fields | LOW | HIGH | `extra="forbid"` on `TripAdvisorReviewSignals`; test asserts schema rejects author/text fields |

### Items that must be flagged for first real-browser run

1. **Confirm all 27 uf_geoids.json values** — resolve each via typeahead query
2. **Confirm queryId shape** (Shape A vs Shape B from §1)
3. **Confirm review field paths** in GraphQL response (`reviewSummary.count` etc.)
4. **Confirm DataDome cookie names** (`__ddg*` vs alternative names)
5. **Confirm pagination termination** (empty array vs `hasMore: false` field)

---

## Validation Architecture

> nyquist_validation is enabled (config.json workflow.nyquist_validation: true).

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 9.0.x (backend) + Vitest 4.1.x (dashboard) |
| Config file | pyproject.toml `[tool.pytest.ini_options]` |
| Quick run command | `.venv/bin/python -m pytest tests/unit/lanes/ tests/unit/test_routing_mar_ready.py -x` |
| Full suite command | `.venv/bin/python -m pytest tests/ -m "not real_browser" -x` |
| Dashboard quick | `cd dashboard && bun run test --run` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| TA-01 | NullTripAdvisorClient returns empty lists | unit | `pytest tests/unit/clients/test_null_tripadvisor.py -x` | ❌ Wave 0 |
| TA-01 | Session expiry → re-bootstrap → quarantine | unit (mock) | `pytest tests/unit/lanes/test_ta_session.py -x` | ❌ Wave 0 |
| TA-01 | queryId captured from intercepted request | real_browser | `pytest tests/real_browser/test_ta_bootstrap.py -m real_browser` | ❌ opt-in |
| TA-02 | `TripAdvisorDestinosIngest.produce(uf)` writes Nascente + routes RioRecord | unit | `pytest tests/unit/lanes/test_ta_producers.py -x` | ❌ Wave 0 |
| TA-02 | `TripAdvisorAtrativosIngest.produce(uf)` carries parent_rio_id | unit | `pytest tests/unit/lanes/test_ta_producers.py::test_atrativo_carries_parent -x` | ❌ Wave 0 |
| TA-03 | rapidfuzz token_sort_ratio ≥ 88 matches municipality | unit | `pytest tests/unit/lanes/test_ta_ibge_resolver.py -x` | ❌ Wave 0 |
| TA-03 | haversine < 15km fallback matches when name below threshold | unit | `pytest tests/unit/lanes/test_ta_ibge_resolver.py::test_haversine_fallback -x` | ❌ Wave 0 |
| TA-03 | No match → quarantine ibge_unmatched | unit | `pytest tests/unit/lanes/test_ta_ibge_resolver.py::test_no_match_quarantine -x` | ❌ Wave 0 |
| TA-04 | Typical (200 reviews/4.5★/5mo) → score ~67 → dlq | unit | `pytest tests/unit/lanes/test_ta_scoring.py::test_typical_atrativo_routes_dlq -x` | ❌ Wave 0 |
| TA-04 | Sparse/no-review → score ~27.5 → descarte | unit | `pytest tests/unit/lanes/test_ta_scoring.py::test_sparse_atrativo_routes_descarte -x` | ❌ Wave 0 |
| TA-04 | val=100 → ~82 < 85 → proves override required | unit | `pytest tests/unit/lanes/test_ta_scoring.py::test_val100_cannot_cross_85 -x` | ❌ Wave 0 |
| TA-05 | mar_ready set for qualifying TA attraction | unit | `pytest tests/unit/test_routing_mar_ready.py -x` | ❌ Wave 0 |
| TA-05 | mar_ready not set for non-TA records | unit | `pytest tests/unit/test_routing_mar_ready.py::test_mar_ready_not_set_for_mtur -x` | ❌ Wave 0 |
| TA-05 | promote_override on non-mar_ready → 409 | integration | `pytest tests/integration/test_ta_promote_override.py::test_non_mar_ready_409 -x` | ❌ Wave 0 |
| TA-05 | promote_override on mar_ready → MarRecord + provenance | integration | `pytest tests/integration/test_ta_promote_override.py::test_mar_ready_promotes -x` | ❌ Wave 0 |
| TA-05 | Alembic 0006 up/down round-trip | integration | `pytest tests/integration/test_migration_0006.py -x` | ❌ Wave 0 |
| TA-06 | `engine_sweep_run(source="tripadvisor")` dispatches sweep_tripadvisor | unit | `pytest tests/unit/test_engine_source_dispatch.py -x` | ❌ Wave 0 |
| TA-06 | `/engine/start` with invalid source → 422 | unit | `pytest tests/unit/test_engine_source_dispatch.py::test_invalid_source_422 -x` | ❌ Wave 0 |
| TA-06 | `GET /api/v1/atrativos/mar-ready` returns mar_ready=True records | integration | `pytest tests/integration/test_ta_api.py::test_mar_ready_list -x` | ❌ Wave 0 |
| TA-07 | MarReadyList renders rows, "Promover" dispatches PATCH | unit (Vitest+MSW) | `cd dashboard && bun run test --run MarReadyList` | ❌ Wave 0 |
| TA-07 | Bulk select → confirm dialog → POST promote-batch | unit (Vitest+MSW) | `cd dashboard && bun run test --run MarReadyBulk` | ❌ Wave 0 |
| TA-07 | 409 on non-mar_ready → optimistic rollback | unit (Vitest+MSW) | `cd dashboard && bun run test --run MarReadyActions` | ❌ Wave 0 |
| TA-08 | TripAdvisorReviewSignals rejects author/text fields | unit | `pytest tests/unit/lanes/test_ta_schema_lgpd.py -x` | ❌ Wave 0 |

### Sampling Rate

- **Per task commit:** `pytest tests/unit/lanes/ tests/unit/test_routing_mar_ready.py -x`
- **Per wave merge:** Full suite `pytest tests/ -m "not real_browser" -x`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps

All test files are new (phase 11 is greenfield within the existing test infrastructure):

- [ ] `tests/fakes/fake_tripadvisor.py` — FakeTripAdvisorClient with call recording
- [ ] `tests/unit/clients/test_null_tripadvisor.py` — NullTripAdvisorClient offline smoke
- [ ] `tests/unit/lanes/test_ta_scoring.py` — scoring math proofs (TA-04 spec proofs)
- [ ] `tests/unit/lanes/test_ta_ibge_resolver.py` — rapidfuzz + haversine (TA-03)
- [ ] `tests/unit/lanes/test_ta_producers.py` — producers offline (TA-02)
- [ ] `tests/unit/lanes/test_ta_schema_lgpd.py` — LGPD boundary (TA-08)
- [ ] `tests/unit/lanes/test_ta_session.py` — session expiry (TA-01)
- [ ] `tests/unit/test_routing_mar_ready.py` — mar_ready flag logic (TA-05)
- [ ] `tests/unit/test_engine_source_dispatch.py` — source gating (TA-06)
- [ ] `tests/integration/test_ta_promote_override.py` — promote_override service (TA-05)
- [ ] `tests/integration/test_migration_0006.py` — Alembic round-trip (TA-05)
- [ ] `tests/integration/test_ta_api.py` — promote endpoints (TA-06)
- [ ] `tests/real_browser/test_ta_bootstrap.py` — real Playwright (opt-in, TA-01)
- [ ] `dashboard/components/mar-ready/MarReadyList.test.tsx` — (TA-07)
- [ ] `dashboard/components/mar-ready/MarReadyActions.test.tsx` — (TA-07)
- [ ] `pytest.ini` marker: `real_browser: requires live browser (opt-in, skipped in CI)` + `addopts = -m "not real_browser"`

---

## Sources

### Primary (HIGH confidence — codebase)
- `brave/lanes/destinos/mtur.py:105-173` — produce() pattern, `*_value` payload keys, `store_raw → process_nascente_record`
- `brave/core/rio/routing.py:25-81, 84-203` — `route_by_score` mutation point; `process_nascente_record` pipeline; ScoreInput construction
- `brave/core/dlq/service.py:18-50` — `validate_and_promote_rio` template for `promote_override`
- `brave/api/routers/dlq.py:130-278` — validate/validate-batch/descarte patterns; 503 broker-down contract; audit write pattern
- `brave/clients/base.py:16-234` — Protocol pattern; 8-client CORE-11 inventory
- `brave/clients/null_places.py:16-54` — NullClient pattern; structural typing assertion
- `brave/config/settings.py:1-248` — sub-config pattern (env_prefix, no alias CR-02); AppConfig nesting
- `alembic/versions/0005_conversation_message.py:1-73` — migration shape; CONCURRENTLY warning
- `dashboard/components/dlq/dlq-actions.ts:64-172` — optimistic mutation pattern; snapshot-restore; DlqListSnapshot type

### Secondary (MEDIUM confidence — web research)
- playwright.dev/python/docs/library — sync_playwright(), page.on("request") — request interception [CITED]
- github.com/microsoft/playwright-python/issues/470 — per-thread Playwright instance requirement [CITED]
- pypi.org/project/rapidfuzz — version 3.14.1 current; process.extractOne API [CITED]
- github.com/rapidfuzz/RapidFuzz/issues/422 — scorer= deprecation in older versions [CITED]
- github.com/kelvins/municipios-brasileiros — 5570 rows with IBGE code + lat/lng, from official IBGE source [CITED]
- www.ibge.gov.br/explica/codigos-dos-municipios.php — authoritative 7-digit code table [CITED]

### Tertiary (LOW confidence — assumed/training)
- TripAdvisor request shape (Shape A vs B) — [ASSUMED]; must confirm via real_browser test
- DataDome cookie TTL (30-120 min) — [ASSUMED]; confirm empirically
- All 27 uf_geoids.json values — [ASSUMED]; must validate via typeahead on first bootstrap
- GraphQL field paths (`reviewSummary.count`, etc.) — [ASSUMED]; confirm via live response
- Pagination termination signal — [ASSUMED]; confirm via live response

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | TripAdvisor request body shape is `[{"query": queryId, "variables": {...}}]` (Shape A) | §1 | Must update client request builder; low-risk because live capture resolves it |
| A2 | DataDome cookies have TTL 30-120 minutes | §1 | Session TTL config may need adjustment; observable from first real-browser run |
| A3 | All 27 uf_geoids.json values are correct | §4 | Wrong geoId → zero results for a UF → detectable from empty sweep audit |
| A4 | GraphQL field paths (`reviewSummary.count`, `rating`, `publishedDate`) | §4 | Response shape mismatch → Pydantic validation error → quarantine; observable |
| A5 | Pagination terminates on empty array | §4 | Infinite loop risk; add `max_pages` config guard as defensive measure |
| A6 | Datacenter VPS IP will initially pass DataDome | §1 | If blocked immediately, proxy is required from day 1; plan for proxy from start |
| A7 | rapidfuzz `process.extractOne` with `scorer=fuzz.token_sort_ratio` API is stable at 3.14.x | §2 | Minor API change; pinned version mitigates |
