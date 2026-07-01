---
phase: quick-260701-has
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - data/tripadvisor/uf_geoids.json
  - brave/lanes/tripadvisor/client.py
  - brave/config/settings.py
  - tests/unit/lanes/tripadvisor/test_client.py
  - scripts/ta_discover_state_geoids.py
autonomous: true
requirements: [TA-ATR-FIX]
must_haves:
  truths:
    - "All 27 UF keys in uf_geoids.json map to the live-validated STATE geoId (not a city geoId)."
    - "A transient AttractionsFusion failure (HTTP 200, status.success==false, totalResults==0) is retried a bounded number of times instead of silently dropping the whole UF."
    - "A genuinely-empty geo (status.success==true, sections==[]) still returns [] WITHOUT burning all retries (single HTTP call)."
    - "Existing 162 TA client unit tests stay green; the offline mandate holds (no test hits the network)."
    - "The discovery-script docstring records the live reality (DataDome rate-limit on TypeAhead; geoId lives in the result url field; GraphQL is the durable path)."
  artifacts:
    - path: "data/tripadvisor/uf_geoids.json"
      provides: "27 correct STATE geoIds keyed by UF"
      contains: "\"PR\": 303435"
    - path: "brave/lanes/tripadvisor/client.py"
      provides: "fetch_attractions with bounded transient-retry"
      contains: "status"
    - path: "brave/config/settings.py"
      provides: "configurable retry count + sleep on TripAdvisorConfig"
    - path: "tests/unit/lanes/tripadvisor/test_client.py"
      provides: "transient-retry + real-empty unit tests"
  key_links:
    - from: "brave/lanes/tripadvisor/atrativos.py produce()"
      to: "client.fetch_attractions(geo_id)"
      via: "unchanged call path — retry is internal to fetch_attractions"
      pattern: "fetch_attractions"
    - from: "brave/lanes/tripadvisor/client.py"
      to: "self._config.attractions_transient_max_retries"
      via: "config-threaded retry knobs"
      pattern: "attractions_transient"
---

<objective>
Fix the TripAdvisor per-UF atrativos lane via three independent, live-POC-backed changes:
1. Replace all 27 wrong geoIds in `data/tripadvisor/uf_geoids.json` with the validated STATE geoIds.
2. Add a bounded transient-retry to `fetch_attractions` so an intermittent AttractionsFusion soft-failure no longer silently drops an entire UF.
3. Update the discovery-script docstring to record live reality.

Purpose: A single wrong geoId or a single silent transient currently drops an entire state's attractions from ingest. This makes the per-UF sweep correct and resilient.
Output: Corrected geoid map, resilient `fetch_attractions`, two new offline unit tests, accurate ops-script docs.
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/STATE.md
@./CLAUDE.md

<ground_truth>
A live POC this session established the following — treat as ground truth, do NOT re-investigate live:

- All 27 current geoIds are wrong (0/27 matched). The validated correct STATE geoIds:
  AC 303199, AL 303208, AM 303226, AP 303221, BA 303251, CE 303284, DF 303321,
  ES 303308, GO 303323, MA 303325, MG 303370, MS 303368, MT 303346, PA 303402,
  PB 303422, PE 303459, PI 303462, PR 303435, RJ 303488, RN 303510, RO 303555,
  RR 303562, RS 303530, SC 303570, SE 303637, SP 303598, TO 303645.
- AttractionsFusion (qid a5cb7fa004b5e4b5) DOES geo-scope correctly by geoId — do NOT change the transport/query. GraphQL is page-1-only; multi-page stays on the HTML `-oa{offset}-` path (untouched).
- Transient: AttractionsFusion intermittently returns HTTP 200 with
  `data.Result[0].status.success == false`, a `status.message`, `totalResults == 0`,
  `sections == []` for a VALID geoId. Retrying the identical request succeeds.
- Real-empty geo is distinguished by `status.success == true` (or status absent) with `sections == []`.
</ground_truth>

<interfaces>
From brave/lanes/tripadvisor/client.py (fetch_attractions, ~line 428):
- `async def fetch_attractions(self, geo_id: int, max_pages: int | None = None) -> list[dict[str, Any]]`
- `if max_pages is not None and max_pages > 1: raise NotImplementedError(...)`  — KEEP as-is.
- Response envelope: `data[0]["data"]["Result"][0]["sections"]` (safe-extracted in a try/except).
- Current behavior (client.py:554-555): `if not sections: return []` — this is the bug (drops the UF on a transient).
- `self._config` is a `TripAdvisorConfig`; existing knob `self._config.page_throttle_seconds` (float). Existing sleep idiom in the file: `await asyncio.sleep(throttle)` (line 802). `asyncio` already imported.

From brave/config/settings.py (class TripAdvisorConfig(BaseSettings)):
- Env prefix `BRAVE_TA_`, NO Field aliases (CR-02) — each field resolves from its exact prefixed name.
- Existing pattern: `page_throttle_seconds: float = Field(default=2.0, description=(...))`.

From tests/unit/lanes/tripadvisor/test_client.py:
- `_make_ta_response(sections: list) -> list` returns `[{"data": {"Result": [{"sections": sections}]}}]` (NO status key).
  Existing `test_fetch_attractions_empty_sections_stops_pagination` relies on the no-status envelope returning [] in exactly 1 call → the retry logic MUST treat "status absent" as real-empty, NOT transient.
- `_make_session_redis(redis)` seeds a valid session. respx mocks `https://www.tripadvisor.com/data/graphql/ids`.
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: Rewrite uf_geoids.json with the 27 validated STATE geoIds</name>
  <files>data/tripadvisor/uf_geoids.json</files>
  <action>Overwrite the file with a flat JSON object mapping each UF to its validated STATE geoId from the ground_truth block: AC 303199, AL 303208, AM 303226, AP 303221, BA 303251, CE 303284, DF 303321, ES 303308, GO 303323, MA 303325, MG 303370, MS 303368, MT 303346, PA 303402, PB 303422, PE 303459, PI 303462, PR 303435, RJ 303488, RN 303510, RO 303555, RR 303562, RS 303530, SC 303570, SE 303637, SP 303598, TO 303645. Keep the existing file format exactly: keys sorted alphabetically by UF, integer values (not strings), 2-space indent, single trailing newline. Do NOT add comments or extra keys.</action>
  <verify>
    <automated>python -c "import json; d=json.load(open('data/tripadvisor/uf_geoids.json')); assert len(d)==27; assert d['PR']==303435 and d['AC']==303199 and d['SP']==303598 and d['TO']==303645; assert all(isinstance(v,int) for v in d.values()); assert list(d)==sorted(d); print('OK')"</automated>
  </verify>
  <done>File has 27 UF keys, all integer values matching ground_truth, sorted, valid JSON.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: Add bounded transient-retry to fetch_attractions (+ config knobs + unit tests)</name>
  <files>brave/config/settings.py, brave/lanes/tripadvisor/client.py, tests/unit/lanes/tripadvisor/test_client.py</files>
  <behavior>
    - Transient retried: mock the graphql endpoint to return on the FIRST call an envelope with `Result[0].status.success == false`, `status.message` set, `totalResults == 0`, `sections == []`; on the SECOND call a normal envelope with one valid FlexCard section. Assert fetch_attractions returns that 1 card (UF not dropped) and made exactly 2 HTTP calls.
    - Real-empty NOT over-retried: mock a single envelope with `Result[0].status.success == true` and `sections == []`. Assert result == [] and exactly 1 HTTP call (retries not burned).
    - Status-absent stays real-empty: the existing `test_fetch_attractions_empty_sections_stops_pagination` (envelope with NO status key) must still return [] in exactly 1 call — do not modify that test's expectations.
    - Retries bounded: if EVERY call is transient (success false), fetch_attractions eventually returns [] after exactly max_retries+1 HTTP calls (add this assertion in a third test using a config with a small max_retries and sleep 0).
  </behavior>
  <action>Add two fields to TripAdvisorConfig in brave/config/settings.py following the existing `page_throttle_seconds` Field pattern (env prefix BRAVE_TA_, NO alias): `attractions_transient_max_retries: int = Field(default=3, ...)` (BRAVE_TA_ATTRACTIONS_TRANSIENT_MAX_RETRIES) and `attractions_transient_retry_sleep_seconds: float = Field(default=1.0, ...)` (BRAVE_TA_ATTRACTIONS_TRANSIENT_RETRY_SLEEP_SECONDS); document both in the class docstring env-list. In client.py fetch_attractions, keep the `max_pages>1 → NotImplementedError` guard and the payload/qid/transport unchanged. Wrap the single POST + parse in a bounded loop: build the payload once (pageview_uid may be regenerated per attempt), then for attempt in range(max_retries+1): perform the httpx POST (keep the 403/429 SessionExpiredError raise and raise_for_status and cookie write-back inside the loop), parse `result0 = data[0]["data"]["Result"][0]` via safe try/except. Determine transient: `status = result0.get("status")`; treat as transient ONLY when `isinstance(status, dict) and status.get("success") is False`. If transient and attempt < max_retries: `await asyncio.sleep(self._config.attractions_transient_retry_sleep_seconds)` and continue. Otherwise extract `sections = result0.get("sections") or []`; if sections → return self._parse_attractions_page(sections); else return [] (real-empty: status absent or success true, OR exhausted retries). CRITICAL: status absent (KeyError/None) is NOT transient — it must fall straight through to real-empty so the existing empty-sections test stays green. In the new tests, build config via AppConfig().tripadvisor and set `attractions_transient_retry_sleep_seconds = 0` (and a small `attractions_transient_max_retries`) so the suite stays fast; extend the local `_make_ta_response` helper or add a sibling helper that injects `status` and `totalResults` into `Result[0]`. No test may hit the network (respx only).</action>
  <verify>
    <automated>BRAVE_USE_FAKEREDIS=1 env -u RUN_REAL_EXTERNALS .venv/bin/python -m pytest tests/unit/lanes/tripadvisor/test_client.py -q</automated>
  </verify>
  <done>New transient-retry, real-empty, and exhausted-retry tests pass; all pre-existing TA client tests stay green; config exposes the two BRAVE_TA_ retry knobs; transport/qid/payload and the max_pages>1 contract unchanged.</done>
</task>

<task type="auto">
  <name>Task 3: Update ta_discover_state_geoids.py docstring to record live reality</name>
  <files>scripts/ta_discover_state_geoids.py</files>
  <action>Update ONLY the module docstring (no logic changes — it is a manual/ops tool). Correct the false claims: TypeAhead is NOT "no auth required" — TypeAheadJson is DataDome rate-limited (soft-blocks after ~5-6 rapid hits, returning a `{"url": "...captcha-delivery..."}` 403); it works with the operator cookie jar but only slowly. The state geoId is NOT in a `locationId`/`geoId` field — it is embedded in the result's `url` field (e.g. `-g303435-`). Record that the durable discovery/validation path is the GraphQL endpoint (canonicalize qid a26bffd43d0e25b6 + fetch_attraction_geo qid d3d4987463b78a39), NOT typeahead. Replace/augment the "Discovery method (typeahead, no session required)" paragraph accordingly. Do not touch the RUN_REAL_EXTERNALS guard or any executable code.</action>
  <verify>
    <automated>python -c "import ast; t=ast.parse(open('scripts/ta_discover_state_geoids.py').read()); d=ast.get_docstring(t); assert 'DataDome' in d and 'url' in d and 'a26bffd43d0e25b6' in d and 'no auth required' not in d, d; print('OK')" && python -m py_compile scripts/ta_discover_state_geoids.py</automated>
  </verify>
  <done>Docstring reflects DataDome rate-limit, geoId-in-url, and GraphQL-as-durable-path; no logic changed; file still compiles.</done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| collector → TripAdvisor GraphQL | Untrusted upstream JSON; transient/soft-failure responses cross here |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-has-01 | Denial of Service | fetch_attractions retry loop | mitigate | Retries are bounded by `attractions_transient_max_retries` with a sleep between attempts; a persistently-failing geo returns [] after max_retries+1 calls, no unbounded loop. |
| T-has-02 | Information Disclosure | TASID/session cookies | accept | No new logging of session_id/cookies added; existing "never log TASID" invariant preserved (task changes control flow only). |
| T-has-03 | Tampering | uf_geoids.json | mitigate | Values are hard-coded from a live-validated set; Task 1 verify asserts the count, sort order, integer typing, and spot-check geoIds. |
</threat_model>

<verification>
Run the full offline unit suite to confirm no regression across the 162 TA tests and the rest of the suite:
`BRAVE_USE_FAKEREDIS=1 env -u RUN_REAL_EXTERNALS .venv/bin/python -m pytest tests/unit -q`
No test may hit the network.
</verification>

<success_criteria>
- uf_geoids.json holds the 27 validated STATE geoIds (correct format).
- fetch_attractions retries the AttractionsFusion transient (bounded) and no longer drops a UF; real-empty geos still return [] in one call.
- Two/three new offline unit tests pass; all pre-existing TA tests stay green.
- Discovery-script docstring records the live reality; no logic changed.
- atrativos.py, pipeline.py, and the HTML `-oa{offset}-` pagination path are untouched.
</success_criteria>

<output>
Create `.planning/quick/260701-has-fix-ta-per-uf-atrativos-correct-27-state/260701-has-SUMMARY.md` when done.
</output>
