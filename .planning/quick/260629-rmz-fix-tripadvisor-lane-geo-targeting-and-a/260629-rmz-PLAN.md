---
phase: quick-260629-rmz
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - data/tripadvisor/uf_geoids.json
  - brave/lanes/tripadvisor/client.py
  - brave/clients/base.py
  - brave/clients/null_tripadvisor.py
  - tests/fakes/fake_tripadvisor.py
  - brave/lanes/tripadvisor/atrativos.py
  - scripts/ta_discover_state_geoids.py
  - tests/unit/lanes/tripadvisor/test_geo.py
  - tests/unit/lanes/tripadvisor/test_client.py
  - tests/unit/lanes/tripadvisor/test_atrativos.py
autonomous: true
requirements:
  - TA-GEO-01
  - TA-DEST-02
  - TA-LINKAGE-03
  - TA-PARSER-04

must_haves:
  truths:
    - "fetch_attractions(geo_id) for a correctly-resolved UF returns that state's attractions, not nationally-scoped or wrong-UF results"
    - "fetch_destinations raises ValueError (not a silent empty list) when no QID is configured; when BRAVE_TA_QUERY_ID_OVERRIDE['destinations'] is set, fetch_destinations uses it and does NOT fall back to the broken session lookup"
    - "A TA attraction without lat/lng whose name does not fuzzy-match IBGE resolves its parent city via the detail parents endpoint and is NOT quarantined as ibge_unmatched"
    - "Attraction cards with null bubbleRating, cardTitle, or primaryInfo are parsed without AttributeError and are included with zero/empty values"
  artifacts:
    - path: "data/tripadvisor/uf_geoids.json"
      provides: "27 correct state-level TA geoIds, one per UF"
      contains: "27 keys: AC AL AM AP BA CE DF ES GO MA MG MS MT PA PB PE PI PR RJ RN RO RR RS SC SE SP TO"
    - path: "brave/lanes/tripadvisor/client.py"
      provides: "_DESTINATIONS_QID constant, fixed fetch_destinations resolution, fetch_attraction_detail method, null-safe parser"
      exports: ["_DESTINATIONS_QID", "fetch_attraction_detail"]
    - path: "brave/clients/base.py"
      provides: "fetch_attraction_detail added to TripAdvisorClientProtocol"
      contains: "fetch_attraction_detail"
    - path: "brave/clients/null_tripadvisor.py"
      provides: "fetch_attraction_detail null stub returning None"
    - path: "tests/fakes/fake_tripadvisor.py"
      provides: "fetch_attraction_detail fake returning fixture or None"
    - path: "scripts/ta_discover_state_geoids.py"
      provides: "Discovery + redirect-validation script for 27 UF geoIds (RUN_REAL_EXTERNALS gated)"
    - path: "brave/lanes/tripadvisor/atrativos.py"
      provides: "_ingest_one calls fetch_attraction_detail as tertiary ibge fallback"
  key_links:
    - from: "brave/lanes/tripadvisor/geo.py"
      to: "data/tripadvisor/uf_geoids.json"
      via: "load_uf_geoids(GEO_SEED_PATH)"
      pattern: "load_uf_geoids"
    - from: "brave/lanes/tripadvisor/client.py:fetch_destinations"
      to: "_DESTINATIONS_QID / config.query_id_override"
      via: "resolution chain in query_id local var"
      pattern: "_DESTINATIONS_QID"
    - from: "brave/lanes/tripadvisor/atrativos.py:_ingest_one"
      to: "client.fetch_attraction_detail"
      via: "tertiary ibge fallback after name-match and geocoder miss"
      pattern: "fetch_attraction_detail"
---

<objective>
Fix four confirmed bugs in the TripAdvisor lane that cause the per-UF sweep to
produce wrong-UF attractions, zero destinations, ibge_unmatched quarantines
for every attraction, and dropped cards on null fields.

Root causes (from SPIKE.md):
1. uf_geoids.json holds arbitrary sequential city geoIds (30350x-30353x). Both
   transports scope correctly by geoId — the table is simply wrong.
2. fetch_destinations uses session["query_ids"]["destinations"] which the cURL
   parser never writes (it stores query_0..query_N positionally) → query_id=""
   → 0 locations every call.
3. _parse_attractions_page uses .get(k,{}).get(...) which AttributeErrors on
   present-but-null fields (review-less cards).
4. Attraction cards carry no lat/lng and no municipality name; resolve_municipio
   gets only the attraction name, which never matches an IBGE municipality →
   100% ibge_unmatched. The TA detail query (444040f131735091) returns parents[0]
   = parent city with localizedName — use that as the authoritative city name.

Purpose: After this fix, a per-UF sweep with a real session produces correctly
geo-scoped attractions, non-empty destination lists, and atrativo→município
linkage via the TA detail parents hierarchy.

Output:
- Corrected uf_geoids.json (27 real state-level geoIds, discovered+validated
  with a real session via scripts/ta_discover_state_geoids.py)
- Fixed fetch_destinations (pinned _DESTINATIONS_QID, proper override chain)
- Null-safe parser
- fetch_attraction_detail client method + protocol + null/fake stubs
- atrativos._ingest_one detail-parents ibge fallback
- Offline unit tests for all four fixes
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/ROADMAP.md
@.planning/STATE.md
@.planning/quick/260629-rmz-fix-tripadvisor-lane-geo-targeting-and-a/260629-rmz-SPIKE.md
@.planning/quick/260629-rmz-fix-tripadvisor-lane-geo-targeting-and-a/260629-rmz-CONTEXT.md

Test rules: offline by default (RUN_REAL_EXTERNALS unset). Run via:
  .venv/bin/python -m pytest tests/unit/lanes/tripadvisor/ -x -q

Real TA opt-in: RUN_REAL_EXTERNALS=1 (never in CI).
</context>

<interfaces>
<!-- Key types and contracts the executor needs. -->

From brave/lanes/tripadvisor/client.py (key existing constants):
  _LISTING_QID = "a5cb7fa004b5e4b5"  # hardcoded attractions qid (security: T-13-01-02)
  _TA_GRAPHQL_URL = "https://www.tripadvisor.com/data/graphql/ids"

From brave/lanes/tripadvisor/client.py fetch_destinations (current broken line, ~:336):
  query_id = session.get("query_ids", {}).get("destinations", "")  # BUG: always ""

From brave/lanes/tripadvisor/client.py _parse_attractions_page (current null-unsafe lines, ~:172-181):
  name = card.get("cardTitle", {}).get("text", "")       # AttributeError when cardTitle=null
  rating_raw = card.get("bubbleRating", {}).get("rating")   # AttributeError when bubbleRating=null
  review_count_raw = card.get("bubbleRating", {}).get("reviewCount")
  category = card.get("primaryInfo", {}).get("text", "")  # AttributeError when primaryInfo=null

From brave/config/settings.py TripAdvisorConfig (already has override mechanism):
  query_id_override: dict[str, str]  # env BRAVE_TA_QUERY_ID_OVERRIDE
  page_throttle_seconds: float       # reuse for detail call throttle
  ibge_match_threshold: int          # default 88

From brave/lanes/tripadvisor/atrativos.py TripAdvisorAtrativosIngest (current __init__):
  def __init__(self, config: ScoreConfig, client: TripAdvisorClientProtocol, ...)
  # ScoreConfig has NO page_throttle_seconds or ibge_match_threshold
  # Those live on TripAdvisorConfig — accessed via new optional ta_config param

From brave/lanes/tripadvisor/ibge.py:
  def resolve_municipio(name, uf, records, *, threshold=88, max_distance_km=15.0,
                        candidate_lat=None, candidate_lng=None) -> IbgeMunicipio | None

From brave/clients/base.py TripAdvisorClientProtocol (current methods):
  fetch_destinations(uf) -> list[dict]
  fetch_attractions(geo_id, max_pages=None) -> list[dict]
  fetch_attractions_paginated(geo_id, start_page=1, max_pages=334) -> AsyncIterator
  resolve_geo_id(uf) -> int

From live SPIKE capture (444040f131735091 detail response shape):
  data[0]["data"]["locations"][0]["parents"]  # list of geo hierarchy dicts
  parents[0] = {"locationId": <city_geo_id>, "localizedName": "Foz do Iguaçu"}
  parents[1] = {"locationId": <state_geo_id>, "localizedName": "Paraná"}
  (e.g. attraction locationId 312332 → parents[0] = {303444, "Foz do Iguaçu"})
</interfaces>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: Correct uf_geoids.json + fix destinos QID resolution</name>
  <files>
    data/tripadvisor/uf_geoids.json,
    brave/lanes/tripadvisor/client.py,
    scripts/ta_discover_state_geoids.py,
    tests/unit/lanes/tripadvisor/test_geo.py,
    tests/unit/lanes/tripadvisor/test_client.py
  </files>
  <behavior>
    - test_uf_geoids_has_27_keys: load uf_geoids.json; assert exactly 27 keys equal
      {AC,AL,AM,AP,BA,CE,DF,ES,GO,MA,MG,MS,MT,PA,PB,PE,PI,PR,RJ,RN,RO,RR,RS,SC,SE,SP,TO}
    - test_uf_geoids_all_positive_ints: all values are positive ints > 0
    - test_uf_geoids_no_sequential_range: no value falls in 303509-303534 range
      (the previous wrong sequential city geoIds)
    - test_fetch_destinations_uses_config_override_qid: build a TripAdvisorClient with
      config.query_id_override={"destinations":"test_qid_x"}, inject a fakeredis
      session with query_ids={query_0:"some_other_id"} (no "destinations" key),
      mock the POST via respx; assert the request body contains
      "preRegisteredQueryId":"test_qid_x" (not "some_other_id" or "")
    - test_fetch_destinations_raises_when_no_qid: when config override is empty AND
      session has no "destinations" key AND _DESTINATIONS_QID is None, calling
      fetch_destinations should raise ValueError("No destinations queryId configured")
      rather than silently making a broken request
  </behavior>
  <action>
    Step A — Discover and write the correct uf_geoids.json.

    First, write scripts/ta_discover_state_geoids.py — a RUN_REAL_EXTERNALS gated
    script that discovers and validates the 27 Brazilian state geoIds:

    The script MUST guard on os.environ.get("RUN_REAL_EXTERNALS") at module entry
    and exit with a clear message if not set. It is never called by pytest offline.

    Discovery method: use the existing Redis session (reads BRAVE_REDIS_URL, calls
    brave.lanes.tripadvisor.client.BRAVE_TA_SESSION_KEY) to authenticate, then for
    each Brazilian state name (PT-BR canonical, e.g. "Acre", "Amazonas") POST to
    https://www.tripadvisor.com/data/graphql/ids with a typeahead/geo-search query.
    The simplest discovery path: GET the TA search page
    https://www.tripadvisor.com/Search?q=Estado+de+{state_name}+Brasil with the
    session cookies, extract geoId from the first result whose name contains the
    state name.

    Validation step (for each discovered geoId): GET
    https://www.tripadvisor.com/Attractions-g{geo_id}-Activities-a_allAttractions.true-oa0-Brazil.html
    with follow_redirects=True; assert the final URL or page title contains
    the expected state name or PT-BR equivalent. Log the validation result per UF.

    Output: script prints a corrected JSON blob that can be pasted into
    data/tripadvisor/uf_geoids.json, plus per-UF validation status.

    Second, update data/tripadvisor/uf_geoids.json by running the discovery script
    with a real session (RUN_REAL_EXTERNALS=1). The 27 correct state-level geoIds
    must be committed in this file. The old sequential 303509-303534 range values
    are ALL wrong and must ALL be replaced. Do NOT leave any of the old values unless
    the discovery script explicitly validates a match (which is unlikely given the
    sequential pattern is wrong).

    IMPORTANT: If the discovery script cannot be run in this session due to missing
    real externals, use the following approach instead: query TA's public destination
    search for each state using the typeahead URL
    https://www.tripadvisor.com/TypeAheadJson?action=API&startTime={epoch}&uiOrigin=xxx&query={state_name}&max=10&types=geo
    and extract the "value" (geoId) from the first result whose "type" is "GEO" and
    "detailsType" is "area" and name matches the state. This endpoint does not require
    session cookies and can be used with a plain httpx GET.

    Step B — Fix fetch_destinations query_id resolution in client.py.

    In fetch_destinations (~line 336), replace the single line:
      query_id = session.get("query_ids", {}).get("destinations", "")
    with a three-step resolution chain:
      1. config.query_id_override.get("destinations") — operator override wins
      2. session.get("query_ids", {}).get("destinations") — legacy session key
      3. _DESTINATIONS_QID module constant (None until discovered)
      If all three are falsy, raise ValueError:
        "No destinations queryId configured. Set BRAVE_TA_QUERY_ID_OVERRIDE "
        '= {"destinations":"<qid>"} or pin _DESTINATIONS_QID in client.py. '
        "Discover the QID by inspecting browser DevTools: POST /data/graphql/ids "
        "for a TA destinations/geo listing page and copy the preRegisteredQueryId."

    Add at module level near _LISTING_QID:
      # Destinations (GEO entities) persisted query id.
      # Discovered by inspecting browser DevTools: the POST to /data/graphql/ids
      # that returns locations[] for a Brazilian state geo page.
      # Set to None until captured from a real session; override via
      # BRAVE_TA_QUERY_ID_OVERRIDE={"destinations":"<qid>"}.
      _DESTINATIONS_QID: str | None = None

    Do NOT change the variables shape in fetch_destinations ({locationId, offset, limit}).
    Do NOT change anything else in fetch_destinations.

    Step C — Tests.

    In test_geo.py, add a test class TestUfGeoidsSeed:
    - test_uf_geoids_has_27_keys: load the real uf_geoids.json via
      geo.load_uf_geoids(geo.GEO_SEED_PATH); assert set(result.keys()) equals the
      expected 27 UF codes.
    - test_uf_geoids_all_positive_ints: assert all(v > 0 for v in result.values()).
    - test_uf_geoids_no_legacy_sequential_range: assert no value v satisfies
      303509 <= v <= 303534 (the previously wrong range; validates the fix landed).

    In test_client.py, add a TestFetchDestinationsQid class:
    - test_uses_config_override_qid: construct TripAdvisorClient with fakeredis
      pre-loaded with a session where query_ids={"query_0":"old_qid"} (no "destinations"
      key), set config.query_id_override={"destinations":"override_qid"}, mock the
      POST with respx returning a valid empty response, call await client.fetch_destinations("SP"),
      assert the captured request JSON body[0]["extensions"]["preRegisteredQueryId"]
      == "override_qid".
    - test_raises_when_no_qid_configured: session has query_ids={"query_0":"x"}
      (no "destinations"), config.query_id_override={}, and temporarily patch
      client._DESTINATIONS_QID to None; assert calling fetch_destinations raises ValueError
      matching "No destinations queryId configured".
  </action>
  <verify>
    <automated>.venv/bin/python -m pytest tests/unit/lanes/tripadvisor/test_geo.py::TestUfGeoidsSeed tests/unit/lanes/tripadvisor/test_client.py::TestFetchDestinationsQid -x -q</automated>
  </verify>
  <done>
    - uf_geoids.json has 27 entries, all values > 0, none in 303509-303534 range
    - fetch_destinations prioritizes config.query_id_override["destinations"] over broken session lookup
    - ValueError raised (not empty list) when no QID is available
    - scripts/ta_discover_state_geoids.py exists, guards on RUN_REAL_EXTERNALS, documents the discovery + validation protocol
    - All 5 new tests pass offline
  </done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: Null-safe parser + fetch_attraction_detail + atrativos linkage</name>
  <files>
    brave/lanes/tripadvisor/client.py,
    brave/clients/base.py,
    brave/clients/null_tripadvisor.py,
    tests/fakes/fake_tripadvisor.py,
    brave/lanes/tripadvisor/atrativos.py,
    tests/unit/lanes/tripadvisor/test_client.py,
    tests/unit/lanes/tripadvisor/test_atrativos.py
  </files>
  <behavior>
    - test_parse_null_bubble_rating_no_attribute_error: call _parse_attractions_page
      with a card where bubbleRating=None; assert no AttributeError is raised and the
      card is returned with rating=0.0 and review_count=0
    - test_parse_null_card_title_no_attribute_error: card where cardTitle=None;
      assert card returned with name=""
    - test_parse_null_primary_info_no_attribute_error: card where primaryInfo=None;
      assert card returned with category=""
    - test_fetch_attraction_detail_sends_correct_payload: using respx to mock POST
      to _TA_GRAPHQL_URL; call fetch_attraction_detail(312332); assert request body is
      [{"variables":{"locationId":312332},"extensions":{"preRegisteredQueryId":"444040f131735091"}}]
    - test_fetch_attraction_detail_returns_none_on_missing_data: mock POST returning
      [{"data":{"locations":[]}}]; assert return is None
    - test_ingest_one_resolves_ibge_via_detail_parents: construct
      TripAdvisorAtrativosIngest with FakeTripAdvisorClient that returns a detail
      fixture of parents=[{"locationId":303444,"localizedName":"Foz do Iguaçu"}];
      pass ta_config=TripAdvisorConfig(page_throttle_seconds=0) so the throttle sleep
      is skipped in offline tests (page_throttle_seconds=0 means no sleep);
      provide an ibge_records list that includes Foz do Iguaçu/PR; provide an
      attraction card with name="Cataratas Iguaçu" (no lat/lng — ibge name match will
      miss); assert the record is stored in Nascente (not quarantined as ibge_unmatched)
      and canonical.municipio == "Foz do Iguaçu"
    - test_ingest_one_still_quarantines_when_detail_also_misses: detail returns None;
      attraction card has no lat/lng and name does not fuzzy-match; assert quarantine
      as ibge_unmatched
  </behavior>
  <action>
    Step A — Null-safe parser in client.py _parse_attractions_page.

    In _parse_attractions_page (~lines 172-181), replace all three occurrences of
    `.get(k, {}).get(...)` with `(card.get(k) or {}).get(...)`.
    Specifically, change:
      card.get("cardTitle", {}).get("text", "")   →  (card.get("cardTitle") or {}).get("text", "")
      card.get("bubbleRating", {}).get("rating")  →  (card.get("bubbleRating") or {}).get("rating")
      card.get("bubbleRating", {}).get("reviewCount") →  (card.get("bubbleRating") or {}).get("reviewCount")
      card.get("primaryInfo", {}).get("text", "")  →  (card.get("primaryInfo") or {}).get("text", "")

    Do NOT change any other logic in the method. The `or {}` guard short-circuits on
    None (present-but-null) while `.get(k, {})` only catches absent keys.

    Step B — Add fetch_attraction_detail to TripAdvisorClient.

    Add the following async method to TripAdvisorClient (place after fetch_attractions,
    before fetch_attractions_paginated):

    async def fetch_attraction_detail(self, location_id: int) -> dict | None:
      """Fetch the TA detail record for a single attraction (qid 444040f131735091).

      Returns the first location dict from the response (contains parents[] geo
      hierarchy). Returns None on empty response or any parsing error.
      Never raises on data shape issues — returns None instead.

      Used by TripAdvisorAtrativosIngest._ingest_one as a tertiary ibge fallback:
      when name-match and geocoder both miss, parents[0].localizedName gives the
      parent city name which can be fuzzy-matched against IBGE.

      Args:
        location_id: TripAdvisor integer locationId of the attraction.

      Raises:
        SessionMissingError: When no session is in Redis.
        SessionExpiredError: On 403 or 429 HTTP status.
      """
      from brave.lanes.tripadvisor.session import persist_rotated_cookies  # noqa
      session = self._get_session()
      cookies = session.get("cookies", {})
      user_agent = session.get("user_agent", "")
      headers: dict[str, str] = {"Content-Type": "application/json"}
      if user_agent:
        headers["User-Agent"] = user_agent
      proxy = self._config.proxy_url or None
      payload = [
        {
          "variables": {"locationId": location_id},
          "extensions": {"preRegisteredQueryId": "444040f131735091"},
        }
      ]
      async with httpx.AsyncClient(cookies=cookies, follow_redirects=True, proxy=proxy) as hc:
        resp = await hc.post(_TA_GRAPHQL_URL, json=payload, headers=headers)
      if resp.status_code in (403, 429):
        raise SessionExpiredError(
          f"TripAdvisor detail returned {resp.status_code} — session expired."
        )
      resp.raise_for_status()
      rotated = dict(resp.cookies)
      if rotated:
        try:
          persist_rotated_cookies(self._redis, rotated, self._config)
        except Exception:  # noqa: BLE001
          pass
      try:
        data = resp.json()
        locations = data[0]["data"]["locations"]
        if not locations:
          return None
        return locations[0]
      except (IndexError, KeyError, TypeError, ValueError):
        return None

    Throttle: callers in atrativos._ingest_one sleep using self._ta_config before
    calling fetch_attraction_detail (see Step F — no new config field needed, no
    self._config usage for this).

    Step C — Add fetch_attraction_detail to TripAdvisorClientProtocol in base.py.

    In the TripAdvisorClientProtocol class (after resolve_geo_id), add:

    async def fetch_attraction_detail(self, location_id: int) -> dict | None:
      """Fetch the detail record (parents[] geo hierarchy) for one attraction.

      Args:
        location_id: TripAdvisor integer locationId.

      Returns:
        First location dict from the GraphQL response (includes parents[]),
        or None when the response is empty or malformed.

      Raises:
        SessionMissingError: When no session is in Redis (real client).
        SessionExpiredError: On 403 or 429 HTTP status (real client).
      """
      ...

    Step D — Add null stub to NullTripAdvisorClient in null_tripadvisor.py.

    Add after resolve_geo_id:
    async def fetch_attraction_detail(self, location_id: int) -> dict | None:
      """Return None — offline stub performs no detail lookup.
      Args:
        location_id: TripAdvisor locationId (ignored).
      Returns:
        None.
      """
      return None

    Step E — Add fake stub to FakeTripAdvisorClient in tests/fakes/fake_tripadvisor.py.

    Add fixture_details: dict[int, dict | None] param to __init__, a detail_calls
    recording list, and the method:

    async def fetch_attraction_detail(self, location_id: int) -> dict | None:
      self.detail_calls.append(location_id)
      return self._fixture_details.get(location_id)

    Step F — Wire detail-parents ibge fallback in atrativos._ingest_one.

    First, add `import asyncio` to the top-level import block of atrativos.py
    (alongside the other stdlib imports such as `import asyncio` if not already
    present). Do NOT use a local/lazy import inside the method — `asyncio` is
    stdlib and top-level is correct here. client.py already imports it top-level
    at :33; follow the same pattern.

    Second, extend TripAdvisorAtrativosIngest.__init__ to accept an optional
    ta_config parameter:

      def __init__(
          self,
          config: ScoreConfig,
          client: TripAdvisorClientProtocol,
          ...,
          ta_config: TripAdvisorConfig | None = None,
      ) -> None:
          ...
          self._ta_config = ta_config

    ta_config defaults to None so all existing call-sites continue to work without
    change. Import TripAdvisorConfig at the top of atrativos.py where settings are
    already imported; use TYPE_CHECKING guard if the import would create a cycle.

    Third, in _ingest_one, after the geocoder fallback block (~line 198) and
    before the `if ibge_match is None: quarantine(ibge_unmatched)` block, add:

    # Tertiary fallback: TA detail query parents[] → parent city name → IBGE.
    # Only fires when name-match AND geocoder both failed (ibge_match is still None).
    # Cost: +1 GraphQL request per unresolved card. Throttle guards DataDome.
    if ibge_match is None and self._ta_config is not None:
      throttle = self._ta_config.page_throttle_seconds
      if throttle > 0:
        await asyncio.sleep(throttle)
      detail = await self._client.fetch_attraction_detail(int(location_id))
      if detail is not None:
        parents = detail.get("parents") or []
        city_name = parents[0].get("localizedName") if parents else None
        if city_name:
          ibge_match = resolve_municipio(
            city_name,
            uf,
            self._ibge_records,
          )

    Notes on this block:
    - `self._ta_config` guards the whole block — when None (existing call-sites
      without ta_config), the detail fallback is skipped entirely; behaviour is
      backward-compatible.
    - `page_throttle_seconds` comes from TripAdvisorConfig, not ScoreConfig —
      accessing it through self._ta_config is correct.
    - resolve_municipio is called WITHOUT a threshold= kwarg, matching the existing
      calls at atrativos.py:173 and :190 (both use the default of 88). Do NOT pass
      threshold= here.
    - Do NOT use self._config.page_throttle_seconds or self._config.ibge_match_threshold
      anywhere in this block — ScoreConfig has neither attribute.

    Do NOT change _ingest_one_bulk (that is the national path, different contract).

    Step G — Tests.

    In test_client.py, add TestParserNullSafety and TestFetchAttractionDetail classes.

    In TestParserNullSafety, build cards with the three null fields (one card per test)
    and call TripAdvisorClient._parse_attractions_page([section_with_null_card]);
    assert no exception and correct default values.

    Section structure for the null test cards:
    {
      "__typename": "WebPresentation_SingleFlexCardSection",
      "singleFlexCardContent": {
        "cardTitle": None,              # or present for other tests
        "bubbleRating": None,
        "primaryInfo": None,
        "cardLink": {"webRoute": {"typedParams": {"detailId": "12345"}}}
      }
    }

    In TestFetchAttractionDetail:
    - Set up TripAdvisorClient with fakeredis session (cookies, user_agent, query_ids)
    - Use respx to mock POST to _TA_GRAPHQL_URL
    - assert correct payload shape
    - assert None returned on empty locations

    In test_atrativos.py, add TestDetailParentsLinkage:
    - Build a FakeTripAdvisorClient with fixture_details={312332: {"parents": [
        {"locationId": 303444, "localizedName": "Foz do Iguaçu"},
        {"locationId": 303435, "localizedName": "Paraná"}
      ]}} and fixture_attractions={303435: [{"locationId": 312332, "name":
      "Cataratas XPTO", "review_count": 10, "rating": 4.5, "category": "Nature"}]}
    - Build ibge_records mini-list with IbgeMunicipio(ibge_code="4108304",
      nome="Foz do Iguaçu", uf="PR", lat=-25.5163, lng=-54.5854)
    - Build a destino_rio_map with key "4108304" → (some_uuid, "tripadvisor:destination:303444")
    - Construct TripAdvisorAtrativosIngest with ta_config=TripAdvisorConfig(page_throttle_seconds=0)
      so the throttle sleep is a no-op in offline tests
    - Mock store_raw / process_nascente_record (use MagicMock on session or a
      FakeSession that records calls)
    - Call await ingest._ingest_one("PR", entity, run_rio=False)
    - Assert fake_client.detail_calls == [312332] (detail was called because name miss)
    - Assert nascente was stored (not quarantined)

    For the "detail also misses" test: fixture_details={} (no detail for 312332);
    also construct with ta_config=TripAdvisorConfig(page_throttle_seconds=0);
    assert quarantine_poison was called with task_name="brave.ta.atrativos.ibge_unmatched".
  </action>
  <verify>
    <automated>.venv/bin/python -m pytest tests/unit/lanes/tripadvisor/test_client.py::TestParserNullSafety tests/unit/lanes/tripadvisor/test_client.py::TestFetchAttractionDetail tests/unit/lanes/tripadvisor/test_atrativos.py::TestDetailParentsLinkage -x -q</automated>
  </verify>
  <done>
    - _parse_attractions_page handles null bubbleRating/cardTitle/primaryInfo without AttributeError
    - fetch_attraction_detail exists on TripAdvisorClient, TripAdvisorClientProtocol, NullTripAdvisorClient, FakeTripAdvisorClient
    - TripAdvisorAtrativosIngest.__init__ accepts optional ta_config: TripAdvisorConfig | None (defaults None; existing call-sites unaffected)
    - atrativos._ingest_one calls fetch_attraction_detail (when ta_config is set) as tertiary ibge fallback; uses ta_config.page_throttle_seconds for throttle; calls resolve_municipio without threshold= kwarg
    - import asyncio is at top-level in atrativos.py (not lazy inside method)
    - All new tests pass offline; full TA test suite green (.venv/bin/python -m pytest tests/unit/lanes/tripadvisor/ -x -q)
  </done>
</task>

<task type="checkpoint:human-verify" gate="blocking">
  <what-built>
    Task 1: corrected uf_geoids.json (27 state-level geoIds) + fixed fetch_destinations
    QID resolution. Task 2: null-safe parser + fetch_attraction_detail + atrativos
    detail-parents ibge fallback.
  </what-built>
  <how-to-verify>
    Offline verification (required, no real session needed):
      .venv/bin/python -m pytest tests/unit/lanes/tripadvisor/ -x -q
    All TA lane tests must be green.

    Real-session verification (opt-in, requires RUN_REAL_EXTERNALS=1 + live session):
      # 1. Validate geoIds (confirm each UF redirect is state-level)
      RUN_REAL_EXTERNALS=1 .venv/bin/python scripts/ta_discover_state_geoids.py
      # Confirm: each UF shows "VALID" with state name in the canonical URL.
      # If any UF shows INVALID, update uf_geoids.json for that UF only.

      # 2. Test per-UF attractions scope (pick AC + a dense UF like SP)
      RUN_REAL_EXTERNALS=1 .venv/bin/python -c "
      import asyncio
      from brave.config.settings import AppConfig
      from brave.lanes.tripadvisor.client import TripAdvisorClient
      from brave.lanes.tripadvisor.geo import resolve_geo_id
      import fakeredis; r = fakeredis.FakeRedis()
      # inject your real session first, then:
      config = AppConfig().tripadvisor
      client = TripAdvisorClient(config, r)
      geo_ac = asyncio.run(client.resolve_geo_id('AC'))
      cards = asyncio.run(client.fetch_attractions(geo_ac))
      print(f'AC geoId={geo_ac} → {len(cards)} cards')
      print('First card:', cards[0]['name'] if cards else 'EMPTY')
      "
      # Expected: geoId != 303509, cards are Acre attractions (not Serra dos Órgãos/RJ)

      # 3. Test destinos (requires BRAVE_TA_QUERY_ID_OVERRIDE set with real destinations QID)
      # Discover the QID from browser DevTools as documented in scripts/ta_discover_state_geoids.py
      # then: BRAVE_TA_QUERY_ID_OVERRIDE='{"destinations":"<qid>"}' + real session
      # run fetch_destinations("AC") and confirm > 0 results

      # 4. Test detail-parents linkage on a known attraction (e.g. locationId 312332)
      RUN_REAL_EXTERNALS=1 .venv/bin/python -c "
      import asyncio
      from brave.config.settings import AppConfig
      from brave.lanes.tripadvisor.client import TripAdvisorClient
      import fakeredis; r = fakeredis.FakeRedis()
      config = AppConfig().tripadvisor
      client = TripAdvisorClient(config, r)
      detail = asyncio.run(client.fetch_attraction_detail(312332))
      print('detail parents:', detail.get('parents') if detail else 'None')
      "
      # Expected: parents[0].localizedName = "Foz do Iguaçu"
  </how-to-verify>
  <resume-signal>
    Type "approved" when offline tests pass.
    If real-session validation finds any wrong geoId or missing QID, describe the
    finding so Task 1 corrections can be applied.
  </resume-signal>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| TA GraphQL response → client parser | TA can return null/missing fields (confirmed live); parser must be defensive |
| detail query parents[] → city name → IBGE resolver | Parent city name from TA is free-text; fuzzy match at threshold=88 filters near-misses |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-rmz-01 | Tampering | uf_geoids.json | mitigate | Discovery script validates each geoId via canonical redirect check before committing |
| T-rmz-02 | Information Disclosure | fetch_attraction_detail | mitigate | Logging discipline inherited from existing client: never log cookies/UA/session_id; log only locationId + error-class |
| T-rmz-03 | DoS | detail call per card | accept | Throttle reuses ta_config.page_throttle_seconds (2s default); only fires on ibge miss path (not common case); whole block skipped when ta_config is None |
| T-rmz-04 | Spoofing | _DESTINATIONS_QID None placeholder | mitigate | ValueError raised immediately when no QID in override/session/constant; never silently sends empty QID |
| T-rmz-SC | Tampering | npm/pip installs | accept | No new packages installed in this task |
</threat_model>

<verification>
Full TA lane offline suite must be green after all tasks:
  .venv/bin/python -m pytest tests/unit/lanes/tripadvisor/ -x -q

Specific new tests (subset gate — fast):
  .venv/bin/python -m pytest \
    tests/unit/lanes/tripadvisor/test_geo.py::TestUfGeoidsSeed \
    tests/unit/lanes/tripadvisor/test_client.py::TestFetchDestinationsQid \
    tests/unit/lanes/tripadvisor/test_client.py::TestParserNullSafety \
    tests/unit/lanes/tripadvisor/test_client.py::TestFetchAttractionDetail \
    tests/unit/lanes/tripadvisor/test_atrativos.py::TestDetailParentsLinkage \
    -x -q

Protocol compliance checks (structural typing):
  .venv/bin/python -c "from brave.clients.null_tripadvisor import _check_protocol_compliance; _check_protocol_compliance(); print('ok')"
  .venv/bin/python -c "from tests.fakes.fake_tripadvisor import _check_protocol_compliance; _check_protocol_compliance(); print('ok')"
</verification>

<success_criteria>
1. data/tripadvisor/uf_geoids.json has 27 entries, no value in 303509-303534 range,
   all positive ints; structural offline test passes.
2. fetch_destinations uses config.query_id_override["destinations"] first; raises
   ValueError (not empty list) when no QID is configured; offline test passes.
3. _parse_attractions_page does not raise AttributeError on null bubbleRating/
   cardTitle/primaryInfo; null cards included with 0.0/empty defaults; 3 tests pass.
4. fetch_attraction_detail method exists on TripAdvisorClient + Protocol + NullClient
   + FakeClient; sends correct payload with qid 444040f131735091; returns None on
   empty response; 2 tests pass.
5. TripAdvisorAtrativosIngest accepts ta_config: TripAdvisorConfig | None = None;
   existing call-sites require no change; new tests construct with
   ta_config=TripAdvisorConfig(page_throttle_seconds=0) for fast offline runs.
6. atrativos._ingest_one calls fetch_attraction_detail after name-match and geocoder
   miss (when ta_config is not None); parent city name from parents[0].localizedName
   is fed to resolve_municipio without threshold= kwarg (uses default 88); a card
   resolving via detail parents is stored (not quarantined); 2 tests pass.
7. import asyncio is at top-level in atrativos.py; no lazy import inside _ingest_one.
8. Full .venv/bin/python -m pytest tests/unit/lanes/tripadvisor/ -x -q is green.
</success_criteria>

<output>
Create .planning/quick/260629-rmz-fix-tripadvisor-lane-geo-targeting-and-a/260629-rmz-SUMMARY.md when done.
</output>
