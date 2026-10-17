---
name: reset-brave-db
description: >-
  Reset the norteia-brave collector database to a clean "carga inicial" state —
  truncate every data table (Nascente/Rio/Mar/DLQ/runs_history/audit/etc.) while
  keeping the schema + Alembic head, and flush the engine/cache `brave:*` keys
  from Redis. Use this whenever the user wants to wipe, clear, reset, zero out,
  or "refresh" the Brave database / pipeline data, e.g. "limpe a base de dados",
  "zerar a base", "reset the db", "refresh the database", "comecar do zero",
  "clean the pipeline data", or before a fresh whole-Brazil sweep. This is the
  fast data-only reset (no migrations re-run). Trigger even if the user only says
  "limpa o banco" / "reset db" without naming the tables.
---

# Reset Brave DB

Wipes all collected pipeline data from the `norteia-brave` Postgres and flushes the
engine/cache state in Redis, so the collector starts a fresh cold "carga inicial".
The **schema and `alembic_version` are preserved** — this is a data reset, not a
migration reset, so it's fast and leaves the DB at the current Alembic head.

The static **reference tables are also always preserved**: `municipios`, `distritos`,
`uf_geoids`, `local_businesses` and `atrativo_buscas` (`REFERENCE_TABLES` in
`scripts/reset_db.py`). `atrativo_buscas` holds paid Parallel searches that descriptions are
regenerated from — wiping it would re-bill every search. They are carga-inicial data
seeded by `scripts/seed_reference_data.py`, not pipeline output — wiping them breaks
geo resolution and the TA lane. If they ever come back empty, re-seed with:
`docker compose run --rm --entrypoint sh migrate -c "/app/.venv/bin/python -m scripts.seed_reference_data"`

By default, the broker (pending Celery tasks **and** the `unacked*` keys holding tasks a
worker had reserved under `acks_late`) is also purged, so stale tasks don't re-fire after
a reset — kombu would otherwise restore an in-flight sweep after `visibility_timeout` and
re-run it (with paid Places calls) against the empty base.

### What the Redis flush keeps

Not everything under `brave:*` is pipeline data. These survive by default:

- **redbeat** — `brave:schedule`, `brave:statics`, `brave:lock` (`redbeat_key_prefix="brave"`).
  Deleting the lock kills a running beat (`LockNotOwnedError`), deleting the schedule drops
  every periodic entry (ta-keepalive, repush, batch collection, prune) until beat restarts.
- **TA session** — `brave:ta:session`, `brave:ta:needs_bootstrap`, `brave:ta:keepalive_failures`
  (operator-pasted credential; a data reset doesn't force a re-login).
- **Cost guard** — `brave:cost:*` (today's spend). Wiping it grants a second daily budget in the
  same UTC day. `--reset-cost` zeroes it when that is really wanted.
- **Places key credit** — `brave:cost:places:total:*` (no TTL: how much of each Google key's
  US$300 credit was spent). Kept even with `--reset-cost`; only `--reset-places-credit` zeroes
  it (the exhausted key would spend its whole credit again).
- **Geo caches** — `brave:geo:*` (30-day Nominatim cache, 1.1 s/call to rebuild) and
  `brave:ta:geo:*`. `--flush-geo-cache` drops them.

### In-flight description batches

The script **refuses** (exit 4) when `rio_records.descricao_batch_id` is set on any row: those
Anthropic batches are already paid and only that column lets `collect-description-batches`
pick up the results. Wait for collection, or pass `--discard-inflight-batches` to accept losing them.

## When to use

Any "make the base empty again" request: `limpe a base`, `zerar a base`, `reset db`,
`refresh database`, `começar do zero`, or prepping a clean run before turning the
motor on. If the user wants to also rebuild the schema from scratch, that's a
different operation (`alembic downgrade base && alembic upgrade head`) — mention it
but don't do it unless asked.

## Safety — this is destructive and irreversible

There is no backup. Before running, **state the scope and confirm** unless the user
has clearly already authorized this exact wipe in the conversation. The script
itself refuses to run in a non-interactive shell without `--yes`, and otherwise
prompts for a typed `reset` confirmation.

It only ever truncates tables in the `public` schema and only deletes Redis keys
matching `brave:*` minus the preserved set above — it never runs `FLUSHALL` and never
drops the schema or the `alembic_version` row. The broker purge is similarly scoped:
only `celery`, `unacked`, `unacked_index`, `unacked_mutex` and `_kombu*` are deleted.

## How to run

Use the project venv so SQLAlchemy + redis are importable. The script resolves the
Postgres/Redis URLs from `--db-url`/`--redis-url`, then `$BRAVE_DB_URL` /
`$BRAVE_DB_REDIS_URL`, then the repo-root `.env`, so it works without exporting env.

```bash
# Full data wipe + brave:* Redis flush + Celery broker purge (the default scope), no prompt:
.venv/bin/python .claude/skills/reset-brave-db/scripts/reset_db.py --yes
```

Useful variants:

```bash
# Keep the audit / cost trail, wipe only territorial + pipeline data:
.venv/bin/python .claude/skills/reset-brave-db/scripts/reset_db.py --yes \
  --keep audit_log --keep llm_generations

# Postgres only, leave Redis (engine counters/session) intact:
.venv/bin/python .claude/skills/reset-brave-db/scripts/reset_db.py --yes --no-redis

# Also zero today's cost-guard spend / drop the geocode caches (both kept by default):
.venv/bin/python .claude/skills/reset-brave-db/scripts/reset_db.py --yes --reset-cost --flush-geo-cache

# Skip broker purge (keep queued tasks — rarely needed):
.venv/bin/python .claude/skills/reset-brave-db/scripts/reset_db.py --yes --no-broker-purge

# Preserve the operator config (thresholds/weights/source toggles/engine mode)
# across the data wipe instead of resetting it to defaults:
.venv/bin/python .claude/skills/reset-brave-db/scripts/reset_db.py --yes --keep config_settings

# Skip the post-wipe config_settings default re-seed (leave the table empty):
.venv/bin/python .claude/skills/reset-brave-db/scripts/reset_db.py --yes --no-seed

# Interactive (prompts for a typed 'reset' confirmation):
.venv/bin/python .claude/skills/reset-brave-db/scripts/reset_db.py
```

The script prints per-table before→after row counts and the Redis key delete count,
so report those back to the user as the proof of what was wiped.

### config_settings (persisted operator config, Alembic 0009)

The wipe truncates `config_settings` — the persisted overlay for the operator-tunable
config (score weights/threshold, per-source enabled flags, and the engine **mode**).
By default the script then **re-seeds the idempotent defaults** (`brave.config.runtime.
seed_default_config`, values equal to the env-effective `AppConfig`, so no behavior
change), so the painel **Config** view and the engine mode come up in a known clean
state. Two knobs:

- `--keep config_settings` — preserve the operator's tuned config across a data-only reset.
- `--no-seed` — wipe it and leave it empty (effective config then falls back to env
  defaults via `load_effective_config`; re-seed later with
  `.venv/bin/python -m scripts.seed_config`).

## After a reset — cold-start note

On an empty base, **atrativos have no parent destino** yet, so a Places/atrativos
sweep will log many `parent_destino_absent` warnings and route records to DLQ until
destinos are collected first. That's expected cold-start behavior, not a bug. If the
engine was running, its Redis state (counts, depth, source, run_id, sweep progress) is
cleared by the `brave:*` flush, so the painel shows the motor idle with zeroed counts. The
TA session, beat schedule, cost guard and geo caches are kept (see above).

Stale queued tasks (e.g., old `push_destination` referencing reset-away rio_ids) are
cleared by the broker purge step. If workers were running, restart them after reset
so they pick up fresh tasks only.
