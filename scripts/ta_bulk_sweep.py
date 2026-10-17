#!/usr/bin/env python3
"""TripAdvisor bulk national sweep trigger (Phase 15, TA-12).

Thin operator entrypoint for the all-Brazil (geoId 294280) bulk pagination run.
Dispatches `brave.sweep_tripadvisor` in its `bulk_national=True` branch, which
paginates the AttractionsFusion listing through `produce_paginated`, bulk-ingests
each page into Nascente (parent-less), commits per page, writes the live progress
hash, and fails fast (needs_bootstrap) on a mid-run 403/429.

Slice-first (CONTEXT, LOCKED): validate a SMALL page range end-to-end first, then
scale to the full 334 pages — the slice and the full run share ONE code path,
parameterized by --start-page / --max-pages. The full national run is a later
TRIGGER (a larger --max-pages), not new code.

Resume: a re-run after a mid-run stop continues from the stored next_page cursor
automatically (read from the sweep_progress hash) — --start-page only seeds a FRESH
run, which is what follows a finished (done) run or --fresh.

Engine run: the bulk gets its OWN engine run (brave.core.engine.start, source
tripadvisor, one unit per page) — the motor goes LIGADO (a seeded DESLIGADO no longer
stops it at page 1), the Painel shows it and its Stop works, and no other run can end
mid-bulk and switch the motor off under it. A busy motor → exit 2. Inline mode exits 1
unless the sweep ended ``done``. When done, the sweep chains into the backfill
(brave.backfill_ta_atrativos, ta_backfill_enabled) inside the same run.

  # offline default — runs the small slice inline (RUN_REAL_EXTERNALS unset → Null client, no network)
  python scripts/ta_bulk_sweep.py --start-page 1 --max-pages 5

  # enqueue on the Celery worker instead of running inline
  python scripts/ta_bulk_sweep.py --max-pages 5 --enqueue

SECURITY (T-15-07-01 / T-12-04-01): this script logs ONLY page range / counts /
error-class — never cookies, session, datadome, user-agent, or proxy values.
Those live exclusively in the worker's `brave:ta:session` Redis key.
"""

from __future__ import annotations

import argparse
import sys

# All-Brazil geoId + full-run page count (CONTEXT, LOCKED).
NATIONAL_GEO_ID = 294280
FULL_PAGES = 334
# Slice-first default cap (small page range, ~150 attractions) — keeps the default
# invocation a safe validation slice, never the full 3h national run.
DEFAULT_MAX_PAGES = 5


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ta_bulk_sweep.py",
        description=(
            "Trigger the TripAdvisor bulk national sweep (geoId 294280) via "
            "brave.sweep_tripadvisor(bulk_national=True). Slice-first: pass a small "
            "--max-pages to validate end-to-end, then scale to the full 334 pages."
        ),
    )
    parser.add_argument(
        "--start-page",
        type=int,
        default=1,
        metavar="N",
        help=(
            "1-based page to start a FRESH run at (offset = (N-1)*30). Page 1 = oa0, "
            "page 2 = oa30. Ignored while an unfinished run is stored (it resumes at its "
            "next_page) unless --fresh. Default: 1."
        ),
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=DEFAULT_MAX_PAGES,
        metavar="N",
        help=(
            f"Cap on pages to fetch this run (slice-first). Pass {FULL_PAGES} for the "
            f"full national run. Default: {DEFAULT_MAX_PAGES} (a small validation slice)."
        ),
    )
    parser.add_argument(
        "--geo-id",
        type=int,
        default=NATIONAL_GEO_ID,
        metavar="GEOID",
        help=f"TripAdvisor integer geoId (default: {NATIONAL_GEO_ID} = all Brazil).",
    )
    parser.add_argument(
        "--depth",
        default=None,
        metavar="DEPTH",
        help=(
            "Pipeline depth (nascente|nascente_rio|nascente_rio_mar). Omit for the "
            "full pipeline path. depth=nascente runs Nascente + reliability scoring only (no Rio)."
        ),
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Ignore a stored unfinished run and start at --start-page.",
    )
    parser.add_argument(
        "--enqueue",
        action="store_true",
        help=(
            "Enqueue the sweep on the Celery worker (.delay) instead of running it "
            "inline in this process. Default: run inline."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.start_page < 1:
        print("error: --start-page must be >= 1", file=sys.stderr)
        return 2
    if args.max_pages < 1:
        print("error: --max-pages must be >= 1", file=sys.stderr)
        return 2

    # Import lazily so `--help` works without loading Celery/DB machinery.
    import os  # noqa: PLC0415

    import redis as redis_lib  # noqa: PLC0415

    from brave.config.runtime import enabled_sources, load_effective_config  # noqa: PLC0415
    from brave.core import engine as collection_engine  # noqa: PLC0415
    from brave.domains.tripadvisor import sweep_progress  # noqa: PLC0415
    from brave.tasks.pipeline import _get_session, sweep_tripadvisor  # noqa: PLC0415

    # uf is unused by the bulk branch (it derives UF per-attraction from the geocode);
    # pass a national placeholder so the task's logging/quarantine payloads are well-formed.
    uf = "BR"

    # Log only the page range / geo_id / mode — never any session material.
    print(
        f"ta_bulk_sweep: geo_id={args.geo_id} start_page={args.start_page} "
        f"max_pages={args.max_pages} depth={args.depth} fresh={args.fresh} "
        f"mode={'enqueue' if args.enqueue else 'inline'}"
    )

    rc = redis_lib.from_url(os.environ.get("BRAVE_DB_REDIS_URL", "redis://localhost:6379/0"))
    session, _ = _get_session()
    try:
        run_id = collection_engine.start(
            rc,
            session,
            action="sweep",
            depth=args.depth or collection_engine.NASCENTE_RIO_MAR,
            source="tripadvisor",
            ufs=[uf],
            lane="tripadvisor_bulk",
            valid_sources=enabled_sources(load_effective_config(session)),
            units_total=args.max_pages,
        )
        if run_id is None:
            print("ta_bulk_sweep: motor ocupado — another run is active", file=sys.stderr)
            return 2
        claimed = collection_engine.claim_producer(rc, run_id)
        task_kwargs = dict(
            bulk_national=True,
            start_page=args.start_page,
            max_pages=args.max_pages,
            geo_id=args.geo_id,
            run_id=claimed,
            fresh=args.fresh,
        )

        if args.enqueue:
            try:
                result = sweep_tripadvisor.delay(uf, args.depth, **task_kwargs)
            except Exception as exc:
                collection_engine.abort(rc, session, run_id)
                print(f"ta_bulk_sweep: enqueue failed ({type(exc).__name__})", file=sys.stderr)
                return 1
            collection_engine.dispatch_finished(rc, session, run_id)
            print(f"ta_bulk_sweep: enqueued task id={result.id} run_id={run_id}")
            return 0

        # Inline: invoke the raw task function (bind=True → __wrapped__.__func__).
        raw_fn = sweep_tripadvisor.__wrapped__.__func__
        try:
            raw_fn(sweep_tripadvisor, uf, args.depth, **task_kwargs)
        finally:
            collection_engine.dispatch_finished(rc, session, run_id)
    finally:
        session.close()

    state = sweep_progress.get_progress(rc)["state"]
    print(
        f"ta_bulk_sweep: inline run ended state={state} "
        f"mode={collection_engine.get_mode(rc)} run_id={run_id}"
    )
    return 0 if state == sweep_progress.DONE else 1


if __name__ == "__main__":
    raise SystemExit(main())
