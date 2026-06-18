# Phase 7: Real Places Hardening + Targeted Atrativos Discovery + Mtur Refresh - Context

**Gathered:** 2026-06-17
**Status:** Ready for planning

<domain>
## Phase Boundary

Make the **real Atrativos collection path actually work end-to-end** so a load test can register **10 destinos, each with ≥10 atrativos** from live data. Phase 6 made the LLM real; this phase fixes the Google Places real path + the destino→atrativo linking + refreshes the Mtur seed, all surfaced by real dogfooding:

1. **`RealPlacesClient` is broken against the live API** — a real `text_search` call returns `400 INVALID_ARGUMENT: FieldMask is a required parameter`. Places API (New) requires an `X-Goog-FieldMask` on every `search_text`/`get_place` call; the client sends none. Tests only used `FakePlacesClient`, so this never surfaced. (`google-maps-places` 0.9.0 was also missing from the venv/pyproject — already installed + declared as part of this work.)
2. **`text_search` returns no `municipio_ibge`/`municipio_nome`**, so `DiscoveryAgent._resolve_parent_destino(..., municipio_ibge="")` runs `source_ref.contains("")` → matches ANY Mar destino → every atrativo links to one arbitrary parent. The "per destino" structure the goal needs does not exist.
3. **`DiscoveryAgent.produce(uf)` is a UF-wide sweep** (2 queries × 20 = ~40 places for the whole state, capital-biased) — it cannot yield ≥10 atrativos for each of 10 distinct destinos.
4. **Mtur seed is a 16-row hand-curated sample**, not the current Mapa do Turismo Brasileiro.

**This phase delivers:** field-mask-correct `RealPlacesClient` (text_search + place_details) that also maps `addressComponents`→município; correct parent linking; a **targeted per-município discovery** path that produces ≥10 atrativos per Mar destino; a **refreshed Mtur dataset** (current official categorization); a **load-test harness** that runs the full real flow (ingest destinos → steward→Mar → targeted atrativos discovery) and reports the 10×10 result. After this, the operator runs the harness with real keys and sees the records in the dashboard/DB.

**Out of scope:** automatic WhatsApp send (stays human-gated), norteia-api push, Apify signal, new score-engine logic, dashboard changes.
</domain>

<decisions>
## Implementation Decisions

### RealPlacesClient field mask (the live-API blocker)
- **D-01:** Add `X-Goog-FieldMask` to every Places API (New) call via the gRPC `metadata=[("x-goog-fieldmask", "<mask>")]` kwarg on `client.search_text(...)` and `client.get_place(...)`. `text_search` mask MUST include at least: `places.id, places.displayName, places.formattedAddress, places.types, places.location, places.addressComponents`. `place_details` mask MUST include the SignalAgent fields: `id, displayName, formattedAddress, types, location, addressComponents, businessStatus, regularOpeningHours, reviews`. Verify the mask is passed via a unit test that mocks the async client and asserts the metadata. (Researcher: confirm the exact `metadata` kwarg shape for `google-maps-places` 0.9.0 async client; confirm `regularOpeningHours.weekdayDescriptions` is the weekday_text field and `reviews.publishTime` is available.)

### município resolution (correct parent linking)
- **D-02:** Populate `municipio_nome` + `municipio_ibge` on each `text_search` result by reading `addressComponents` (administrative_area_level_2 = município name; the API does NOT return IBGE codes). Resolve name→IBGE by matching `(municipio_name, uf)` against the loaded Mtur municipalities (the same dataset that produced the parent destinos) — a small in-process lookup, no extra API. Harden `_resolve_parent_destino` so an empty/unknown `municipio_ibge` does NOT fall through to `contains("")`/arbitrary-parent — when the município can't be resolved to a Mar destino, quarantine `parent_destino_absent` (correct behavior), never silently mislink.

### targeted per-município discovery (the 10×10 shape)
- **D-03:** Add a targeted discovery path so atrativos are produced **per Mar destino município**: for a given UF, iterate the active Mar destination records, and for each run a targeted query (`"pontos turísticos em {municipio_nome} {uf}"`, plus a second query like `"o que fazer em {municipio_nome} {uf}"` if needed to reach ≥10) → extract → `store_raw` linked to that known parent. Keep the existing `produce(uf)` UF-sweep but route it through the same fixed extraction; the targeted method is what guarantees correct linking + per-destino volume. Recommended surface: `DiscoveryAgent.produce_for_destino(parent_mar, target_count=10)` (the harness/sweep iterates Mar destinos and calls it).

### Mtur dataset refresh
- **D-04:** Replace the 16-row sample with the **current official Mapa do Turismo Brasileiro** categorization (Ministério do Turismo dados abertos — latest portaria). Write `data/mtur/municipios_mtur_<year>.csv` in the existing schema (`co_municipio,no_municipio,sg_uf,categoria,no_regiao_turistica`); the loader already globs newest-by-filename. (Researcher: find the canonical download URL for the latest categorization with IBGE codes + categories A–E; if only XLSX is published, convert to the CSV schema. Keep it reproducible — note the source + date in a small data/mtur/README or header comment. Do NOT delete the sample if the real file can't be obtained; flag and fall back.)

### Load-test harness + acceptance bar
- **D-05:** `scripts/loadtest_destinos_atrativos.py` drives the real flow against the live DB: (1) ingest destinos for the chosen UF(s) via the existing Mtur seed; (2) promote 10 destinos DLQ→Mar (D-06); (3) run targeted atrativos discovery for those 10 → ≥10 atrativos each; (4) print a summary: destinos-in-Mar count + atrativos-in-rio count grouped by `parent_mar_id`. **Acceptance:** 10 `mar_records` (entity_type=destination) and ≥10 `rio_records` (entity_type=attraction) per parent, each linked via `parent_mar_id`. DB reset is operator-run (destructive) — the harness assumes a clean or additive DB and reports absolute counts for the run.

### steward→Mar promotion (test path)
- **D-06:** Provide a programmatic destino promotion the harness can call: set `validacao_humana=100` on the DLQ rio record → re-score → `promote_to_mar`. Reuse the existing steward-validate logic (the DLQ validate endpoint / its underlying service function) rather than re-implementing scoring. (Researcher/planner: locate the existing steward validate function behind the DLQ validate endpoint and reuse it.)

### cost + safety
- **D-07:** Real run cost ≈ 10 text_search + ~100 place-extractions (LLM) + ~100 place_details. Default cost guard `BRAVE_LLM_USD_DAILY_BUDGET=10.0` should cover DeepSeek extraction; flag the operator to raise it if `CostGuardError` trips. `provider.data_collection=deny` stays. No WhatsApp send. Persist only `place_id` from Google (COMP-03/D-04 already enforced in store_raw).

### offline test mandate
- **D-08:** Suite stays 100% offline by default. New/updated tests mock the `google.maps.places_v1` async client: assert the field-mask metadata is sent (D-01), assert `addressComponents`→município mapping (D-02), assert targeted discovery links to the correct parent and quarantines on unresolved município (D-02/D-03). Keep CI keyless; real verification via an opt-in (`RUN_REAL_EXTERNALS=true` + keys) smoke/harness run, skipif-gated.

### Claude's Discretion
- Exact field-mask string contents (beyond the required fields), the município-name normalization (accents/casing) for name→IBGE matching, whether targeted discovery lives on DiscoveryAgent vs a thin sweep wrapper, harness CLI args (UF list, per-destino target count), and commit granularity.
</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### The broken real path
- `brave/clients/places.py` — `RealPlacesClient.text_search` / `place_details`: the missing `X-Goog-FieldMask` (D-01) and the missing `municipio_*` mapping (D-02). `_get_client()` uses `google.maps.places_v1`.
- `brave/lanes/atrativos/discovery_agent.py` — `_resolve_parent_destino` (the `contains("")` mislink bug) and `DiscoveryAgent.produce` (UF-sweep; reads `place.get("municipio_ibge")` which is never populated). Targeted-discovery surface (D-03) lands here.
- `brave/lanes/atrativos/signal_agent.py` — the fields `place_details` must return (business_status, weekday hours, reviews[].publishTime) so the field mask is complete.

### Destinos seed + promotion (test scaffolding)
- `brave/clients/mtur.py` — `MturClient._load_csv` (globs `data/mtur/municipios_mtur_*.csv`, newest wins) + `_map_categoria` (A/B/E + new nomenclature). The refreshed dataset must match this schema.
- `brave/lanes/destinos/mtur.py` — `MturSeedIngest.produce(uf)` (destinos → Rio → DLQ).
- `scripts/ingest_destinos.py` — the existing real destinos driver to mirror for the harness.
- `brave/core/mar/service.py` — `promote_to_mar(session, rio)`.
- The DLQ validate endpoint + its service function (steward validacao_humana=100 → re-score → Mar) — reuse for D-06. Find in `brave/api/routers/` (dlq) / the dashboard router.

### Config / scoring / models
- `brave/config/settings.py` — `LLMConfig.usd_daily_budget` (cost guard), `AppConfig.run_real_externals`.
- `brave/core/models.py` — `RioRecord`, `MarRecord` (entity_type, source_ref `mtur:{UF}:{ibge}` / `desm:...`, `parent_mar_id` linkage), `NascenteRecord`.
- `brave/clients/base.py` — `PlacesClientProtocol` (the contract text_search/place_details must keep satisfying).

### External (researcher)
- Google Places API (New) field-mask docs (`X-Goog-FieldMask` system parameter) + `google-maps-places` 0.9.0 async metadata kwarg.
- Ministério do Turismo — Mapa do Turismo Brasileiro categorized municipalities (latest portaria, dados abertos) download.
</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `promote_to_mar` + the steward DLQ-validate service — reuse for D-06; do not re-implement scoring.
- `MturSeedIngest` + `scripts/ingest_destinos.py` — the destinos half already works real; harness mirrors it.
- `store_raw(source="places_discovery", entity_type="attraction", ...)` already persists only `place_id` (COMP-03) and links `parent_mar_id` — keep.
- `FakePlacesClient` — extend its fixtures so the new field-mask/municipio behavior is offline-testable.

### Established Patterns
- Real clients fail-closed on `run_real_externals=False`; tenacity retry on 429/5xx — preserve.
- `_resolve_parent_destino` matches Mar by `source_ref.contains(ibge)` with `mtur:{UF}:{ibge}` format — the IBGE must be real for this to work (D-02).

### Integration Points
- `brave/tasks/pipeline.py` `discover_atrativo_task` constructs `RealPlacesClient(api_key=...)` + `RealLLMClient(...)` and runs `DiscoveryAgent` — the targeted path must be reachable from here and from the harness.
</code_context>

<specifics>
## Specific Ideas

- Goal/demo acceptance: operator runs `scripts/loadtest_destinos_atrativos.py` with real keys → DB shows 10 destinos in Mar and ≥10 atrativos per destino in Rio (linked by `parent_mar_id`), visible in the dashboard. This is the human-UAT for the phase.
- Pick a UF with enough Oferta-Principal destinos in the refreshed Mtur data (e.g. BA) so 10 real municípios are available.
- Reset is operator-run and destructive — do NOT auto-truncate in the harness.
</specifics>

<deferred>
## Deferred Ideas

- Apify IG/X signal enrichment for atrativos.
- norteia-api push of the validated atrativos.
- Automatic WhatsApp owner-validation outreach (stays human-gated).
- Full national Mtur fan-out scheduling (beat already exists; not this phase).
- A name→IBGE service beyond the Mtur-table lookup (e.g. full IBGE municipality table) if Places municípios fall outside the seeded set.
</deferred>

---

*Phase: 7-Real Places Hardening + Targeted Atrativos Discovery + Mtur Refresh*
*Context gathered: 2026-06-17*
