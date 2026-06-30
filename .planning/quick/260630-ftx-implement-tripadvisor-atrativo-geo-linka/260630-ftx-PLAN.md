---
phase: quick-260630-ftx
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - brave/lanes/tripadvisor/uf_names.py
  - tests/unit/lanes/tripadvisor/test_uf_names.py
  - brave/clients/base.py
  - brave/clients/null_tripadvisor.py
  - tests/fakes/fake_tripadvisor.py
  - brave/lanes/tripadvisor/client.py
  - brave/lanes/tripadvisor/atrativos.py
  - tests/unit/lanes/tripadvisor/test_client.py
  - tests/unit/lanes/tripadvisor/test_atrativos.py
autonomous: true
requirements: [TA-ftx-01]

must_haves:
  truths:
    - "fetch_attraction_geo(312332) returns {city_name:'Foz do Iguacu', city_geo_id:303444, state_geo_id:303435} for the Cataratas fixture (SPIKE-2 validated)"
    - "state_name_to_uf('State of Parana') returns 'PR'; state_name_to_uf('Federal District') returns 'DF' (live-confirmed: DF has no 'State of ' prefix); unknown name returns None"
    - "_ingest_one tertiary fallback calls fetch_attraction_geo when ibge_match is still None after card-coords + geocoder, derives UF via state_name_to_uf, resolves IBGE via resolve_municipio(city_name, derived_uf, ...)"
    - "atrativos.py contains NO reference to parents[0].localizedName (the broken rmz-04 path is fully replaced)"
    - "NullTripAdvisorClient.fetch_attraction_geo returns None; FakeTripAdvisorClient.fetch_attraction_geo returns from fixture_geo dict"
    - "TestDetailParentsLinkage class (both methods) is REMOVED from test_atrativos.py — coverage superseded by TestAtrativosGeoFallback"
    - "test_uf_names.py covers all 27 UFs parametrized, including 'Federal District'→'DF', 'State of ' strip, and ASCII-fold ('State of Sao Paulo'→'SP')"
    - "All prior TA offline tests continue to pass (.venv/bin/python -m pytest tests/unit/lanes/tripadvisor/)"
  artifacts:
    - path: "brave/lanes/tripadvisor/uf_names.py"
      provides: "28-key _TA_STATE_CANONICAL dict (27 UFs; 'federal district' and 'distrito federal' both map to 'DF') + state_name_to_uf() (optionally strips 'State of ', NFKD-folds, maps to 2-letter UF)"
      contains: "state_name_to_uf"
    - path: "tests/unit/lanes/tripadvisor/test_uf_names.py"
      provides: "Parametrized pytest suite for state_name_to_uf — all 27 UFs, DF no-prefix, 'State of ' strip, ASCII-fold, None on unknown"
      contains: "test_uf_names"
    - path: "brave/clients/base.py"
      provides: "fetch_attraction_geo abstract method in TripAdvisorClientProtocol"
      contains: "fetch_attraction_geo"
    - path: "brave/lanes/tripadvisor/client.py"
      provides: "Real fetch_attraction_geo posting qid d3d4987463b78a39; fetch_attraction_detail docstring updated (no longer caller in _ingest_one)"
      contains: "d3d4987463b78a39"
    - path: "brave/lanes/tripadvisor/atrativos.py"
      provides: "Rewired IBGE fallback via fetch_attraction_geo + state_name_to_uf"
      contains: "fetch_attraction_geo"
  key_links:
    - from: "brave/lanes/tripadvisor/atrativos.py"
      to: "brave/lanes/tripadvisor/uf_names.py"
      via: "from brave.lanes.tripadvisor.uf_names import state_name_to_uf"
      pattern: "state_name_to_uf"
    - from: "brave/lanes/tripadvisor/atrativos.py"
      to: "TripAdvisorClientProtocol.fetch_attraction_geo"
      via: "await self._client.fetch_attraction_geo(loc_id_int)"
      pattern: "fetch_attraction_geo"
    - from: "brave/lanes/tripadvisor/client.py"
      to: "_TA_GRAPHQL_URL"
      via: "POST payload with preRegisteredQueryId d3d4987463b78a39"
      pattern: "d3d4987463b78a39"
---

<objective>
Implement the TA atrativo → município geo-linkage via the single GraphQL query
d3d4987463b78a39 (validated live, SPIKE-2 2026-06-30). Replaces the broken
parents[0].localizedName path shipped in rmz (TA-rmz-04) where that field does NOT
exist in live TA data.

Purpose: Attractions can be correctly linked to their IBGE município and, through
destino_rio_map, to their parent Mtur destino. Without this fix the tertiary IBGE
fallback silently does nothing — all coordless, name-unmatched attractions quarantine
as "ibge_unmatched" instead of resolving correctly.

Output: uf_names.py (new), test_uf_names.py (new), fetch_attraction_geo on full
protocol stack (base/null/fake/real), atrativos._ingest_one rewired, offline tests
for client + ingest coverage, TestDetailParentsLinkage removed (superseded).

Out of scope (explicitly): coords enrichment via daebddd2c711c5fb; TA destinos query
(_DESTINATIONS_QID stays None; Mtur/IBGE seeds destinos); sweep-by-city / TA city
catalog / typeahead; corrected-geoId discovery (rmz shipped uf_geoids.json); p2v
keepalive.
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/quick/260629-rmz-fix-tripadvisor-lane-geo-targeting-and-a/260629-rmz-SPIKE-2-linkage.md
@brave/lanes/tripadvisor/client.py
@brave/lanes/tripadvisor/atrativos.py
@brave/clients/base.py
@brave/clients/null_tripadvisor.py
@tests/fakes/fake_tripadvisor.py

<interfaces>
<!-- Key contracts for the executor. No codebase exploration needed beyond these. -->

From brave/clients/base.py — TripAdvisorClientProtocol (Protocol class):
  Methods already on the protocol:
    async def fetch_destinations(self, uf: str) -> list[dict]: ...
    async def fetch_attractions(self, geo_id: int, max_pages: int | None = None) -> list[dict]: ...
    async def fetch_attractions_paginated(self, geo_id: int, ...) -> AsyncIterator[...]: ...
    async def resolve_geo_id(self, uf: str) -> int: ...
    async def fetch_attraction_detail(self, location_id: int) -> dict | None: ...
  ADD: async def fetch_attraction_geo(self, location_id: int) -> dict | None: ...

From brave/lanes/tripadvisor/client.py — TripAdvisorClient:
  _TA_GRAPHQL_URL = "https://www.tripadvisor.com/data/graphql/ids"
  Session wiring (copy exactly from fetch_attraction_detail):
    session = self._get_session()
    cookies = session.get("cookies", {})
    user_agent = session.get("user_agent", "")
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if user_agent: headers["User-Agent"] = user_agent
    proxy = self._config.proxy_url or None
    # lazy import inside method:
    from brave.lanes.tripadvisor.session import persist_rotated_cookies
  403/429 guard: raise SessionExpiredError(...)
  persist_rotated_cookies(self._redis, rotated, self._config) — swallowed on error
  All parse errors: except (IndexError, KeyError, TypeError, ValueError): return None

From brave/lanes/tripadvisor/ibge.py — resolve_municipio signature:
  def resolve_municipio(
      name: str, uf: str, records: list[IbgeMunicipio], *,
      threshold: int = 88, max_distance_km: float = 15.0,
      candidate_lat: float | None = None, candidate_lng: float | None = None,
  ) -> IbgeMunicipio | None

From brave/lanes/tripadvisor/atrativos.py — TripAdvisorAtrativosIngest:
  __init__(self, ta_client, session, config, ibge_records, destino_rio_map=None,
           geocoder=None, ta_config: TripAdvisorConfig | None = None)
  self._ta_config: TripAdvisorConfig | None
  self._client: TripAdvisorClientProtocol
  self._ibge_records: list[IbgeMunicipio]

BROKEN code to replace in atrativos._ingest_one (lines ~212–232):
  detail = await self._client.fetch_attraction_detail(loc_id_int)
  if detail is not None:
      parents: list[dict] = detail.get("parents") or []
      if parents:
          parent_city_name = parents[0].get("localizedName", "")  # BROKEN — field absent in live data
          if parent_city_name:
              ibge_match = resolve_municipio(parent_city_name, uf, self._ibge_records)

SPIKE-2 response shape for d3d4987463b78a39 (scrubbed, for fixture):
  [{"data": {"gtmData": {"locationData": {
      "cityName": "Foz do Iguacu",
      "stateName": "State of Parana",
      "stateId": 303435,
      "countryName": "Brazil",
      "countryId": 294280,
      "locationHierarchy": ":312332:1:13:294280:303435:303444:"
  }}}}]
  → city_geo_id = last non-empty element of locationHierarchy.split(':') = "303444" → int(303444)

LIVE-CONFIRMED DF shape (probe 2026-06-30): stateName is "Federal District" — no "State of " prefix.
  {"cityName":"Brasilia","stateName":"Federal District","stateId":303321}
  → state_name_to_uf must handle prefix-absent case: strip "State of " ONLY when present.
  → _TA_STATE_CANONICAL must contain "federal district"→"DF" in addition to "distrito federal"→"DF".

From brave/config/settings.py — TripAdvisorConfig:
  page_throttle_seconds: float = Field(default=2.0, ...)  # 0.0 disables throttling in tests

From tests/unit/lanes/tripadvisor/test_atrativos.py — existing test helpers:
  _IBGE_RECORDS = [IbgeMunicipio("3170107", "Uberlândia", "MG", -18.9186, -48.2772), ...]
  _GEO_ID_MG = 303380
  _IBGE_CODE_UB = "3170107"
  _PARENT_RIO_ID = uuid.uuid4()
  _DESTINO_RIO_MAP = {_IBGE_CODE_UB: (_PARENT_RIO_ID, "tripadvisor:destination:303380")}
  _make_coordless_card() → {locationId:312332, name:"Cachoeira do Tabuleiro", review_count:100, rating:4.0, category:"Waterfalls"}
  (name does NOT match any IBGE record — forcing the geo fallback)
</interfaces>
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: UF name map + unit tests + protocol stack extension (base/null/fake)</name>
  <files>
    brave/lanes/tripadvisor/uf_names.py,
    tests/unit/lanes/tripadvisor/test_uf_names.py,
    brave/clients/base.py,
    brave/clients/null_tripadvisor.py,
    tests/fakes/fake_tripadvisor.py
  </files>
  <behavior>
    - state_name_to_uf("State of Parana") == "PR"
    - state_name_to_uf("State of Rio de Janeiro") == "RJ"
    - state_name_to_uf("State of Minas Gerais") == "MG"
    - state_name_to_uf("State of Sao Paulo") == "SP"  (ASCII-folded input; no accent)
    - state_name_to_uf("Parana") == "PR"  (no "State of " prefix — strip is optional)
    - state_name_to_uf("Federal District") == "DF"  (live-confirmed: DF has no "State of " prefix)
    - state_name_to_uf("Distrito Federal") == "DF"  (Portuguese form also maps correctly)
    - state_name_to_uf("unknown state xyz") is None
    - All 27 UFs present in _TA_STATE_CANONICAL (28 keys total — DF has both "federal district" and "distrito federal")
    - NullTripAdvisorClient.fetch_attraction_geo(999) returns None
    - FakeTripAdvisorClient(fixture_geo={312332: geo_dict}).fetch_attraction_geo(312332) returns geo_dict
    - FakeTripAdvisorClient().fetch_attraction_geo(999) returns None
    - FakeTripAdvisorClient geo_calls list records each call's location_id
    - _check_protocol_compliance() passes for both NullTripAdvisorClient and FakeTripAdvisorClient after adding fetch_attraction_geo
  </behavior>
  <action>
    CREATE brave/lanes/tripadvisor/uf_names.py:

    Module docstring: "TripAdvisor stateName → IBGE 2-letter UF mapping (TA-ftx). The
    d3d4987463b78a39 query returns stateName in two observed forms: 'State of {X}'
    (English, e.g. 'State of Parana') for most states, or a bare English name with no
    prefix (e.g. 'Federal District' for DF — live-confirmed 2026-06-30). Pure dict,
    no runtime dependency. ToS/LGPD: aggregate geo only."

    _TA_STATE_CANONICAL: dict[str, str] with all entries (lowercase ASCII-folded keys).
    DF has TWO keys to handle both Portuguese ('distrito federal') and English bare form
    ('federal district'). All other states arrive as 'State of {X}' so their stripped
    form is the Portuguese ASCII name.
      "acre"→"AC", "alagoas"→"AL", "amapa"→"AP", "amazonas"→"AM", "bahia"→"BA",
      "ceara"→"CE",
      "distrito federal"→"DF",
      "federal district"→"DF",
      "espirito santo"→"ES", "goias"→"GO",
      "maranhao"→"MA", "mato grosso"→"MT", "mato grosso do sul"→"MS",
      "minas gerais"→"MG", "para"→"PA", "paraiba"→"PB", "parana"→"PR",
      "pernambuco"→"PE", "piaui"→"PI", "rio de janeiro"→"RJ",
      "rio grande do norte"→"RN", "rio grande do sul"→"RS", "rondonia"→"RO",
      "roraima"→"RR", "santa catarina"→"SC", "sao paulo"→"SP", "sergipe"→"SE",
      "tocantins"→"TO".

    def state_name_to_uf(state_name: str) -> str | None:
      Strip leading/trailing whitespace. Lowercase the value. If it starts with
      "state of " (the 9-character prefix), strip those first 9 characters — this strip
      is CONDITIONAL (only when the prefix is present; 'Federal District' arrives without
      it and must NOT be stripped). Apply unicodedata.NFKD + encode("ascii","ignore")
      + decode() + .lower() to normalize the remaining text. Lookup in _TA_STATE_CANONICAL;
      return the 2-letter code or None if not found.

    CREATE tests/unit/lanes/tripadvisor/test_uf_names.py:

    Parametrized class TestStateNameToUf covering:
      (a) All 27 UFs via "State of X" or bare-name form — at minimum one test case per
          UF confirming state_name_to_uf returns the correct 2-letter code.
      (b) DF special cases: "Federal District"→"DF", "Distrito Federal"→"DF",
          "State of Distrito Federal"→"DF" (extra robustness).
      (c) "State of " strip: "State of Sao Paulo"→"SP", "State of Para"→"PA",
          "State of Minas Gerais"→"MG".
      (d) ASCII-fold: "State of São Paulo"→"SP" (accented input normalised to "SP"),
          "State of Pará"→"PA".
      (e) Unknown input: "unknown state xyz"→None, ""→None, "Federal Republic"→None.
      (f) Whitespace tolerance: "  State of Parana  "→"PR".

    Use @pytest.mark.parametrize for the all-27-UFs cases so a single failing entry is
    immediately identifiable. Import state_name_to_uf from
    brave.lanes.tripadvisor.uf_names. No external dependencies — pure unit test.

    EDIT brave/clients/base.py — TripAdvisorClientProtocol:
    Add fetch_attraction_geo after fetch_attraction_detail. Docstring: "Fetch parent
    municipality geo data for one attraction (qid d3d4987463b78a39). Returns a normalized
    dict {location_id:int, city_name:str, state_name:str, city_geo_id:int,
    state_geo_id:int} or None when response is empty, malformed, or countryId != 294280
    (non-Brazil). ToS/LGPD: aggregate geo only (cityName/stateName/geoIds), no PII.
    Raises SessionMissingError when no session in Redis; SessionExpiredError on 403/429."
    Method body: ...

    EDIT brave/clients/null_tripadvisor.py:
    Add async def fetch_attraction_geo(self, location_id: int) -> dict | None: with
    docstring "Return None — offline stub performs no geo lookup." Return None.

    EDIT tests/fakes/fake_tripadvisor.py — FakeTripAdvisorClient:
    Add fixture_geo: dict[int, dict[str, Any] | None] | None = None parameter to __init__.
    Add self._fixture_geo: dict[int, dict[str, Any] | None] = fixture_geo or {}.
    Add self.geo_calls: list[int] = [] to call recording lists.
    Add method:
      async def fetch_attraction_geo(self, location_id: int) -> dict[str, Any] | None:
        """Record call and return fixture geo dict for locationId, or None if absent."""
        self.geo_calls.append(location_id)
        return self._fixture_geo.get(location_id)
  </action>
  <verify>
    <automated>.venv/bin/python -m pytest tests/unit/lanes/tripadvisor/test_uf_names.py tests/unit/lanes/tripadvisor/test_client.py -x -q 2>&1 | tail -5</automated>
  </verify>
  <done>
    state_name_to_uf maps all 27 UFs correctly including 'Federal District'→'DF' (no
    "State of " prefix) and 'Distrito Federal'→'DF'; test_uf_names.py passes with
    parametrized coverage of all 27 UFs, both DF forms, ASCII-fold, and unknown→None;
    fetch_attraction_geo present in Protocol, Null (→None), and Fake (→fixture);
    geo_calls recorded; protocol compliance checks pass for both Null and Fake.
  </done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: Real client fetch_attraction_geo + unit tests</name>
  <files>
    brave/lanes/tripadvisor/client.py,
    tests/unit/lanes/tripadvisor/test_client.py
  </files>
  <behavior>
    - POST payload uses preRegisteredQueryId "d3d4987463b78a39" with variables
      {locationId: N, eventType: "PAGEVIEW", isGeoPage: true}
    - Parses data[0].data.gtmData.locationData → {location_id, city_name, state_name,
      city_geo_id (last non-empty in locationHierarchy.split(':')), state_geo_id}
    - countryId != 294280 → returns None (non-Brazil guard)
    - Malformed/empty response → returns None, no raise
    - 403 → raises SessionExpiredError; 429 → raises SessionExpiredError
    - persist_rotated_cookies called when resp.cookies non-empty (error swallowed)
    - Reuses same session/cookie/UA/proxy wiring as fetch_attraction_detail
  </behavior>
  <action>
    EDIT brave/lanes/tripadvisor/client.py — add fetch_attraction_geo after
    fetch_attraction_detail (around line 615):

    async def fetch_attraction_geo(self, location_id: int) -> dict | None:
      Docstring: "Fetch parent município geo data for one attraction (qid d3d4987463b78a39).
      Single GraphQL request — no HTML surface, no parents hop. Returns normalized dict
      {location_id, city_name, state_name, city_geo_id, state_geo_id} from
      data.gtmData.locationData. Returns None on empty response or any parsing error.
      ToS/LGPD: aggregate geo only (cityName/stateName/geoIds); no PII.
      Validated: 5 attractions / 2 cities (SPIKE-2 2026-06-30)."

      Lazy import: from brave.lanes.tripadvisor.session import persist_rotated_cookies
      Session wiring: copy EXACTLY from fetch_attraction_detail (same self._get_session(),
      cookies, user_agent, headers, proxy pattern).
      Payload (batched array of one):
        [{"variables": {"locationId": location_id, "eventType": "PAGEVIEW",
                        "isGeoPage": True},
          "extensions": {"preRegisteredQueryId": "d3d4987463b78a39"}}]
      POST to _TA_GRAPHQL_URL with httpx.AsyncClient(cookies=cookies,
        follow_redirects=True, proxy=proxy).
      403/429: raise SessionExpiredError(...).
      resp.raise_for_status() for other 5xx.
      persist_rotated_cookies (swallowed on error — same as fetch_attraction_detail).
      Parse:
        data = resp.json()
        loc_data = data[0]["data"]["gtmData"]["locationData"]
        Non-Brazil guard: if loc_data.get("countryId") != 294280: return None
        city_geo_id: last non-empty element of loc_data["locationHierarchy"].split(":")
          cast to int (0 if hierarchy empty/malformed — use try/except ValueError)
        Return {
          "location_id": location_id,
          "city_name": loc_data["cityName"],
          "state_name": loc_data["stateName"],
          "city_geo_id": city_geo_id,
          "state_geo_id": int(loc_data["stateId"]),
        }
      except (IndexError, KeyError, TypeError, ValueError): return None

    EDIT tests/unit/lanes/tripadvisor/test_client.py — add class TestFetchAttractionGeo:
    Use the existing test pattern: monkeypatch _get_session to return stub_session dict,
    respx.mock the POST to "https://www.tripadvisor.com/data/graphql/ids".

    Test 1 — happy path Foz do Iguacu fixture:
      respx returns the SPIKE-2 scrubbed response (see <interfaces> section).
      Assert result == {"location_id": 312332, "city_name": "Foz do Iguacu",
        "state_name": "State of Parana", "city_geo_id": 303444, "state_geo_id": 303435}.

    Test 2 — malformed response (missing gtmData key):
      respx returns [{"data": {}}]. Assert result is None.

    Test 3 — non-Brazil guard (countryId != 294280):
      respx returns loc_data with countryId=999999. Assert result is None.

    Test 4 — 403 raises SessionExpiredError.
    Test 5 — 429 raises SessionExpiredError.

    Also add to TestFakeTripAdvisorClient:
    Test fake_fixture_geo_returns_configured and fake_fixture_geo_records_calls
    (FakeTripAdvisorClient with fixture_geo={312332: some_dict}, assert geo_calls).
  </action>
  <verify>
    <automated>.venv/bin/python -m pytest tests/unit/lanes/tripadvisor/test_client.py -x -q 2>&1 | tail -5</automated>
  </verify>
  <done>
    fetch_attraction_geo in client.py: correct qid, correct parse path, non-Brazil guard,
    403/429 → SessionExpiredError, None on malformed. All new + existing test_client.py
    tests pass offline.
  </done>
</task>

<task type="auto" tdd="true">
  <name>Task 3: Rewire atrativos._ingest_one fallback + offline tests + cleanup</name>
  <files>
    brave/lanes/tripadvisor/atrativos.py,
    brave/lanes/tripadvisor/client.py,
    tests/unit/lanes/tripadvisor/test_atrativos.py
  </files>
  <behavior>
    - When ibge_match is None after card-coords + geocoder AND ta_config is not None:
      fetch_attraction_geo is called (not fetch_attraction_detail), and the result drives
      the IBGE resolution via state_name_to_uf + resolve_municipio
    - parents[0].localizedName is NEVER referenced in atrativos.py
    - When ta_config is None, fetch_attraction_geo is NOT called
    - When fetch_attraction_geo returns None, ibge_match stays None (no crash)
    - When state_name_to_uf returns None (unrecognized state), ibge_match stays None
    - Throttle (page_throttle_seconds > 0 → asyncio.sleep) still applies before the geo call
    - TestDetailParentsLinkage is ABSENT from test_atrativos.py (both methods deleted)
    - fetch_attraction_detail docstring in client.py notes it is no longer called by _ingest_one
  </behavior>
  <action>
    EDIT brave/lanes/tripadvisor/client.py — fetch_attraction_detail docstring update (W2):
    Locate fetch_attraction_detail (~line 561). Prepend the following note to its existing
    docstring (do NOT change the method body or signature):
      "NOTE (TA-ftx): No longer called by TripAdvisorAtrativosIngest._ingest_one —
      replaced by fetch_attraction_geo (qid d3d4987463b78a39) which returns
      cityName/stateName directly without a parents[] hop. Method kept; existing
      TestFetchAttractionDetail tests remain valid and must not be removed."

    EDIT brave/lanes/tripadvisor/atrativos.py:

    Add import at the top (alongside existing brave.lanes.tripadvisor.* imports):
      from brave.lanes.tripadvisor.uf_names import state_name_to_uf

    In _ingest_one, REPLACE the broken fallback block that starts at
    "if ibge_match is None and self._ta_config is not None:" (~line 212) and ends
    after the parents[0].localizedName resolution (~line 232) with:

      # TA-ftx: geo-linkage via d3d4987463b78a39 — single GraphQL query returns
      # cityName + stateName directly. Replaces the broken parents[0].localizedName
      # path (rmz-04) where that field is absent from live TA data.
      # Validated: 5 attractions / 2 cities (SPIKE-2 2026-06-30).
      # ToS/LGPD: aggregate geo only (cityName/stateName/geoIds), no PII.
      if ibge_match is None and self._ta_config is not None:
          try:
              loc_id_int = int(location_id) if location_id else None
          except (ValueError, TypeError):
              loc_id_int = None
          if loc_id_int is not None:
              if self._ta_config.page_throttle_seconds > 0:
                  await asyncio.sleep(self._ta_config.page_throttle_seconds)
              geo = await self._client.fetch_attraction_geo(loc_id_int)
              if geo is not None:
                  derived_uf = state_name_to_uf(geo["state_name"])
                  if derived_uf:
                      ibge_match = resolve_municipio(
                          geo["city_name"],
                          derived_uf,
                          self._ibge_records,
                      )

    Verify no reference to "localizedName" or "parents[0]" remains in the fallback block
    (the rmz-04 comment may mention it descriptively but not as a live code path).

    EDIT tests/unit/lanes/tripadvisor/test_atrativos.py — TWO changes:

    CHANGE 1 — REMOVE TestDetailParentsLinkage entirely (BLOCKER 1):
    DELETE the complete class TestDetailParentsLinkage including both methods:
      - test_ingest_one_resolves_ibge_via_detail_parents
      - test_ingest_one_still_quarantines_when_detail_also_misses
    These tests assert fake_client.detail_calls == [312332] and behaviour of the
    parents[0].localizedName path — a code path that no longer exists after the rewire.
    After Task 3, detail_calls == [] and store_raw is not called via that path, causing
    both assertions to fail. Coverage is fully superseded by TestAtrativosGeoFallback
    (added below). Delete the class and its _make_fake_client / _FOZ_IBGE / _make_destino_rio_map helpers
    if they are private to that class and not shared. If any helper is used elsewhere,
    keep it.

    CHANGE 2 — ADD TestAtrativosGeoFallback:

    Import TripAdvisorConfig from brave.config.settings.

    Test 1 — test_geo_fallback_resolves_ibge_via_fetch_attraction_geo:
      Use _make_coordless_card() → card with name "Cachoeira do Tabuleiro" locationId=312332.
      This name does NOT match any _IBGE_RECORDS entry (by design of the fixture).
      Build FakeTripAdvisorClient(
          fixture_attractions={_GEO_ID_MG: [card]},
          geo_ids={"MG": _GEO_ID_MG},
          fixture_geo={312332: {
              "location_id": 312332,
              "city_name": "Uberlândia",
              "state_name": "State of Minas Gerais",
              "city_geo_id": 303380,
              "state_geo_id": 303383,
          }},
      )
      Pass ta_config=TripAdvisorConfig(page_throttle_seconds=0) to TripAdvisorAtrativosIngest.
      Run produce("MG", run_rio=False) with store_raw + process_nascente_record patched.
      Assertions:
        - fake_client.geo_calls == [312332]  (fetch_attraction_geo was called)
        - mock_store_raw.called  (record was stored, not quarantined)

    Test 2 — test_geo_fallback_skipped_when_ta_config_none:
      Same card, but TripAdvisorAtrativosIngest constructed WITHOUT ta_config
      (ta_config=None, the default). Run produce.
      Assert fake_client.geo_calls == []  (fetch_attraction_geo never called).
      (card may quarantine as ibge_unmatched — that is correct behavior without ta_config)

    Test 3 — test_geo_fallback_returns_none_no_crash:
      FakeTripAdvisorClient with fixture_geo={} (geo returns None for all locationIds).
      ta_config wired. Run produce with the coordless card.
      Assert fake_client.geo_calls == [312332] and no exception raised
      (quarantine occurs but no crash — quarantine_poison is called, not store_raw).
  </action>
  <verify>
    <automated>.venv/bin/python -m pytest tests/unit/lanes/tripadvisor/ -x -q 2>&1 | tail -10</automated>
  </verify>
  <done>
    atrativos.py contains "fetch_attraction_geo" and NOT "localizedName" in the fallback
    block. fetch_attraction_detail docstring in client.py notes it is no longer called by
    _ingest_one. TestDetailParentsLinkage class REMOVED (both methods deleted; no more
    detail_calls / store_raw assertions on the dead code path). All 3 new geo-fallback
    tests pass. Full TA unit suite passes (all prior tests green).
  </done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| TA GraphQL → fetch_attraction_geo | Response from www.tripadvisor.com is untrusted; all field access behind try/except |
| locationHierarchy split | String from external source; cast to int inside except ValueError |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-ftx-01 | Tampering | fetch_attraction_geo response parsing | mitigate | All parse paths wrapped in except (IndexError, KeyError, TypeError, ValueError) → None; non-Brazil guard (countryId != 294280) prevents mislinks |
| T-ftx-02 | Information Disclosure | gtmData.locationData fields | accept | cityName/stateName/geoIds are aggregate geo — no PII; author/review fields not requested in this query's variables; confirmed in SPIKE-2 response shape |
| T-ftx-03 | Elevation of Privilege | state_name_to_uf returning wrong UF | mitigate | Pure deterministic dict (no runtime logic that can be injected); derived_uf None-check before resolve_municipio prevents a mislinked UF match |
</threat_model>

<verification>
Full offline suite:
  .venv/bin/python -m pytest tests/unit/lanes/tripadvisor/ -x -q

Confirm test_uf_names.py exists and covers all 27 UFs:
  .venv/bin/python -m pytest tests/unit/lanes/tripadvisor/test_uf_names.py -v 2>&1 | grep PASSED | wc -l
  (expect ≥27 PASSED lines)

Confirm "Federal District"→"DF" in the map (W1):
  .venv/bin/python -c "from brave.lanes.tripadvisor.uf_names import state_name_to_uf; assert state_name_to_uf('Federal District') == 'DF', 'DF bare-name failed'; print('DF OK')"

Confirm no "localizedName" in the atrativos fallback block:
  grep -n "localizedName" brave/lanes/tripadvisor/atrativos.py
  (should return zero lines in the _ingest_one fallback; the comment referencing rmz-04
  may mention it descriptively but not as a code path)

Confirm TestDetailParentsLinkage is gone (BLOCKER 1):
  grep -c "TestDetailParentsLinkage" tests/unit/lanes/tripadvisor/test_atrativos.py
  (expect 0)

Confirm d3d4987463b78a39 appears in client.py:
  grep -c "d3d4987463b78a39" brave/lanes/tripadvisor/client.py
  (expect >= 1)

Confirm state_name_to_uf covers all 27 UFs (dict has 28 keys — DF has two entries):
  .venv/bin/python -c "from brave.lanes.tripadvisor.uf_names import _TA_STATE_CANONICAL; ufs=set(_TA_STATE_CANONICAL.values()); print(len(ufs), 'UFs'); assert len(ufs)==27"
</verification>

<success_criteria>
- fetch_attraction_geo implemented and tested across full protocol stack (base Protocol / NullClient / FakeClient / real TripAdvisorClient)
- state_name_to_uf('State of Parana') == 'PR'; state_name_to_uf('Federal District') == 'DF' (live-confirmed bare-name); all 27 UFs covered
- test_uf_names.py exists with parametrized coverage of all 27 UFs, both DF forms, ASCII-fold, and unknown→None
- atrativos._ingest_one fallback calls fetch_attraction_geo (not fetch_attraction_detail); parents[0].localizedName references are gone
- TestDetailParentsLinkage class deleted from test_atrativos.py (grep returns 0)
- fetch_attraction_detail docstring updated to note it is no longer called by _ingest_one
- All prior TA offline tests pass; new geo-fallback tests (5+ new test methods across test_uf_names.py, test_client.py, test_atrativos.py) pass
- Run: .venv/bin/python -m pytest tests/unit/lanes/tripadvisor/ -x -q — green
</success_criteria>

<output>
Create .planning/quick/260630-ftx-implement-tripadvisor-atrativo-geo-linka/260630-ftx-SUMMARY.md when done
</output>
