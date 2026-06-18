# Phase 7: Real Places Hardening + Targeted Atrativos Discovery + Mtur Refresh - Research

**Researched:** 2026-06-17
**Domain:** Google Places API (New) gRPC/REST SDK mechanics + destino→atrativo linking + Mtur dataset
**Confidence:** HIGH (field-mask mechanics from installed SDK source; Mtur from live WebSearch)

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** Add `X-Goog-FieldMask` to every Places API (New) call via `metadata=[("x-goog-fieldmask", "<mask>")]` kwarg.
- **D-02:** Populate `municipio_nome` + `municipio_ibge` from `addressComponents`; resolve IBGE by matching against loaded Mtur municipalities; fix `_resolve_parent_destino` to never fall through on empty ibge.
- **D-03:** Add `DiscoveryAgent.produce_for_destino(parent_mar, target_count=10)` targeted path; keep existing `produce(uf)`.
- **D-04:** Replace 16-row sample with current official Mapa do Turismo Brasileiro CSV; loader globs newest-by-filename.
- **D-05:** `scripts/loadtest_destinos_atrativos.py` drive ingest → promote 10 destinos → targeted discovery → print summary; acceptance = 10 MarRecords + ≥10 RioRecords per parent.
- **D-06:** Reuse the DLQ validate logic (`_validate_dlq_inline`: `normalized["validacao_humana_value"]=100.0` + `flag_modified` + `reprocess_record` + `promote_to_mar` if routing=mar). Do not re-implement.
- **D-07:** Cost guard `BRAVE_LLM_USD_DAILY_BUDGET=10.0` covers ~100 DeepSeek extractions; operator raises if `CostGuardError` trips.
- **D-08:** Suite stays 100% offline; new tests mock the async client; assert metadata field-mask; assert addressComponents parsing; CI keyless; real = `RUN_REAL_EXTERNALS=true` opt-in.

### Claude's Discretion
- Exact field-mask string contents (beyond required fields), município-name normalization (accents/casing) for name→IBGE matching, whether targeted discovery lives on DiscoveryAgent vs a thin sweep wrapper, harness CLI args (UF list, per-destino target count), commit granularity.

### Deferred Ideas (OUT OF SCOPE)
- Apify IG/X signal enrichment for atrativos.
- norteia-api push of the validated atrativos.
- Automatic WhatsApp owner-validation outreach.
- Full national Mtur fan-out scheduling.
- A name→IBGE service beyond the Mtur-table lookup.
</user_constraints>

---

## Summary

Phase 7 fixes four concrete bugs surfaced by a real load-test attempt. The deepest one is the missing `X-Goog-FieldMask` on `search_text` calls (live 400 error). A second bug is that `place_details` already passes `metadata` but uses the wrong field-mask prefix (`places.id` instead of `id`), which likely causes no fields to be returned. A third is that `text_search` results never carry `municipio_ibge/nome` so `_resolve_parent_destino` is called with `municipio_ibge=""`, matching any destination via `source_ref.contains("")`. Fourth, `produce(uf)` is a UF-wide sweep that cannot guarantee per-destino volume.

All four are mechanical fixes with no schema changes. The steward→Mar promotion path is already implemented in `brave/api/routers/dlq.py` (`validate_dlq_record` / batch variant) and can be extracted as a standalone function for the harness. The Mtur dataset situation requires a manual download from `mapa.turismo.gov.br` (Excel export → CSV conversion) because no machine-readable direct-download URL exists.

**Primary recommendation:** Fix the two field-mask issues first (they block every real Places call), then wire `addressComponents` → município mapping, then add `produce_for_destino`, then write the harness.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Places API field-mask compliance | API/Backend (collector) | — | `RealPlacesClient` is the sole caller; fix lives in `brave/clients/places.py` |
| `municipio_ibge` population | API/Backend (collector) | — | Parsing `addressComponents` belongs in the client layer that receives the raw proto response |
| Name→IBGE lookup | API/Backend (collector) | — | In-process match against the already-loaded Mtur table; no extra API |
| Targeted per-destino discovery | API/Backend (collector) | — | `DiscoveryAgent.produce_for_destino` — lane logic, same tier as existing `produce(uf)` |
| DLQ→Mar promotion | API/Backend (collector) | — | Existing `dlq.py` logic; harness calls the extracted helper directly (no HTTP) |
| Mtur dataset refresh | Data / Storage | — | Bundled CSV file in `data/mtur/`; loader (`brave/clients/mtur.py`) already globs by filename |
| Load-test harness | Scripts | — | `scripts/loadtest_destinos_atrativos.py`; mirrors `scripts/ingest_destinos.py` pattern |

---

## RQ-1: Field Mask Mechanics (google-maps-places 0.9.0)

### Verified Call Signatures [VERIFIED: installed .venv source]

Both `search_text` and `get_place` on `PlacesAsyncClient` accept a `metadata` keyword argument:

```python
# async_client.py lines 366-415 (search_text)
async def search_text(
    self,
    request: Optional[Union[places_service.SearchTextRequest, dict]] = None,
    *,
    retry: OptionalRetry = gapic_v1.method.DEFAULT,
    timeout: Union[float, object] = gapic_v1.method.DEFAULT,
    metadata: Sequence[Tuple[str, Union[str, bytes]]] = (),
) -> places_service.SearchTextResponse: ...

# async_client.py lines 507-591 (get_place)
async def get_place(
    self,
    request: Optional[Union[places_service.GetPlaceRequest, dict]] = None,
    *,
    name: Optional[str] = None,
    retry: OptionalRetry = gapic_v1.method.DEFAULT,
    timeout: Union[float, object] = gapic_v1.method.DEFAULT,
    metadata: Sequence[Tuple[str, Union[str, bytes]]] = (),
) -> place.Place: ...
```

The `metadata` parameter is `Sequence[Tuple[str, Union[str, bytes]]]`. The correct call pattern is:

```python
response = await client.search_text(
    request,
    metadata=[("x-goog-fieldmask", "places.id,places.displayName,...")]
)

place = await client.get_place(
    request,
    metadata=[("x-goog-fieldmask", "id,displayName,...")]
)
```

### Critical: Different prefix for search_text vs get_place [VERIFIED: official docs + REST transport source]

`search_text` response is `SearchTextResponse` with a repeated `.places` field — the mask MUST use the `places.` prefix:

```
X-Goog-FieldMask: places.id,places.displayName,places.formattedAddress,...
```

`get_place` response is a single `Place` object — the mask uses field names directly, NO `places.` prefix:

```
X-Goog-FieldMask: id,displayName,formattedAddress,...
```

The REST transport source (`transports/rest.py` line 376) explicitly states: "every request requires a field mask set outside of the request proto via the HTTP header `X-Goog-FieldMask`." [VERIFIED: installed .venv source at `.venv/lib/python3.12/site-packages/google/maps/places_v1/services/places/transports/rest.py:376`]

### Bug in existing `place_details` [VERIFIED: code read]

`brave/clients/places.py` lines 201-208 defines `field_mask` for `place_details` with `places.id, places.displayName, ...` — this `places.` prefix is WRONG for `get_place`. The correct mask for `get_place` must NOT have the prefix. This is a silent bug (no 400, but fields likely returned empty/default). Fix required alongside the `search_text` 400 fix.

### Correct Field Mask Strings [VERIFIED: proto source + official docs]

**`text_search` mask** (must include `addressComponents` for D-02):
```
places.id,places.displayName,places.formattedAddress,places.types,places.location,places.addressComponents
```

**`get_place` mask** (SignalAgent fields; no `places.` prefix; `regularOpeningHours` for stable hours, `reviews` for `publish_time`):
```
id,displayName,formattedAddress,types,location,addressComponents,businessStatus,regularOpeningHours,reviews,internationalPhoneNumber,websiteUri
```

Note: The existing `place_details` code accesses `place.current_opening_hours.weekday_descriptions`. `current_opening_hours` (field 46) reflects current week including exceptions; `regularOpeningHours` (field 21) is the permanent schedule. Either works for `weekday_descriptions`. The mask should request `regularOpeningHours` (more stable, lower cost tier) and the code should be updated to access `place.regular_opening_hours.weekday_descriptions`.

### Exact Proto Field Names [VERIFIED: `.venv/lib/python3.12/site-packages/google/maps/places_v1/types/place.py` and `review.py`]

| Proto attribute (Python) | Field mask string token | Notes |
|--------------------------|------------------------|-------|
| `place.address_components` | `addressComponents` (no prefix for get_place) | `RepeatedField[AddressComponent]` |
| `place.address_components[i].long_text` | — | Full text of the component |
| `place.address_components[i].short_text` | — | Abbreviated (e.g. state initials) |
| `place.address_components[i].types` | — | `list[str]`, e.g. `["administrative_area_level_2","political"]` |
| `place.regular_opening_hours.weekday_descriptions` | `regularOpeningHours` | `list[str]`, 7 elements |
| `place.business_status` | `businessStatus` | Enum: `OPERATIONAL`, `CLOSED_PERMANENTLY`, `CLOSED_TEMPORARILY`, `FUTURE_OPENING` |
| `review.publish_time` | `reviews` | `google.protobuf.timestamp_pb2.Timestamp` |
| `review.text` | `reviews` | `LocalizedText`; access `.text` attribute for the string |
| `review.rating` | `reviews` | `float` |

`review.publish_time` is a protobuf `Timestamp`. Accessing `.isoformat()` directly will fail — it must be converted via `review.publish_time.ToDatetime(tzinfo=timezone.utc).isoformat()` or `datetime.fromtimestamp(review.publish_time.seconds, tz=timezone.utc).isoformat()`. The existing `place_details` code calls `review.publish_time.isoformat()` directly (line 225) which works only if proto-plus auto-converts — verify with a real call or mock test. [ASSUMED: proto-plus Timestamp may auto-convert to Python datetime; confirm in unit test with a canned proto fixture]

### GetPlaceRequest.name format [VERIFIED: installed .venv source + async_client.py]

`get_place` accepts either `request=GetPlaceRequest(name="places/{place_id}")` or the shorthand `name="places/{place_id}"` keyword arg. The existing code uses the request form — correct. The `get_place` async client also appends the `name` as a routing header in metadata (lines 575-576 of async_client.py), so passing both request and the name keyword argument will raise `ValueError` (line 551-555) — the existing code correctly avoids this.

---

## RQ-2: addressComponents → município (D-02)

### Structure [VERIFIED: `.venv/.../types/place.py` class AddressComponent]

Each element in `place.address_components` has:
- `long_text: str` — full name (e.g. "Salvador")
- `short_text: str` — abbreviation where applicable
- `types: list[str]` — list of type strings

Relevant type strings for BR addresses:
- `"administrative_area_level_2"` → **município name** (long_text)
- `"administrative_area_level_1"` → **state name** (long_text) / `"BA"` (short_text)
- `"country"` → `"Brazil"` (long_text) / `"BR"` (short_text)

Example parsing function:

```python
def _extract_municipio_nome(address_components: list) -> str:
    """Extract município name from Places API addressComponents."""
    for component in address_components:
        if "administrative_area_level_2" in component.types:
            return component.long_text
    return ""
```

### API does NOT return IBGE codes [VERIFIED: Google Places API docs — no IBGE field exists]

The Places API has no IBGE code field. The name→IBGE lookup must be done in-process against the loaded Mtur municipality table.

### Name→IBGE lookup strategy [ASSUMED: normalization approach]

The Mtur CSV contains `no_municipio` (the municipality name) and `co_municipio` (IBGE code). The `MturClient.fetch_municipalities` returns `{"ibge_code": ..., "name": ..., "categoria": ..., "uf": ...}`.

Normalization for matching:
1. Lowercase both strings
2. Strip accents using `unicodedata.normalize('NFD', s).encode('ascii', 'ignore').decode()` or the `Unidecode` / `unicodedata` stdlib approach
3. Strip leading/trailing whitespace

The comparison key is `(normalized_name, uf)` — UF is always available from the search context. Build a dict `{(normalized_name, uf): ibge_code}` once from the full Mtur table (all UFs) on first use.

**Risk:** Places API may return the comarca/micro-region name instead of the exact município name (e.g. "Costa do Descobrimento" instead of "Porto Seguro"). In that case the lookup returns None → quarantine `parent_destino_absent` (correct behavior per D-02). This is expected and acceptable — it means that municipality's Places results simply won't link until the Mtur table has an entry. [ASSUMED: exact match will work for most Oferta Principal destinos since they are large, well-known cities]

### `_resolve_parent_destino` fix (the `contains("")` bug) [VERIFIED: code read]

Current code at `discovery_agent.py` lines 130-153:
- Primary lookup: `MarRecord.source_ref.contains(municipio_ibge)` where `municipio_ibge` is always `""` (never populated) → `source_ref.contains("")` matches ANY string → returns the first Mar destination regardless of UF/municipality.
- Fallback: `source_ref.startswith(f"mtur:{uf}:")` — this fires when primary returns None, which never happens due to the `contains("")` bug.

**Fix**: Guard at the top of `_resolve_parent_destino` — if `municipio_ibge` is empty/blank, return `None` immediately (don't query). This ensures the `parent_destino_absent` quarantine fires correctly.

---

## RQ-3: Targeted per-município discovery (D-03)

### New method: `produce_for_destino`

Recommended signature:

```python
async def produce_for_destino(
    self,
    parent_mar: MarRecord,
    target_count: int = 10,
) -> int:
    """Run targeted Places discovery for a single Mar destino.
    
    Queries: "pontos turísticos em {municipio_nome} {uf}"
             "o que fazer em {municipio_nome} {uf}"  (if needed to reach target_count)
    
    Args:
        parent_mar: Active MarRecord with entity_type="destination".
        target_count: Desired number of distinct atrativos (20 per query max).
    
    Returns:
        Count of Rio records created/existing for this parent.
    """
```

`municipio_nome` comes from `parent_mar.canonical.get("municipio") or parent_mar.canonical.get("name")` — the Mtur ingest stores `"municipio": name` in `canonical` (see `mtur.py` line 144).

**Query design:** 2 queries × up to 20 results = up to 40 Places results per município. After LLM extraction and dedup (idempotent `store_raw`), ≥10 distinct atrativos per município should be achievable for any Mtur Oferta Principal city. Both queries link directly to the known `parent_mar` — no `_resolve_parent_destino` call needed (skip that path entirely for the targeted case). The existing `_resolve_parent_destino` is still called from `produce(uf)` but with the D-02 empty-ibge guard in place.

**Parent linking:** Store `parent_mar_id=str(parent_mar.id)` in the payload directly — same as the existing `produce(uf)` path (lines 308-309 of `discovery_agent.py`). The `municipio_ibge` is available from `parent_mar.canonical.get("ibge_code")`.

**De-duplication:** `store_raw` is already idempotent by `(source, source_ref, content_hash)`. Running `produce_for_destino` twice for the same municipality is a no-op.

### Keeping the existing `produce(uf)` intact

The harness uses `produce_for_destino`. The existing `produce(uf)` is still reachable from `discover_atrativo_task` (Celery task in `brave/tasks/pipeline.py`) and from the ops sweep endpoint. It should receive the D-02 empty-ibge guard fix, but its call structure (UF-wide queries) is unchanged.

---

## RQ-4: Steward→Mar Promotion for the Harness (D-06)

### Located function [VERIFIED: code read of `brave/api/routers/dlq.py`]

The validate logic is NOT a standalone service function — it is inlined in two places:
1. `validate_dlq_record` (single-record, lines 127-193) in `dlq.py`
2. The inner loop of `validate_batch` (batch, lines 196-267) in `dlq.py`

Both implement the same 4-step pattern:
```python
# Step 1: set validacao_humana=100 + flag_modified
normalized = dict(rio.normalized or {})
normalized["validacao_humana_value"] = 100.0
rio.normalized = normalized
flag_modified(rio, "normalized")
db.flush()

# Step 2: re-score via reprocess_record (NOT process_nascente_record — see docstring pitfall)
from brave.core.rio.routing import reprocess_record
reprocess_record(db, rio.id, ScoreConfig())
db.refresh(rio)

# Step 3: promote to Mar if routing == 'mar'
if rio.routing == "mar":
    from brave.core.mar.service import promote_to_mar
    promote_to_mar(db, rio)

# Step 4: write audit row
```

### Recommended approach for the harness (D-06)

**Option A (simplest):** Extract a `_validate_and_promote(session, rio)` helper from `dlq.py` and call it from both the router and the harness. This avoids HTTP overhead and re-implements nothing.

**Option B:** The harness calls `validate_batch` logic directly by importing `reprocess_record` and `promote_to_mar`. Same 4 steps above.

**Planner should choose A** — extract a standalone `validate_and_promote_rio(session: Session, rio: RioRecord, config: ScoreConfig) -> MarRecord | None` function in a new or existing service module (e.g. `brave/core/dlq/service.py`), then have `dlq.py` router call it. The harness imports it directly. This is a pure refactor with no behavior change.

### Why NOT `process_nascente_record` [VERIFIED: dlq.py docstring + routing.py code read]

`process_nascente_record` (routing.py line 84) returns early if `canonical_key` already exists for the record — it does not re-score. The function is for first-time processing only. Re-scoring after human validation requires `reprocess_record` (routing.py line 200), which resets `routing="in_progress"` then calls `route_by_score`.

---

## RQ-5: Mtur Dataset Refresh (D-04)

### Current state [VERIFIED: code read of `data/mtur/municipios_mtur_2024.csv`]

The file `data/mtur/municipios_mtur_2024.csv` has 16 rows (a hand-curated sample for BA, RJ, SP) plus a header. Schema: `co_municipio,no_municipio,sg_uf,categoria,no_regiao_turistica`. The `_load_csv` function globs `municipios_mtur_*.csv` and picks the newest by filename sort.

### Official source [CITED: mapa.turismo.gov.br, Portaria MTUR nº 9/2025]

The Mapa do Turismo Brasileiro is published by Ministério do Turismo under **Portaria MTUR nº 9, de 24 de abril de 2025**. [CITED: https://www.gov.br/turismo/pt-br/assuntos/noticias/ministerio-do-turismo-apresenta-nova-categorizacao-dos-municipios-no-mapa-do-turismo-brasileiro]

**No direct programmatic download URL exists.** [VERIFIED: WebSearch + WebFetch of dados.gov.br, dados.turismo.gov.br, basedosdados.org] The official portal at `mapa.turismo.gov.br` provides a "Município Categorizado Detalhado Excel" export UI with year/macroregion/UF filters. This is a manual browser-driven download.

### 2025 category nomenclature [CITED: mapa.turismo.gov.br portal + Portaria 9/2025]

Old A/B → **"Municípios turísticos"** (highest visitor flow)
Old C/D → **"Municípios com oferta turística complementar"**
Old E → **"Municípios de apoio ao turismo"**

The existing `_map_categoria` already handles both old and new nomenclature (see `mtur.py` lines 56-83). No code change needed for the categoria mapping.

### Acquiring the full dataset [CITED: mapa.turismo.gov.br]

Manual steps (operator runs once):
1. Go to `https://www.mapa.turismo.gov.br/mapa/init.html`
2. Select "Municípios Categorizados" → "Município Categorizado Detalhado Excel"
3. Select year **2025**, macroregion = All, UF = All
4. Download XLSX
5. Convert to CSV with the existing schema:
   `co_municipio,no_municipio,sg_uf,categoria,no_regiao_turistica`
6. Save as `data/mtur/municipios_mtur_2025.csv`
7. The loader auto-picks it (newest filename wins)

**Column mapping from XLSX → CSV schema [ASSUMED: column names based on previous releases; verify against actual XLSX]:**
- IBGE code → `co_municipio` (7-digit integer)
- Municipality name → `no_municipio`
- UF → `sg_uf`
- Category → `categoria` (use raw string; `_map_categoria` normalizes it)
- Tourism region name → `no_regiao_turistica`

**Fallback:** If the operator cannot obtain the 2025 dataset before the harness run, the existing `municipios_mtur_2024.csv` is sufficient for the 10-destino acceptance test (BA has Porto Seguro, Ilhéus, Camaçari etc). The harness should pick a UF present in the loaded file and print a warning if fewer than 10 Oferta Principal/Complementar destinos are available.

**Licensing:** The Mapa do Turismo Brasileiro is a Brazilian federal government open data publication. Use is free for informational purposes; the `data/mtur/README` should note the portaria and download date. [CITED: dados.gov.br — government open data, default license is CC-BY or public domain for federal data]

---

## RQ-6: Offline Testing the Field Mask + município mapping (D-08)

### Approach [ASSUMED: test pattern based on existing discovery_agent tests + Python mocking]

The `PlacesAsyncClient` is constructed via `places_v1.PlacesAsyncClient(client_options={"api_key": ...})`. For unit testing, monkeypatch `google.maps.places_v1.PlacesAsyncClient` to return a Mock, and mock `search_text`/`get_place` as `AsyncMock`.

```python
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

@pytest.fixture
def mock_places_async_client():
    """Mock PlacesAsyncClient to capture metadata kwarg."""
    mock_client = MagicMock()
    mock_client.search_text = AsyncMock(return_value=_make_search_response())
    mock_client.get_place = AsyncMock(return_value=_make_place_response())
    return mock_client
```

### Field mask assertion pattern

The `metadata` kwarg is passed as a keyword argument. With `AsyncMock`, assert via:

```python
call_kwargs = mock_client.search_text.call_args.kwargs
metadata = dict(call_kwargs.get("metadata", []))
assert "x-goog-fieldmask" in metadata
assert "addressComponents" in metadata["x-goog-fieldmask"]
assert metadata["x-goog-fieldmask"].startswith("places.")  # text_search prefix

# For get_place:
call_kwargs = mock_client.get_place.call_args.kwargs
metadata = dict(call_kwargs.get("metadata", []))
assert "x-goog-fieldmask" in metadata
assert not metadata["x-goog-fieldmask"].startswith("places.")  # no prefix
assert "regularOpeningHours" in metadata["x-goog-fieldmask"]
```

### Proto-like response fixture construction [ASSUMED: proto-plus message construction]

Proto-plus messages (used by google-maps-places 0.9.0) can be constructed by import if grpc is available, or mocked as `MagicMock` with attributes for unit tests. The test does not need a real gRPC connection:

```python
def _make_search_response():
    """Canned search response with addressComponents."""
    place = MagicMock()
    place.id = "ChIJtest001"
    place.display_name.text = "Praia de Trancoso"
    place.formatted_address = "Trancoso, Porto Seguro - BA, Brasil"
    place.types = ["tourist_attraction", "point_of_interest"]
    place.location.latitude = -16.57
    place.location.longitude = -39.08
    # addressComponents with administrative_area_level_2
    comp_municipio = MagicMock()
    comp_municipio.long_text = "Porto Seguro"
    comp_municipio.types = ["administrative_area_level_2", "political"]
    comp_state = MagicMock()
    comp_state.long_text = "Bahia"
    comp_state.short_text = "BA"
    comp_state.types = ["administrative_area_level_1", "political"]
    place.address_components = [comp_municipio, comp_state]
    
    response = MagicMock()
    response.places = [place]
    return response
```

### FakePlacesClient changes needed

`FakePlacesClient` (`tests/fakes/fake_places.py`) does NOT currently record or validate the `metadata` kwarg. For D-08 unit tests that test the `RealPlacesClient` field mask, the test should mock the underlying `PlacesAsyncClient` directly (not use `FakePlacesClient`). `FakePlacesClient` is used for `DiscoveryAgent` tests (which test the agent logic, not the SDK call shape).

To test that `DiscoveryAgent` correctly passes `municipio_nome` / `municipio_ibge` from the Places result to `_resolve_parent_destino`, the existing `FakePlacesClient` just needs its `fixture_results` to include `municipio_ibge` and `municipio_nome` fields in the returned place dicts. The existing `_make_places_result` helper in `test_discovery_agent.py` already sets these — no change to `FakePlacesClient` itself, only to test fixtures.

---

## RQ-7: Cost/Scale (D-07)

### Cost guard path [VERIFIED: code read of `brave/config/settings.py`]

`LLMConfig.usd_daily_budget: float = 10.0` (default). Override via environment variable `BRAVE_LLM_USD_DAILY_BUDGET=<float>`. The cost guard raises `CostGuardError` on breach (enforced in `brave/clients/llm.py` RealLLMClient).

### Estimation for 10-destino load test [ASSUMED: DeepSeek pricing as of 2025]

- `text_search` calls: ~2 queries × 10 destinos = 20 requests (no LLM cost, only Places API billing)
- LLM extraction: up to 200 Places results × 1 extraction each ≈ 200 DeepSeek calls
- DeepSeek deepseek-chat cost: ~$0.14/M input tokens + $0.28/M output. Each extraction ≈ 400 tokens in + 200 out ≈ $0.000112 per call
- 200 calls × $0.000112 ≈ **$0.022** — well within the $10.00 daily budget
- `place_details` (Places API): up to 200 calls (billed by Google, not by the cost guard)

No changes needed for D-07. Operator note: if `CostGuardError` fires unexpectedly (e.g. due to accumulated prior calls in the same UTC day), set `BRAVE_LLM_USD_DAILY_BUDGET=50.0` and re-run.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead |
|---------|-------------|-------------|
| Field mask string | Ad-hoc string concat | Constant strings defined once in `RealPlacesClient` |
| Text accent normalization | Custom regex | `unicodedata.normalize('NFD', s).encode('ascii', 'ignore').decode().lower()` — stdlib only |
| DLQ→Mar promotion logic | Duplicate scoring logic in harness | Extract `validate_and_promote_rio()` from existing `dlq.py` inline code |
| IBGE lookup table | External API call | In-process dict built from `MturClient._load_csv()` at startup |
| Proto Timestamp to datetime | Custom parsing | `datetime.fromtimestamp(ts.seconds, tz=timezone.utc)` (stdlib) |

---

## Common Pitfalls

### Pitfall 1: Wrong field mask prefix for get_place
**What goes wrong:** Using `places.id,places.displayName,...` as the mask for `get_place` — no error is raised but the response fields come back empty/default since the mask tokens don't match any field of a single `Place`.
**Why it happens:** `search_text` returns `SearchTextResponse.places[]` so the `places.` prefix is required there; it is NOT required for `get_place` which returns a bare `Place`.
**How to avoid:** Use `id,displayName,...` (no prefix) for `get_place`. Add a unit test that asserts the mask string does NOT start with `places.` for the `get_place` call.
**Warning signs:** `place_details` returns a result dict with empty `name`, `business_status="UNKNOWN"`, empty `reviews`.

### Pitfall 2: `source_ref.contains("")` matches everything
**What goes wrong:** `_resolve_parent_destino` called with `municipio_ibge=""` → `MarRecord.source_ref.contains("")` is true for every non-null string → returns the first Mar destination in the query order (non-deterministic across replays).
**Why it happens:** `text_search` results never contained `municipio_ibge` because `addressComponents` was never requested (no field mask → 400, never got a response).
**How to avoid:** Add early-return guard: `if not municipio_ibge: return None`. Unit test that assert quarantine fires for empty ibge.

### Pitfall 3: `flag_modified` omission on JSON mutation
**What goes wrong:** `rio.normalized["validacao_humana_value"] = 100.0` in-place without `flag_modified` — SQLAlchemy does not track in-place dict mutations, so the change is never flushed to the DB.
**Why it happens:** SQLAlchemy JSON columns require explicit dirty-marking for mutable sub-structures.
**How to avoid:** Always `normalized = dict(rio.normalized or {}); ...; rio.normalized = normalized; flag_modified(rio, "normalized")`. Already correctly implemented in `dlq.py` — copy the pattern exactly.

### Pitfall 4: Calling `process_nascente_record` instead of `reprocess_record` for re-scoring
**What goes wrong:** `process_nascente_record` returns the existing Rio record immediately if `canonical_key` already exists — it does NOT re-score. The validacao_humana update is written but never acted on.
**Why it happens:** `process_nascente_record` is first-time ingest only.
**How to avoid:** Use `reprocess_record(session, rio.id, ScoreConfig())` for re-scoring. Already documented as "Pitfall 4" in `dlq.py` docstring.

### Pitfall 5: `publish_time` is a proto Timestamp, not a datetime
**What goes wrong:** `review.publish_time.isoformat()` raises `AttributeError` — protobuf `Timestamp` objects don't have `.isoformat()`.
**Why it happens:** Proto-plus may auto-convert to Python datetime in some versions; in others it stays as Timestamp. The behavior depends on proto-plus version.
**How to avoid:** Use `review.publish_time.ToDatetime(tzinfo=timezone.utc).isoformat()` (always safe) or check the type first. Alternatively keep the existing code and add a unit test with a real mock Timestamp to catch the exception.

### Pitfall 6: Harness DB not clean → duplicate counts
**What goes wrong:** Running the harness a second time on a non-reset DB inflates counts; the summary prints totals not per-run deltas.
**Why it happens:** The harness is designed as additive (no auto-truncate per D-05).
**How to avoid:** Document that operator must run `TRUNCATE nascente_records, rio_records, mar_records CASCADE;` before a fresh harness run. Print a header warning in the harness if MarRecords for the target UF already exist.

---

## Code Examples

### D-01 fix: `text_search` with field mask

```python
# brave/clients/places.py — RealPlacesClient.text_search
# Source: VERIFIED from installed async_client.py + official Places API docs

_TEXT_SEARCH_FIELD_MASK = (
    "places.id,"
    "places.displayName,"
    "places.formattedAddress,"
    "places.types,"
    "places.location,"
    "places.addressComponents"
)

response = await client.search_text(
    request,
    metadata=[("x-goog-fieldmask", _TEXT_SEARCH_FIELD_MASK)],
)
```

### D-01 fix: `place_details` with corrected field mask (no `places.` prefix)

```python
# brave/clients/places.py — RealPlacesClient.place_details
# Source: VERIFIED from installed async_client.py + official Places API docs

_GET_PLACE_FIELD_MASK = (
    "id,"
    "displayName,"
    "formattedAddress,"
    "types,"
    "location,"
    "addressComponents,"
    "businessStatus,"
    "regularOpeningHours,"
    "reviews,"
    "internationalPhoneNumber,"
    "websiteUri"
)

place = await client.get_place(
    request,
    metadata=[("x-goog-fieldmask", _GET_PLACE_FIELD_MASK)],
)
```

### D-02: addressComponents → município

```python
# brave/clients/places.py — inside text_search result builder
# Source: VERIFIED from installed place.py AddressComponent class

def _extract_municipio_from_components(address_components) -> tuple[str, str]:
    """Return (municipio_nome, uf_short) from addressComponents."""
    municipio_nome = ""
    uf_short = ""
    for comp in address_components:
        types = list(comp.types)
        if "administrative_area_level_2" in types:
            municipio_nome = comp.long_text
        elif "administrative_area_level_1" in types:
            uf_short = comp.short_text  # "BA", "RJ", etc.
    return municipio_nome, uf_short
```

### D-02: in-process name→IBGE lookup

```python
# Build once (e.g. in a module-level cache or RealPlacesClient.__init__)
import unicodedata

def _normalize_name(name: str) -> str:
    nfd = unicodedata.normalize("NFD", name.lower().strip())
    return nfd.encode("ascii", "ignore").decode()

# Build lookup dict from ALL Mtur rows:
# {(normalized_municipio_name, "BA"): "2927408", ...}
def build_mtur_ibge_lookup(mtur_rows: list[dict]) -> dict[tuple[str, str], str]:
    lookup = {}
    for row in mtur_rows:
        name = row.get("name", "")
        uf = row.get("uf", "").upper()
        ibge = row.get("ibge_code", "")
        if name and uf and ibge:
            lookup[(_normalize_name(name), uf)] = ibge
    return lookup
```

### D-03: `produce_for_destino` targeted discovery

```python
# brave/lanes/atrativos/discovery_agent.py — new method on DiscoveryAgent

async def produce_for_destino(
    self,
    parent_mar: "MarRecord",
    target_count: int = 10,
) -> int:
    """Run targeted discovery for a single Mar destino municipality."""
    canonical = parent_mar.canonical or {}
    municipio_nome = canonical.get("municipio") or canonical.get("name", "")
    uf = canonical.get("uf", "")
    municipio_ibge = canonical.get("ibge_code", "")
    
    if not municipio_nome or not uf:
        logger.warning("produce_for_destino_missing_fields", parent_mar_id=str(parent_mar.id))
        return 0
    
    queries = [
        f"pontos turísticos em {municipio_nome} {uf}",
        f"o que fazer em {municipio_nome} {uf}",
    ]
    
    created = 0
    seen_place_ids: set[str] = set()
    
    for query in queries:
        if created >= target_count:
            break
        places_results = await self._places_client.text_search(query=query, uf=uf)
        for place in places_results:
            place_id = place.get("place_id", "")
            if not place_id or place_id in seen_place_ids:
                continue
            seen_place_ids.add(place_id)
            # Use known parent_mar directly — skip _resolve_parent_destino
            await self._ingest_place(
                place=place,
                uf=uf,
                municipio_ibge=municipio_ibge,
                municipio_nome=municipio_nome,
                parent_mar=parent_mar,
            )
            created += 1
    
    return created
```

### D-06: extracted `validate_and_promote_rio` helper

```python
# brave/core/dlq/service.py (new module) — or added to brave/core/mar/service.py

from sqlalchemy.orm.attributes import flag_modified
from brave.config.settings import ScoreConfig
from brave.core.models import MarRecord, RioRecord
from brave.core.rio.routing import reprocess_record
from brave.core.mar.service import promote_to_mar

def validate_and_promote_rio(
    session: Session,
    rio: RioRecord,
    config: ScoreConfig | None = None,
) -> MarRecord | None:
    """Set validacao_humana=100 → re-score → promote_to_mar if routing=='mar'.

    Extracted from dlq.py validate_dlq_record.
    Returns the MarRecord if promoted, None if routing != 'mar' after re-score.
    """
    config = config or ScoreConfig()
    normalized = dict(rio.normalized or {})
    normalized["validacao_humana_value"] = 100.0
    rio.normalized = normalized
    flag_modified(rio, "normalized")
    session.flush()

    reprocess_record(session, rio.id, config)
    session.refresh(rio)

    if rio.routing == "mar":
        return promote_to_mar(session, rio)
    return None
```

### D-05: harness skeleton

```python
# scripts/loadtest_destinos_atrativos.py (mirrors ingest_destinos.py)

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from brave.clients.mtur import MturClient
from brave.clients.places import RealPlacesClient
from brave.clients.llm import RealLLMClient
from brave.config.settings import AppConfig, LLMConfig, ScoreConfig
from brave.core.models import MarRecord, RioRecord
from brave.core.dlq.service import validate_and_promote_rio
from brave.lanes.destinos.mtur import MturSeedIngest
from brave.lanes.atrativos.discovery_agent import DiscoveryAgent

def main():
    cfg = AppConfig()
    # 1. Ingest destinos for chosen UF
    # 2. Promote 10 destinos DLQ→Mar via validate_and_promote_rio
    # 3. Run produce_for_destino for each of the 10
    # 4. Print summary: MarRecord count + RioRecord count per parent_mar_id
```

---

## Architecture Patterns

### System Architecture Diagram

```
Operator CLI
    │
    ▼
scripts/loadtest_destinos_atrativos.py
    │
    ├─[1] MturSeedIngest.produce(uf)
    │       └── MturClient._load_csv()  ──► data/mtur/municipios_mtur_2025.csv
    │       └── store_raw → process_nascente_record → RioRecord(routing=dlq)
    │
    ├─[2] validate_and_promote_rio(session, rio)          [extracted from dlq.py]
    │       └── normalized[validacao_humana]=100 + flag_modified
    │       └── reprocess_record → routing=mar
    │       └── promote_to_mar → MarRecord
    │
    └─[3] DiscoveryAgent.produce_for_destino(parent_mar)
            └── RealPlacesClient.text_search(query, uf)
            │       └── metadata=[("x-goog-fieldmask", MASK)]  ← D-01 FIX
            │       └── _extract_municipio_from_components()     ← D-02 FIX
            └── build_mtur_ibge_lookup() name→IBGE               ← D-02 FIX
            └── (skip _resolve_parent_destino — parent is KNOWN) ← D-03 FIX
            └── RealLLMClient.extract(AtrativoResult)
            └── store_raw(source="places_discovery", parent_mar_id=...)
            └── process_nascente_record → RioRecord(sub_state="discovered")
```

### Recommended Project Structure (additions only)

```
brave/
├── clients/
│   └── places.py           # D-01: add _TEXT_SEARCH_FIELD_MASK + _GET_PLACE_FIELD_MASK
│                           # D-02: add _extract_municipio_from_components + _build_ibge_lookup
├── core/
│   └── dlq/
│       └── service.py      # NEW: validate_and_promote_rio() (D-06)
├── lanes/
│   └── atrativos/
│       └── discovery_agent.py  # D-02: empty-ibge guard; D-03: produce_for_destino
data/
└── mtur/
    ├── municipios_mtur_2024.csv        # existing 16-row sample (keep)
    ├── municipios_mtur_2025.csv        # NEW: operator downloads from mapa.turismo.gov.br
    └── README                          # source + portaria + download date
scripts/
└── loadtest_destinos_atrativos.py      # NEW: D-05 harness
tests/
└── unit/
    └── clients/
        └── test_real_places_client.py  # NEW: D-08 field mask + addressComponents tests
```

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| `google-maps-places` 0.9.0 | `RealPlacesClient` | Already in `.venv` | 0.9.0 | — |
| PostgreSQL (live DB) | harness + tests | Operator-managed | 16/17 | docker-compose |
| Redis | Celery broker | Not needed for harness | — | not needed |
| `BRAVE_PLACES_API_KEY` | `RealPlacesClient` | Operator env | — | `FakePlacesClient` for tests |
| `BRAVE_LLM_OPENROUTER_API_KEY` | `RealLLMClient` | Operator env | — | `FakeLLMClient` for tests |
| `RUN_REAL_EXTERNALS=true` | All real clients | Operator flag | — | False = offline tests |

---

## Validation Architecture

`nyquist_validation` is explicitly `false` in `.planning/config.json` — section skipped.

---

## Security Domain

`security_enforcement` not explicitly set — treated as enabled. However this phase has no new auth surfaces, no new external data ingestion paths, and no user-facing endpoints. The relevant existing controls remain unchanged:

| ASVS Category | Applies | Control |
|---------------|---------|---------|
| V5 Input Validation | Yes — `addressComponents` parsing | Pydantic `AtrativoResult` (instructor Mode.Tools) validates LLM extraction output |
| V6 Cryptography | No new crypto | — |
| V2 Auth | No new auth surface | Bearer/steward guards unchanged |

The Places API field mask change is a correctness fix, not a security change. The harness script runs operator-local with env-vars; no new secret surface.

COMP-03 (`place_id` only persisted from Google) is already implemented and unchanged.

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Proto-plus `Timestamp` field `publish_time` may auto-convert to Python `datetime` on attribute access (proto-plus behavior depends on version) | RQ-1 / Common Pitfalls §5 | `review.publish_time.isoformat()` in existing code may raise `AttributeError` with some proto-plus versions |
| A2 | `administrative_area_level_2` consistently maps to the município name in Brazilian Google Places responses | RQ-2 | Wrong component type → empty `municipio_nome` → all atrativos quarantined `parent_destino_absent` |
| A3 | An exact name-match (accent-normalized) against the Mtur table will resolve IBGE for the 10 destinos in the harness run | RQ-2 | Name mismatch → no IBGE → quarantine; harness would show 0 atrativos |
| A4 | The XLSX downloaded from mapa.turismo.gov.br has columns mappable to `co_municipio,no_municipio,sg_uf,categoria,no_regiao_turistica` | RQ-5 | Column name mismatch → `_load_csv` returns empty rows; harness has no destinos to ingest |
| A5 | 2 queries × 20 results = up to 40 Places results per município is sufficient to get ≥10 distinct after dedup + quarantine | RQ-3 | Fewer distinct results → harness acceptance fails; add a 3rd query or raise `max_result_count` |

---

## Open Questions

1. **`publish_time` proto conversion**
   - What we know: `review.publish_time` is `google.protobuf.timestamp_pb2.Timestamp`. Proto-plus may wrap it as Python datetime automatically.
   - What's unclear: Whether `.isoformat()` works or requires `.ToDatetime(tzinfo=utc).isoformat()`.
   - Recommendation: Add a unit test with a mock `Timestamp(seconds=..., nanos=0)` and assert `.isoformat()` produces the expected string. If it fails, switch to `ToDatetime`.

2. **Mtur XLSX column names**
   - What we know: The 2025 portal provides an Excel export; older years used `co_municipio`, `no_municipio`, `sg_uf`, `categoria`, `no_regiao_turistica`.
   - What's unclear: Whether 2025 XLSX uses the same column names.
   - Recommendation: After downloading, inspect headers before conversion. The `_load_csv` function already handles both `co_municipio`/`codigo_ibge` and `sg_uf`/`uf` variants.

3. **`produce_for_destino` placement**
   - What we know: Can be a method on `DiscoveryAgent` or a thin wrapper elsewhere.
   - What's unclear: Whether the harness should iterate Mar destinos itself or call a higher-level `sweep_destinos_targeted(session, uf, target_count)` wrapper.
   - Recommendation: Method on `DiscoveryAgent` (consistent with `produce(uf)`); harness queries `MarRecord` directly and iterates.

---

## Sources

### Primary (HIGH confidence)
- `.venv/lib/python3.12/site-packages/google/maps/places_v1/services/places/async_client.py` — `search_text` and `get_place` exact signatures with `metadata` kwarg
- `.venv/lib/python3.12/site-packages/google/maps/places_v1/types/place.py` — `Place.AddressComponent` fields (`long_text`, `short_text`, `types`), `OpeningHours.weekday_descriptions`, `Place.business_status` enum, `Place.regular_opening_hours`, `Place.reviews`
- `.venv/lib/python3.12/site-packages/google/maps/places_v1/types/review.py` — `Review.publish_time` (Timestamp), `Review.text`, `Review.rating`
- `.venv/lib/python3.12/site-packages/google/maps/places_v1/services/places/transports/rest.py:376` — "every request requires a field mask set outside of the request proto via the HTTP header X-Goog-FieldMask"
- `brave/clients/places.py` — existing bugs identified (no metadata on search_text; wrong `places.` prefix on get_place mask)
- `brave/lanes/atrativos/discovery_agent.py` — `_resolve_parent_destino` `contains("")` bug confirmed
- `brave/api/routers/dlq.py` — `validate_dlq_record` 4-step pattern (flagged + reprocess + promote + audit)
- `brave/core/rio/routing.py` — `reprocess_record` signature and behavior
- `brave/clients/mtur.py`, `data/mtur/municipios_mtur_2024.csv` — existing schema and 16-row sample

### Secondary (MEDIUM confidence)
- `https://developers.google.com/maps/documentation/places/web-service/choose-fields` — confirmed `places.` prefix for search_text (array response) vs no prefix for get_place (single Place response)

### Tertiary (LOW confidence / ASSUMED)
- Mapa do Turismo Brasileiro 2025 XLSX column names (cannot verify without downloading)
- Proto-plus Timestamp auto-conversion behavior on `.isoformat()` (depends on proto-plus version)

---

## Metadata

**Confidence breakdown:**
- Field mask mechanics (D-01): HIGH — read from installed SDK source
- AddressComponent field names (D-02): HIGH — read from proto type definitions
- DLQ/promote logic (D-06): HIGH — read from existing dlq.py and routing.py
- Mtur dataset download path (D-04): MEDIUM — WebSearch confirmed portal exists, XLSX download is manual; no direct URL
- Targeted discovery design (D-03): MEDIUM — design based on code reading; not yet executed

**Research date:** 2026-06-17
**Valid until:** 2026-09-17 (90 days; SDK source is pinned to installed version; Mtur portal structure may shift)
