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
matching `brave:*` — it never runs `FLUSHALL` and never drops the schema or the
`alembic_version` row.

## How to run

Use the project venv so SQLAlchemy + redis are importable. The script resolves the
Postgres/Redis URLs from `--db-url`/`--redis-url`, then `$BRAVE_DB_URL` /
`$BRAVE_DB_REDIS_URL`, then the repo-root `.env`, so it works without exporting env.

```bash
# Full data wipe + brave:* Redis flush (the default scope), no prompt:
.venv/bin/python .claude/skills/reset-brave-db/scripts/reset_db.py --yes
```

Useful variants:

```bash
# Keep the audit / cost trail, wipe only territorial + pipeline data:
.venv/bin/python .claude/skills/reset-brave-db/scripts/reset_db.py --yes \
  --keep audit_log --keep llm_generations

# Postgres only, leave Redis (engine counters/session) intact:
.venv/bin/python .claude/skills/reset-brave-db/scripts/reset_db.py --yes --no-redis

# Interactive (prompts for a typed 'reset' confirmation):
.venv/bin/python .claude/skills/reset-brave-db/scripts/reset_db.py
```

The script prints per-table before→after row counts and the Redis key delete count,
so report those back to the user as the proof of what was wiped.

## After a reset — cold-start note

On an empty base, **atrativos have no parent destino** yet, so a Places/atrativos
sweep will log many `parent_destino_absent` warnings and route records to DLQ until
destinos are collected first. That's expected cold-start behavior, not a bug. If the
engine was running, its Redis state (counts, depth, source, run_id, TA session) is
cleared by the `brave:*` flush, so the painel shows the motor idle with zeroed counts.
