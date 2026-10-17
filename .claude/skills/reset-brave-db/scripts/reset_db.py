#!/usr/bin/env python3
"""Reset the norteia-brave Postgres + Redis to a clean "carga inicial" state.

Truncates every data table in the Brave Postgres — keeping the SCHEMA and the
`alembic_version` row intact — and flushes the engine/cache `brave:*` keys from
Redis. By default, also purges the Celery broker (the `celery` task list, the
`unacked*` in-flight bookkeeping and `_kombu*` bindings) so stale queued tasks
don't re-fire after a reset and hit reset-away rio_ids.

The Redis flush PRESERVES (see PRESERVED_KEYS / PRESERVED_PREFIXES): the redbeat
schedule + leader lock (redbeat_key_prefix="brave" puts them under brave:*), the
TripAdvisor session, the daily cost-guard spend (--reset-cost to zero it), each Google
Places key's credit counter (--reset-places-credit to zero it) and the geocode caches
(--flush-geo-cache to drop them).

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
  --reset-cost        also zero today's cost-guard spend (brave:cost:* daily) — a second budget
  --reset-places-credit  also zero the Places keys' credit counters (brave:cost:places:total:*)
  --flush-geo-cache   also drop the geocode caches (brave:geo:*, brave:ta:geo:*)
  --discard-inflight-batches  wipe even if Anthropic description batches are in flight
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

# Static carga-inicial reference tables. These are NOT pipeline data — the collection
# lanes read their parent destinos / geo resolution from them, and nothing
# FK-references them. A reset preserves them so a re-seed is not needed after every
# wipe. The first three are seeded at migrate time by scripts.seed_reference_data;
# local_businesses is filled on demand by scripts.cadastur_import (an MTur quarterly
# register — re-downloading ~500k rows after every wipe would be absurd).
# atrativo_buscas holds PAID Parallel searches (docs/poc/gemini-viability.md §29), keyed by
# canonical_key with no FK: descriptions are regenerated from it, so a wipe must not re-bill.
REFERENCE_TABLES = {"municipios", "distritos", "uf_geoids", "local_businesses", "atrativo_buscas"}

# brave:* keys that are NOT pipeline data and survive the Redis flush.
# - redbeat (celery_app.py redbeat_key_prefix="brave"): deleting brave:lock makes the
#   running beat's next lock.extend raise LockNotOwnedError and beat dies (no restart
#   host-native); deleting brave:schedule/statics silently drops every periodic entry
#   until beat restarts.
# - TA session: an operator-pasted credential; a data reset must not force a re-login.
PRESERVED_KEYS = {
    "brave:schedule", "brave:statics", "brave:lock",
    "brave:ta:session", "brave:ta:needs_bootstrap", "brave:ta:keepalive_failures",
}
# Money / paid-or-slow caches — kept unless the matching flag asks otherwise.
COST_PREFIX = "brave:cost:"  # cost_guard daily spend: wiping it grants a 2nd budget same UTC day
# Each Places key's credit spent so far (no TTL): wiping it lets an exhausted key spend its
# whole credit again — never part of --reset-cost, only its own explicit flag.
PLACES_CREDIT_PREFIX = "brave:cost:places:total:"
GEO_CACHE_PREFIXES = ("brave:geo:", "brave:ta:geo:")  # 30d Nominatim (1.1s/call) + TA geo cache

# Kombu redis transport keys for acks_late messages a worker has reserved: purging only
# `celery` leaves them, and kombu restores them after visibility_timeout against the
# wiped base.
BROKER_KEYS = ("celery", "unacked", "unacked_index", "unacked_mutex")


def _is_preserved(key: str, *, reset_cost: bool, flush_geo_cache: bool,
                  reset_places_credit: bool = False) -> bool:
    if key in PRESERVED_KEYS:
        return True
    if key.startswith(PLACES_CREDIT_PREFIX):
        return not reset_places_credit
    if not reset_cost and key.startswith(COST_PREFIX):
        return True
    return not flush_geo_cache and key.startswith(GEO_CACHE_PREFIXES)


def flush_brave_keys(r, pattern: str, *, reset_cost: bool = False,
                     flush_geo_cache: bool = False,
                     reset_places_credit: bool = False) -> tuple[int, int]:
    """Delete keys matching ``pattern`` except the preserved ones. Returns (deleted, kept)."""
    found = [k.decode() if isinstance(k, bytes) else k for k in r.scan_iter(match=pattern, count=500)]
    doomed = [k for k in found
              if not _is_preserved(k, reset_cost=reset_cost, flush_geo_cache=flush_geo_cache,
                                   reset_places_credit=reset_places_credit)]
    deleted = r.delete(*doomed) if doomed else 0
    return deleted, len(found) - len(doomed)


def purge_broker(r) -> tuple[int, int]:
    """Delete the Celery queue + unacked bookkeeping and ``_kombu*`` bindings.

    Returns (broker keys deleted, kombu keys deleted)."""
    kombu_keys = list(r.scan_iter(match="_kombu*", count=100))
    return r.delete(*BROKER_KEYS), (r.delete(*kombu_keys) if kombu_keys else 0)


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
    ap.add_argument("--reset-cost", action="store_true",
                    help="also zero today's cost-guard spend (brave:cost:* daily); default "
                         "keeps it. Never touches the Places key credit (see below)")
    ap.add_argument("--reset-places-credit", action="store_true",
                    help="also zero each Google Places key's credit counter "
                         "(brave:cost:places:total:*) — the exhausted key may spend again")
    ap.add_argument("--flush-geo-cache", action="store_true",
                    help="also drop the geocode caches (brave:geo:*, brave:ta:geo:*)")
    ap.add_argument("--discard-inflight-batches", action="store_true",
                    help="wipe even when rio_records has in-flight description batches")
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

    keep = PROTECTED_TABLES | REFERENCE_TABLES | {t.strip() for t in args.keep}
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

    # Anthropic description batches already submitted (paid) are tracked only by
    # rio_records.descricao_batch_id: truncating it means their results are never
    # collected. Refuse unless the operator explicitly accepts losing them.
    if "rio_records" in targets:
        with engine.connect() as c:
            inflight = c.execute(text(
                "select count(*) from rio_records where descricao_batch_id is not null"
            )).scalar()
        if inflight and not args.discard_inflight_batches:
            print(
                f"REFUSING: {inflight} rio_records have an in-flight description batch "
                "(descricao_batch_id). Let collect-description-batches finish, or pass "
                "--discard-inflight-batches to lose those paid results.",
                file=sys.stderr,
            )
            return 4

    # Show the plan.
    print(f"Postgres : {_mask(db_url)}")
    print(f"Redis    : {_mask(redis_url)}  (pattern {args.redis_pattern})"
          if not args.no_redis else "Redis    : (skipped)")
    print("\nWill TRUNCATE (RESTART IDENTITY CASCADE):")
    for t in targets:
        print(f"  - {t:32} {counts_before[t]} rows")
    always_preserved = PROTECTED_TABLES | REFERENCE_TABLES
    user_kept = keep - always_preserved
    if user_kept:
        print("Keeping (not truncated): " + ", ".join(sorted(user_kept)))
    print("Always preserved: " + ", ".join(sorted(always_preserved)))

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
        deleted, kept = flush_brave_keys(
            r, args.redis_pattern, reset_cost=args.reset_cost, flush_geo_cache=args.flush_geo_cache,
            reset_places_credit=args.reset_places_credit,
        )
        print(f"\nRedis flush ({args.redis_pattern}): {deleted} deleted, {kept} preserved "
              "(redbeat schedule/lock, TA session"
              + ("" if args.reset_cost else ", cost guard")
              + ("" if args.reset_places_credit else ", Places key credit")
              + ("" if args.flush_geo_cache else ", geo caches") + ").")

        # Celery broker purge — scoped to the broker keys only (BROKER_KEYS + _kombu*).
        # Never FLUSHALL, never brave:* again. Skipped with --no-redis or --no-broker-purge.
        if not args.no_broker_purge:
            n_broker, n_kombu = purge_broker(r)
            print(
                f"Celery broker purge (celery + unacked* + _kombu*): "
                f"{n_broker} broker key(s), {n_kombu} kombu key(s) deleted."
            )
            print("  Stale queued tasks cleared — restart workers to begin fresh.")

    print("\nDone — base zerada (schema + alembic_version intact).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
