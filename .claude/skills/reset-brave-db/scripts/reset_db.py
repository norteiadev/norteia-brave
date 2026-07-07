#!/usr/bin/env python3
"""Reset the norteia-brave Postgres + Redis to a clean "carga inicial" state.

Truncates every data table in the Brave Postgres — keeping the SCHEMA and the
`alembic_version` row intact — and flushes the engine/cache `brave:*` keys from
Redis. By default, also purges the Celery broker queue (the `celery` task list
key and `_kombu*` binding/unacked metadata) so stale queued tasks don't re-fire
after a reset and hit reset-away rio_ids.

This is the fast "start the collection over from cold" reset: no migrations are
re-run, so the DB stays at the current Alembic head.

DESTRUCTIVE + IRREVERSIBLE. There is no backup. Requires `--yes` (or an
interactive y/N confirmation) before it will touch anything.

Connection:
  - Postgres URL from --db-url, else $BRAVE_DB_URL, else the `BRAVE_DB_URL` line
    in the repo-root .env.
  - Redis URL from --redis-url, else $BRAVE_DB_REDIS_URL, else the .env line,
    else redis://localhost:6379/0.

Scope flags:
  --keep TABLE        preserve a table (repeatable), e.g. --keep audit_log --keep llm_generations
  --no-redis          leave Redis untouched (Postgres only); also skips broker purge
  --no-broker-purge   skip Celery broker queue purge (Postgres + brave:* flush still run)
  --redis-pattern     key glob to delete (default "brave:*"); never does FLUSHALL
  -y / --yes          skip the confirmation prompt (required in non-interactive use)

Examples:
  python scripts/reset_db.py            # interactive confirm, full data wipe + brave:* flush + broker purge
  python scripts/reset_db.py --yes      # no prompt (CI / agent)
  python scripts/reset_db.py --yes --keep audit_log --keep llm_generations  # keep the audit/cost trail
  python scripts/reset_db.py --yes --no-redis                               # Postgres only
  python scripts/reset_db.py --yes --no-broker-purge                        # skip broker purge only
"""

from __future__ import annotations

import argparse
import os
import sys

# alembic_version is the schema-version pointer — truncating it would make the DB
# look un-migrated. It is NEVER truncated by this script.
PROTECTED_TABLES = {"alembic_version"}


def _repo_root() -> str:
    # scripts/ lives at <repo>/.claude/skills/reset-brave-db/scripts/reset_db.py
    here = os.path.abspath(__file__)
    return os.path.abspath(os.path.join(here, "..", "..", "..", "..", ".."))


def _from_dotenv(key: str) -> str | None:
    """Best-effort read of a single KEY=VALUE from the repo-root .env (no deps)."""
    path = os.path.join(_repo_root(), ".env")
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                if k.strip() == key:
                    return v.strip().strip('"').strip("'")
    except OSError:
        return None
    return None


def _resolve(cli_value: str | None, env_key: str, default: str | None = None) -> str | None:
    return cli_value or os.environ.get(env_key) or _from_dotenv(env_key) or default


def _mask(url: str) -> str:
    """Hide credentials in a DSN before printing."""
    import re

    return re.sub(r"//[^@/]*@", "//***@", url)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Reset Brave Postgres data + flush brave:* Redis keys.")
    ap.add_argument("--db-url", default=None, help="Postgres URL (else $BRAVE_DB_URL / .env)")
    ap.add_argument("--redis-url", default=None, help="Redis URL (else $BRAVE_DB_REDIS_URL / .env)")
    ap.add_argument("--keep", action="append", default=[], metavar="TABLE",
                    help="preserve a table (repeatable)")
    ap.add_argument("--no-redis", action="store_true", help="do not touch Redis")
    ap.add_argument("--no-broker-purge", action="store_true",
                    help="skip Celery broker queue purge (Postgres + brave:* flush still run)")
    ap.add_argument("--no-seed", action="store_true",
                    help="skip re-seeding config_settings defaults after the wipe")
    ap.add_argument("--redis-pattern", default="brave:*", help='key glob to delete (default "brave:*")')
    ap.add_argument("-y", "--yes", action="store_true", help="skip the confirmation prompt")
    args = ap.parse_args(argv)

    db_url = _resolve(args.db_url, "BRAVE_DB_URL")
    if not db_url:
        print("ERROR: no Postgres URL — pass --db-url or set BRAVE_DB_URL (or add it to .env).", file=sys.stderr)
        return 2
    redis_url = _resolve(args.redis_url, "BRAVE_DB_REDIS_URL", "redis://localhost:6379/0")

    try:
        from sqlalchemy import create_engine, text
    except ImportError:
        print("ERROR: SQLAlchemy not importable — run with the project venv (.venv/bin/python).", file=sys.stderr)
        return 2

    keep = PROTECTED_TABLES | {t.strip() for t in args.keep}
    engine = create_engine(db_url)

    # Discover the data tables (everything in public except the protected/kept set).
    with engine.connect() as c:
        all_tables = sorted(
            r[0] for r in c.execute(
                text("select tablename from pg_tables where schemaname='public'")
            )
        )
        counts_before = {
            t: c.execute(text(f'select count(*) from "{t}"')).scalar() for t in all_tables
        }
    targets = [t for t in all_tables if t not in keep]

    # Show the plan.
    print(f"Postgres : {_mask(db_url)}")
    print(f"Redis    : {_mask(redis_url)}  (pattern {args.redis_pattern})"
          if not args.no_redis else "Redis    : (skipped)")
    print("\nWill TRUNCATE (RESTART IDENTITY CASCADE):")
    for t in targets:
        print(f"  - {t:32} {counts_before[t]} rows")
    if keep - PROTECTED_TABLES:
        print("Keeping (not truncated): " + ", ".join(sorted(keep - PROTECTED_TABLES)))
    print("Always preserved: " + ", ".join(sorted(PROTECTED_TABLES)))

    if not targets:
        print("\nNothing to truncate.")
    elif not args.yes:
        if not sys.stdin.isatty():
            print("\nREFUSING: destructive reset in a non-interactive shell without --yes.", file=sys.stderr)
            return 3
        reply = input("\nType 'reset' to wipe the data above (irreversible): ").strip()
        if reply != "reset":
            print("Aborted — nothing changed.")
            return 1

    # Postgres wipe.
    if targets:
        joined = ", ".join(f'"{t}"' for t in targets)
        with engine.begin() as c:
            c.execute(text(f"TRUNCATE {joined} RESTART IDENTITY CASCADE"))
        with engine.connect() as c:
            after = {t: c.execute(text(f'select count(*) from "{t}"')).scalar() for t in all_tables}
        print("\nPostgres reset:")
        for t in all_tables:
            tag = "kept" if t in keep else "wiped"
            print(f"  {t:32} {counts_before[t]:>7} -> {after[t]:<7} [{tag}]")

    # Re-seed config_settings defaults (Phase D). The wipe empties config_settings,
    # which would drop the operator-tunable baseline (score weights/threshold, per-source
    # enabled flags, engine mode). Repopulate the idempotent defaults so the painel Config
    # view and the engine mode come up in a known clean cold-start state. Values equal the
    # env-effective AppConfig, so this never changes pipeline behavior. Skipped when
    # config_settings was preserved (--keep) or --no-seed is passed. Best-effort: a seed
    # failure warns but does not fail the reset (the data wipe already succeeded).
    if "config_settings" in targets and not args.no_seed:
        try:
            from sqlalchemy.orm import sessionmaker

            from brave.config.runtime import seed_default_config

            with sessionmaker(bind=engine)() as session:
                inserted = seed_default_config(session)
                session.commit()
            print(f"\nconfig_settings re-seeded: {inserted} default row(s) inserted.")
        except Exception as exc:  # noqa: BLE001 — best-effort; never fail the reset
            print(
                f"\nWARN: config_settings re-seed skipped ({type(exc).__name__}: {exc}). "
                "Run manually: set -a; source .env; set +a; "
                ".venv/bin/python -m scripts.seed_config",
                file=sys.stderr,
            )

    # Redis flush (scoped to the pattern — never FLUSHALL).
    if not args.no_redis:
        try:
            import redis as _redis
        except ImportError:
            print("\nWARN: redis client not importable — skipping Redis flush.", file=sys.stderr)
            return 0
        r = _redis.from_url(redis_url)
        keys = [k for k in r.scan_iter(match=args.redis_pattern, count=500)]
        deleted = r.delete(*keys) if keys else 0
        remaining = sum(1 for _ in r.scan_iter(match=args.redis_pattern, count=500))
        print(f"\nRedis flush ({args.redis_pattern}): {len(keys)} found, {deleted} deleted, {remaining} remaining.")

        # Celery broker queue purge — scoped to the broker keys only.
        # Safety: only deletes "celery" (the task queue list) and "_kombu*"
        # (Kombu binding/unacked metadata). Never FLUSHALL, never brave:* again.
        # Gate: skip when --no-redis is set (already skipped above) OR when
        # --no-broker-purge is set explicitly.
        if not args.no_broker_purge:
            celery_keys = list(r.scan_iter(match="celery", count=100))
            kombu_keys = list(r.scan_iter(match="_kombu*", count=100))
            n_celery = r.delete(*celery_keys) if celery_keys else 0
            n_kombu = r.delete(*kombu_keys) if kombu_keys else 0
            print(
                f"Celery broker purge (celery + _kombu*): "
                f"{n_celery} task(s), {n_kombu} kombu key(s) deleted."
            )
            print("  Stale queued tasks cleared — restart workers to begin fresh.")

    print("\nDone — base zerada (schema + alembic_version intact).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
