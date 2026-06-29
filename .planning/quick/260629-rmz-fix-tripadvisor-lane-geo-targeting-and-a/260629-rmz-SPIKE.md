# 260629-rmz SPIKE findings (live, 2026-06-29)

Spike-first decision. Probed the real TA session before planning implementation. Results below
**reframe the root cause** and simplify the fix.

## 🔴 FINDING 1 (decisive) — `uf_geoids.json` geoIds are WRONG, not the query

- `fetch_attractions(geo_id=303509)` returned "Serra dos Órgãos" etc. — initially read as "national /
  not scoped". **Wrong interpretation.** Live HTML GET of `Attractions-g303509-...` **redirects to**
  `…-Teresopolis_State_of_Rio_de_Janeiro` — title "THE 15 BEST Things to Do in **Teresopolis**".
- **geoId 303509 = Teresópolis/RJ, NOT Acre.** Serra dos Órgãos IS in Teresópolis → the query scoped
  **correctly** to 303509. We simply sent the wrong geoId for "AC".
- => Bug 1 root cause is the **incorrect UF→geoId table** (`data/tripadvisor/uf_geoids.json`), whose
  sequential `30350x–30353x` values are arbitrary cities, not Brazilian state geoIds. **Both
  transports (GraphQL AttractionsFusion AND HTML-SSR) scope fine by geoId.**
- Implication for the LOCKED decision "HTML-SSR per-UF": transport choice is now largely moot — the
  real fix is **correct geoIds**. HTML-SSR is still fine (and the redirect's canonical place name is a
  handy way to *validate* a geoId), but switching transport is not required.
- **Open implementation work:** obtain the correct TA geoId for each of the 27 UFs (state-level
  "State of X" geoIds). Source options: TA typeahead/search API by state name, or the redirect-name
  validation loop (GET each candidate, assert canonical contains "State of <UF>"). Also decide
  state-level vs city-level granularity (TA state pages list attractions across the state).

## 🔴 FINDING 2 — destinos=0 root cause = empty query_id, not sparsity

- `fetch_destinations(uf)` uses `query_id = session["query_ids"].get("destinations", "")`
  (client.py ~:336). But the cURL-paste parser stores query_ids **positionally** (`query_0..query_N`),
  with **no `"destinations"` key** (confirmed: session status `query_ids: [query_0..query_23]`).
- => `query_id = ""` → invalid persisted query → **0 locations**, every time, every UF. Not AC sparsity.
- **Open work:** identify the real destinos query_id + variables (the destinos GraphQL query shape),
  and stop relying on a semantic `"destinations"` key that the parser never produces. The destinos
  lane may need its own captured/pinned query like attractions has `a5cb7fa004b5e4b5`.

## 🟡 FINDING 3 — atrativo→município linkage needs a city-geoId→IBGE map (confirmed)

- Listing card has no município/coords (re-confirmed). Detail query `444040f131735091`
  (variables{locationId}) returns `parents[]` (city geoId → state → Brazil); `parents[0]` = city geoId
  (e.g. locationId 312332 Iguazu → 303444 Foz do Iguaçu).
- **No city-geoId→IBGE map exists** (only `uf_geoids.json` UF→state). Implementation must build one,
  or fuzzy-match the parent city NAME (from the detail/`parents` localizedName) to IBGE município.
- Cost: +1 detail request per attraction (throttle + DataDome exposure) — acceptable per discuss
  decision; measure during implementation.

## 🟡 FINDING 4 — parser null-unsafe (confirmed live)

- `_parse_attractions_page` (client.py:172-181) `.get(k, {}).get(...)` raises AttributeError when
  `bubbleRating`/`cardTitle`/`primaryInfo` is present-but-null (review-less attractions) →
  `ta_parse_skip_malformed_card` drops the card. Fix: `(card.get(k) or {})`. Small.

## ⚠️ SIDE FINDING (NOT this task) — p2v keepalive beat is NOT firing

- After ~40 min, `brave:ta:session` lapsed (ttl=-2) and `ta_keepalive` **never executed** in the
  worker/beat logs (only the registration line). The p2v **cookie write-back works** (validated: TTL
  slid up during active AC sweeps), but the **keepalive beat is not running**, so an idle session
  still lapses at session_ttl — defeating "1 cURL forever" during idle. Needs a separate follow-up to
  diagnose why the redbeat `ta-keepalive` entry doesn't dispatch (beat process state after
  worker/queue restarts? schedule not loaded by the long-running beat? queue routing to brave.sweep?).

## Net recommendation for the implementation plan
1. **Correct `uf_geoids.json`** (the 27 real state geoIds) + a validation step (redirect canonical
   contains the state) — this alone fixes "wrong attractions". Core of the task.
2. **Fix the destinos query** (real query_id + variables; drop the missing `"destinations"` key
   dependency).
3. **Build city-geoId→IBGE map** (or parent-city-name→IBGE) + wire atrativo linkage via detail
   `parents`; throttle the +1 detail call.
4. **Null-safe `_parse_attractions_page`.**
5. Re-validate per-UF with a real session (a denser UF + AC) before declaring done.

Implementation deferred (spike-first complete). A fresh session is recommended to author/execute the
plan given this session's length.
