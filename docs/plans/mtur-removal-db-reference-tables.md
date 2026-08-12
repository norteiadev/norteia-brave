# DB-backed reference data + remove the mtur destino-seed lane

## Context

Today the **mtur / "default" sweep** (`sweep_uf` task → `MturSeedIngest`) reads static CSVs
(`data/mtur/municipios_mtur_2025.csv`, `data/ibge/ibge_municipios.csv`) and pushes every
municipality through the whole Brave pipeline (Nascente → Rio → Mar) **only to materialize
parent "destino" records** that the attraction lanes link to. That data is static — running it
through scoring/DLQ is pointless churn.

**Goal:** load the reference data into DB tables at migration/seed time, have the collection
lanes read destinos from those tables, and retire the mtur destino-seed process.

**Decisions (confirmed with user):**
1. Seed three tables from the static files: `municipios` (IBGE + folded-in mtur turistic
   `categoria`/`regiao_turistica`), `distritos` (IBGE distritos), `uf_geoids` (TA geoIds JSON).
2. Fold the mtur turistic categorization onto `municipios` as nullable columns (keep the signal).
3. Keep the Places "default" atrativos + WhatsApp discovery track **as code, but dormant**:
   relocate the shared agent modules out of `brave/domains/mtur/`, repoint the Places
   parent-destino lookup at the table, but ship it **disabled** (`source.default.enabled=false`)
   so nothing Places runs now — re-enablable later via config.
4. **Dashboard surfaces TripAdvisor only** — the "default"/Places source is removed from the
   source selector (not shown, not activatable). Backend wiring for it stays intact.

**Key facts that shape the approach** (verified in code):
- **TA is already decoupled** — `TripAdvisorAtrativosIngest._ensure_destino`
  (`brave/domains/tripadvisor/atrativos.py:240-300`) synthesizes parent destinos from the IBGE
  CSV (`source="ibge"`, `source_ref="ibge:{uf}:{ibge}"`, promote-to-Rio). The mtur-fed
  `destino_rio_map` (`brave/tasks/pipeline.py:1101-1115`) is only a fast-path; empty map falls
  through to `_ensure_destino`. So "adapt TA" = swap its CSV loader for a DB loader.
- **Shared-code trap** — `discovery.py`, `contact.py`, `signal.py`, `number_discovery.py`,
  `dtos.py`, `description.py` live under `brave/domains/mtur/` but are the generic attractions
  lane, consumed by TA + Places + WhatsApp via `brave/lanes/atrativos/*` `sys.modules` shims.
  Deleting the dir wholesale breaks attractions. The shims are the neutral home — **promote them
  to real modules** (reverse the alias) to minimize test churn (tests already patch
  `brave.lanes.atrativos.*`).
- **Places parent lookup differs** — `_resolve_parent_destino` (`brave/domains/mtur/discovery.py:112-167`)
  reads **Mar** by `source_ref` `mtur:{uf}:{ibge}` and DLQs `parent_destino_absent`. After the
  seed is gone there are no Mar destinos → must repoint to the same ensure-from-table path as TA.
- **Places name→IBGE lookup is dead in the live path** — `RealPlacesClient` is built without
  `ibge_lookup` (`pipeline.py:675`), so `municipio_ibge` is `""`. Wiring it from the new table is
  part of making Places resolve parents post-mtur.
- **Source-name asymmetry** — engine/API/dashboard speak `"default"`; registry/services/source_ref
  speak `"mtur"`. Keep the `"default"` slug (it now = Places lane); drop user-facing "mTur".

---

## 1. New DB tables

Add three models to `brave/core/models.py` (single `Base`, picked up by `alembic/env.py:20`).
Column types chosen so the existing resolver dataclasses `IbgeMunicipio`
(`brave/domains/tripadvisor/ibge.py:30`) and `IbgeDistrito` (`brave/shared/ibge_distritos.py`)
round-trip unchanged.

- **`municipios`** — `ibge_code` `String(7)` PK, `nome` `String(128)`, `uf` `String(2)` indexed,
  `lat`/`lng` `Float`, plus nullable `categoria` `String(32)` + `regiao_turistica` `String(128)`
  (the mtur fold-in; only ~2922/5571 rows carry them).
- **`distritos`** — `distrito_code` `String(9)` PK, `nome` `String(128)`, `ibge_code` `String(7)`
  indexed, `municipio_nome` `String(128)`, `uf` `String(2)`.
- **`uf_geoids`** — `uf` `String(2)` PK, `geo_id` `Integer`.

**Migration `alembic/versions/0011_reference_tables.py`** — `revision="0011"`,
`down_revision="0010"`, DDL-only (mirror `0009_config_settings.py:33-50`: plain `op.create_table`
for all three, indexes on `municipios.uf` + `distritos.ibge_code`; `downgrade` drops). Row data
does **not** go in the migration — it lives in the seed script (§2).

## 2. Seed mechanism

New idempotent **`scripts/seed_reference_data.py`** (mirrors `scripts/seed_config.py`: reads
`BRAVE_DB_URL`, builds a `sessionmaker`, one commit).
- **Idempotent:** per-table count-gate — bulk-load only when the table is empty; else no-op.
- **municipios:** reuse the CSV parse from `ibge.py:66-77`; build
  `{co_municipio: (categoria, regiao_turistica)}` from `data/mtur/municipios_mtur_2025.csv` and
  set the nullable columns on matching rows. **Move `_map_categoria`** (`brave/clients/mtur.py:55-86`,
  handles old A/B/C/D/E + 2025 nomenclature) into this seed module — the only bit of `MturClient`
  worth keeping.
- **distritos:** reuse the parse in `ibge_distritos.py`. Skip `ibge_subdistritos.csv` (unused).
- **uf_geoids:** reuse `geo.py` JSON parse → 27 rows.
- Use `bulk_insert_mappings` for the ~16k rows in one transaction.

**Migrate-time wiring** — extend the migrate command (`docker-compose.yml:71-73`):
`alembic upgrade head && python -m scripts.seed_reference_data && python -m scripts.seed_config`.

**reset-brave-db consistency** — reference tables are static carga-inicial, not pipeline data.
In `.claude/skills/reset-brave-db/scripts/reset_db.py`, add
`REFERENCE_TABLES = {"municipios","distritos","uf_geoids"}` and fold it into the never-truncate
set beside `PROTECTED_TABLES` (line 45) so a reset preserves them (nothing FK-references them).

## 3. Repoint loaders (keep every resolver; swap only the loader source)

Add DB loaders returning the **same dataclass lists** so all downstream resolver call sites are
untouched:
- `brave/domains/tripadvisor/ibge.py`: add `load_ibge_municipios(session) -> list[IbgeMunicipio]`.
  Keep `resolve_municipio`, `resolve_municipio_national`, `haversine_km`, `load_ibge_csv`.
- `brave/shared/ibge_distritos.py`: add `load_distritos(session) -> list[IbgeDistrito]`.
- `brave/domains/tripadvisor/geo.py`: change the seed-fallback branch of `resolve_geo_id`
  (`:100-106`) to read `uf_geoids` from DB. Callers carry `redis`+`config` but no `session`, so
  open a short-lived engine from `BRAVE_DB_URL` inside the fallback (mirror `beat_schedule.py:92-105`);
  fires only on Redis miss. Keep the `seed_path` param as a test override.
- `brave/clients/places.py`: repoint `build_mtur_ibge_lookup` → `load_municipio_name_ibge_lookup(session)`
  (SELECT nome, uf, ibge_code from `municipios`; reuse `_normalize_name`).

**Call sites:** `pipeline.py:1010-1011` → `load_ibge_municipios(session)`;
`pipeline.py:694-701` and `:1493-1500` → `load_distritos(session)`; `pipeline.py:675`
(`RealPlacesClient` in `discover_atrativo`) → pass `ibge_lookup=load_municipio_name_ibge_lookup(session)`
so Places attractions get `municipio_ibge`. `find_contacts`/`gather_signals` use `place_details`
→ no lookup needed.

## 4. Relocate shared agents → `brave/lanes/atrativos/`

Promote the shims to real modules (reverse the `sys.modules` alias) — keeps every
`patch("brave.lanes.atrativos.*")` working:
- `domains/mtur/discovery.py` → `lanes/atrativos/discovery_agent.py`
- `domains/mtur/contact.py` → `lanes/atrativos/contact_finder_agent.py`
- `domains/mtur/signal.py` → `lanes/atrativos/signal_agent.py`
- `domains/mtur/number_discovery.py` → `lanes/atrativos/number_discovery.py`
- `domains/mtur/dtos.py` → `lanes/atrativos/schemas.py`
- `domains/mtur/description.py` → `lanes/atrativos/description.py` (no existing shim)

Fix internal cross-imports (`brave.domains.mtur.*` → `brave.lanes.atrativos.*`). Update the direct
import at `pipeline.py:1450`. Update test patch-paths that name the OLD location:
`tests/unit/test_atrativos_schemas.py:140,147,157` and `tests/unit/lanes/test_description_agent.py:30`.
Cosmetic docstring refs: `brave/shared/ibge_distritos.py:5`, `brave/shared/whatsapp/schemas.py:5`.

## 5. Repoint Places parent-destino (extract a shared helper)

Extract `ensure_destino(session, config, *, ibge_code, nome, uf) -> (parent_rio_id,
parent_source_ref, parent_mar_id | None)` into `brave/shared/destino.py` (shared home avoids a
domain→domain edge, D-18). Body = TA's `_ensure_destino` (`store_raw` source="ibge" +
`process_nascente_record`, idempotent), plus a lookup of whether the destino reached Mar to fill
the optional `parent_mar_id`.
- **TA:** `atrativos.py:_ensure_destino` delegates to the helper (behavior unchanged).
- **Places:** in `discovery_agent.py`, delete `_resolve_parent_destino` (`:112-167`) and the
  `parent_destino_absent` quarantine (`:283-302`); call `ensure_destino(...)` with the now-populated
  `municipio_ibge`/`municipio_nome`. Payload (`:336-364`) carries `parent_rio_id`+`parent_source_ref`
  (TA-consistent) and `parent_mar_id` when non-null. `produce_for_destino` (`:543-544`) passes its
  in-hand `parent_mar` id through.
- **Dashboard coupling (highest risk):** `routing.py:216-226` copies only `parent_mar_id`
  payload→normalized, and `cms.py` reads `parent_mar_id`. TA already writes `parent_rio_id`, so
  verify how TA attractions currently surface their parent in `/painel` **before** finalizing.
  Mitigation: `ensure_destino` returns both ids; `routing.py` copies both into `normalized` so the
  CMS parent linkage works uniformly whether or not the ensured destino reached Mar.

## 6. Remove the mtur destino-seed

**Delete:** `brave/domains/mtur/{services,repositories,exceptions,models,__init__}.py` +
`tests/` (then the empty dir); `brave/clients/mtur.py` (after moving `_map_categoria`),
`brave/clients/null_mtur.py`, `MturClientProtocol` (`brave/clients/base.py:181`) + `__init__`
exports; `brave/lanes/destinos/mtur.py` shim; `sweep_uf` task (`pipeline.py:796-881`) + its
imports; scripts `ingest_destinos.py`, `mtur_download_2025.py`, `mtur_xlsx_to_csv.py`.

**MturDomain → neutral `brave/domains/places/controllers.py` (`PLACES_DOMAIN`):** `name="default"`,
`produces=("attraction",)`; `sweep_plan` drops all `brave.sweep_uf` dispatch and the free
NASCENTE destino seed (Places always costs) — `atrativos`/`both` →
`SweepDispatch("brave.discover_atrativo", (uf,), {"depth": depth})`; `beat_entries` emits only
`sweep-atrativos-{uf}-daily`. Check `discover()` callers (only wrapped MturSeedIngest) — repoint to
the Places `DiscoveryAgent` or drop.

**Registry** `brave/domains/__init__.py:36-41`: replace the two mtur lines with
`"default" → PLACES_DOMAIN`; remove the `"mtur"` alias. Keep `"default"` in engine
`_VALID_SOURCES` (`brave/core/engine.py:75`) so it stays a valid re-enablable target.

**Dormant by default** — set the default enabled flag for the Places lane to **false**:
`_default_sources` (`brave/config/settings.py:466`) → `{"default": False, "tripadvisor": True}`,
and the seeded `source.default.enabled` row (`brave/config/runtime.py:267`) → false. Because
`build_beat_schedule` is enabled-gated, beat then emits **no** `sweep-atrativos-{uf}-daily` until
re-enabled. Change the `/engine/start` default source (`brave/api/routers/engine.py:225,340`) from
`"default"` → `"tripadvisor"`. The Places track is fully present but inert.

**Tests:** delete `test_mtur_lane.py`, `test_sweep_uf.py`, `fake_mtur.py`, mtur-only parts of
`test_destinos_lane.py`; move `test_mtur_categoria_mapping.py` → a seed test for the moved
`_map_categoria`; fix `test_registry.py` (assert `default → PLACES_DOMAIN`, drop the
`get_domain("default") is get_domain("mtur")` assertion), `test_engine_depth_gating.py`,
`test_domain_boundaries.py`.

## 7. Dashboard — TripAdvisor only

Surface **only** TripAdvisor as a source; the "default"/Places lane is not shown or activatable.
- `PainelOrigem.tsx` — remove the `"mTur"`/`"default"` radio row entirely; TripAdvisor is the sole
  (and default-selected) option. Drop the `mtur→activateSource("default")` remap.
- `engine-api.ts:48` — narrow `EngineSource` to `"tripadvisor"` (or keep the union but never render
  "default"); ensure the start/status paths default to `"tripadvisor"`.
- `PainelTopbar.tsx`, `PainelLogs*.tsx`, `StageBadge.tsx`, `painel-mapeamento.ts` — drop the
  `"mtur"`/`"default"` labels/keys from user-facing surfaces (keep `source_ref`-parsing paths that
  merely *display* a historical `mtur:`/`ibge:` prefix).
- Mocks: `runs.ts:33,57`, `destinos.ts:75`, `funnels.ts:26`, `dedup.ts:39,56`, `config.ts:30` →
  drop `"mtur"`; reflect `sources:{tripadvisor:true, default:false}`.
- Update bun-test expectations asserting `"mtur"`/`"mTur"`/"Padrão by default" — TripAdvisor is now
  the default and only shown source.

---

## Ordering constraints

1. Models + migration 0011 + `seed_reference_data` land and run **first**.
2. Relocate shared agents (§4) **before** deleting the mtur package (§6) — the moves *are* the
   relocation; only seed-only leftovers get deleted.
3. Extract `ensure_destino` (§5) before repointing Places.
4. Wire `load_municipio_name_ibge_lookup` into `RealPlacesClient` before any real Places sweep.
5. Registry repoint + PlacesDomain + beat/sweep_plan + the dormant `enabled=false` flag land together.
6. Dashboard (TripAdvisor-only) is independent as long as the `"default"` API slug stays valid for
   the dormant backend lane.

## Risks
- **Parent-linkage semantics (highest)** — CMS reads `parent_mar_id`, TA writes `parent_rio_id`.
  Verify live `/painel` parent linkage before finalizing; mitigate by returning + copying both ids.
- **`resolve_geo_id` DB fallback** — worker needs `BRAVE_DB_URL` (it has it); DB read only on Redis miss.
- **Behavior change** — the default lane's free NASCENTE reach collects nothing now (Places always
  costs). Document it.
- **Stray `MturClientProtocol` importers** — grep before deleting.

## Verification

1. `docker compose up` → migrate runs `alembic upgrade head` (0011) → `seed_reference_data` → `seed_config`.
2. Row counts: `municipios`=5571 (`categoria` non-null ~2922), `distritos`=10751, `uf_geoids`=27.
3. **TA sweep** (`/engine/start` source=`tripadvisor`): attractions resolve parents via
   `load_ibge_municipios` → `ensure_destino` (`ibge:{uf}:{ibge}`); zero `parent_destino_absent`.
4. **Default/Places sweep (optional, dormant lane)** — only if you temporarily set
   `source.default.enabled=true` + `RUN_REAL_EXTERNALS=1`: `RealPlacesClient(ibge_lookup=...)`
   populated, `municipio_ibge` resolves, parent links via `ensure_destino`. Off by default.
5. Beat emits **no** Places sweeps (default lane disabled); `get_domain("default")` → `PLACES_DOMAIN`
   still resolves and can be re-enabled via config. Dashboard shows TripAdvisor as the only source.
   Optional smoke: flip `source.default.enabled=true` in config → confirm the dormant Places lane
   still resolves parents from the table.
6. `.venv/bin/python -m pytest` (updated/removed tests per §4/§6); `cd dashboard && bun run test`.
7. Run `reset-brave-db`: `municipios`/`distritos`/`uf_geoids` preserved, data tables wiped,
   `config_settings` re-seeded.
