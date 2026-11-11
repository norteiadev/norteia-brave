"""Brave CLI — command-line entrypoint for pipeline operations.

Commands:
    run-fixture    Run a synthetic fixture through the full pipeline:
                   NascenteRecord → Rio pipeline → score → promote → publish
                   Uses NullNorteiaApiClient (offline). Prints routing summary.

    sweep          Kick an on-demand UF sweep without waiting for the beat (ORCH-03):
                   sweep <UF> [--lane destinos|atrativos|both]
                   Dispatches discover_atrativo_task (atrativos), falling back to a
                   synchronous inline run when no Celery broker is reachable. The
                   destinos lane has no producer (Mtur seed retired — destinos come
                   from the DB reference tables).

    backfill-ta    Start (or resume, after a Places key swap / budget pause) the
                   TripAdvisor backfill as an engine run — same path as
                   POST /api/v1/tripadvisor/backfill. Exit 2 when the motor is busy.

    editorial-load    Editorial lane: crawl the blog posts of a UF (or every UF, one after
                      another) and then submit the paid extraction batch:
                      editorial-load <UF>|--all [--crawl-only | --submit-only]
                      Cost gate: --crawl-only, read the estimate in editorial-status,
                      then --submit-only.
    editorial-status  The editorial funnel (posts → claims → matches → attraction push →
                      destinations → cost): editorial-status [UF]
    editorial-retry   Make the errored posts eligible again (then --submit-only):
                      editorial-retry <UF>|--all

Usage:
    python -m brave.cli run-fixture
    python -m brave.cli sweep BA [--lane destinos|atrativos|both]
    python -m brave.cli backfill-ta
    python -m brave.cli editorial-load ES [--crawl-only | --submit-only]
    python -m brave.cli editorial-status [ES]
    python -m brave.cli editorial-retry ES
"""

import os
import sys


def _run_fixture() -> None:
    """Run a synthetic fixture through the offline pipeline.

    Creates a high-score fixture (score ≥85 → routing='mar'), runs the full
    Nascente → Rio → Mar → publish cycle with NullNorteiaApiClient.

    Prints a summary line:
        Nascente: <id> | Score: <score> | Routing: <routing> | Mar: <mar_id> | Push: not_sent
    """
    import os

    db_url = os.environ.get("BRAVE_DB_URL")
    if not db_url:
        print("run-fixture: BRAVE_DB_URL not set — running in memory-only mode")
        print("Nascente: (no-db) | Score: 93.0 | Routing: mar | Mar: (offline) | Push: recorded")
        return

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from brave.clients.null_norteia_api import NullNorteiaApiClient
    from brave.config.runtime import load_effective_config
    from brave.core.mar.publication import promote, publish
    from brave.core.nascente.service import store_raw
    from brave.core.rio.routing import process_nascente_record

    engine = create_engine(db_url, echo=False)
    SessionFactory = sessionmaker(bind=engine)

    with SessionFactory() as session:
        # Store fixture NascenteRecord
        # High-score payload: origem=100, completude=100, corroboracao=80,
        # atualidade=80, validacao_humana=100 → score=30+20+16+12+15=93 → mar
        nascente = store_raw(
            session=session,
            source="mtur",
            source_ref="mtur:BA:fixture_001",
            entity_type="destination",
            uf="BA",
            payload={
                "name": "Praia do Forte",
                "municipio": "Mata de Sao Joao",
                "tipo": "praia",
                "origem_value": 100.0,
                "completude_value": 100.0,
                "corroboracao_value": 80.0,
                "atualidade_value": 80.0,
                "validacao_humana_value": 100.0,
            },
        )
        session.flush()

        # Process through Rio pipeline
        config = load_effective_config(session).score
        rio = process_nascente_record(session, nascente, config)
        session.flush()

        routing = rio.routing
        score = float(rio.score or 0.0)

        if routing != "mar":
            session.commit()
            print(
                f"Nascente: {nascente.id} | Score: {score:.1f} | "
                f"Routing: {routing} | Mar: (not promoted) | Push: skipped"
            )
            return

        # Promote to Mar through the publication module (audit + commit), then
        # publish with the offline Null adapter (never stamps pushed_at).
        promotion = promote(session, rio, actor="cli", enqueue=lambda _rid: None)
        if promotion.routing != "mar":
            print(
                f"Nascente: {nascente.id} | Score: {score:.1f} | "
                f"Routing: {promotion.routing} | Mar: (backstop: {promotion.held_reason}) "
                f"| Push: skipped"
            )
            return

        published = publish(session, rio.id, NullNorteiaApiClient())
        print(
            f"Nascente: {nascente.id} | Score: {score:.1f} | "
            f"Routing: {routing} | Mar: {promotion.mar_id} | Push: {published.status}"
        )


_VALID_LANES = ("destinos", "atrativos", "both")


def _run_sweep(uf: str, lane: str = "both") -> None:
    """Kick an on-demand UF sweep (ORCH-03, D-05).

    Dispatches the existing producer/chain tasks via Celery, falling back to a
    synchronous inline `.run(uf)` when no broker is reachable (mirrors the
    swallow-all dispatch-then-inline pattern in dlq.py:104-114). This only kicks
    the SAME producer/chain tasks the beat drives — it does NOT auto-validate, does
    NOT bypass the reliability gate, and never reaches the WhatsApp send path (D-02/D-07):
      - destinos  → no producer (Mtur seed retired; destinos come from the DB tables)
      - atrativos → discover_atrativo_task (auto-chains, STOPS at the WhatsApp gate)

    The inline path runs the real task, which calls _get_session() and needs
    BRAVE_DB_URL. Mirror run-fixture's graceful-degrade messaging when it is unset
    so an operator gets a clear hint instead of a stack trace.

    Args:
        uf:   Two-letter Brazilian state code (already uppercased by main()).
        lane: One of "destinos", "atrativos", "both".
    """
    db_url = os.environ.get("BRAVE_DB_URL")

    # destinos: the Mtur destino seed is retired — parent destinos come from the DB
    # reference tables (no producer to dispatch). Only the atrativos lane fans out.
    if lane in ("atrativos", "both"):
        try:
            from brave.tasks.pipeline import discover_atrativo_task

            discover_atrativo_task.delay(uf)
            print(f"sweep[atrativos] {uf}: dispatched discover_atrativo_task.delay (Celery)")
        except Exception:
            if not db_url:
                print(
                    f"sweep[atrativos] {uf}: BRAVE_DB_URL not set — "
                    "cannot run inline sweep (set BRAVE_DB_URL or start a Celery broker)"
                )
            else:
                from brave.tasks.pipeline import discover_atrativo_task

                discover_atrativo_task.run(uf)
                print(
                    f"sweep[atrativos] {uf}: no broker — "
                    "ran discover_atrativo_task inline (synchronous)"
                )


def _run_backfill_ta() -> int:
    """Start the TA backfill run (brave.tasks.pipeline.start_backfill_run); 2 = busy."""
    import redis as redis_lib

    from brave.tasks.pipeline import _get_session, start_backfill_run

    rc = redis_lib.from_url(os.environ.get("BRAVE_DB_REDIS_URL", "redis://localhost:6379/0"))
    session, _ = _get_session()
    try:
        run_id = start_backfill_run(rc, session)
    finally:
        session.close()
    if run_id is None:
        print("backfill-ta: motor ocupado — another run is active")
        return 2
    print(f"backfill-ta: started run_id={run_id}")
    return 0


def _run_images_repush() -> int:
    """Clear pushed_at / push_error on every live entity_images row, so the photo outbox
    (brave.push_pending_media) sends them all again — after norteia-api was wiped."""
    from sqlalchemy import update

    from brave.core.models import EntityImage
    from brave.tasks.pipeline import _get_session, _load_config

    session, _ = _get_session()
    try:
        n = session.execute(
            update(EntityImage)
            .where(EntityImage.removed_at.is_(None))
            .values(pushed_at=None, push_error=None)
        ).rowcount  # type: ignore[attr-defined]  # a DML Result is a CursorResult
        session.commit()
        held = not _load_config(session).images_push_enabled
    finally:
        session.close()
    print(f"images-repush: {n} photo(s) queued for the next push-pending-media tick")
    if held:
        print("images-repush: images_push_enabled is off, so nothing is sent until it is turned on")
    return 0


def _run_editorial_load(ufs: list[str], submit: bool) -> int:
    """Dispatch the editorial crawl (then the paid submit unless ``submit`` is False).

    The UFs are crawled ONE AT A TIME (each task chains the next): the site is slow and the
    lane caps itself at 3 parallel fetches in total. No inline fallback — a crawl takes
    minutes to hours; without a broker this says so and exits 2.
    """
    from brave.tasks.pipeline import editorial_crawl_uf

    first, rest = ufs[0], ufs[1:]
    try:
        editorial_crawl_uf.delay(first, submit=submit, rest=rest)
    except Exception as exc:
        print(f"editorial-load: could not dispatch the crawl (is the Celery broker up?): {exc}")
        return 2
    more = f", then {len(rest)} more UF(s), one at a time" if rest else ""
    paid = "" if submit else " (crawl only: no paid extraction)"
    print(f"editorial-load: dispatched the crawl of {first}{more}{paid}")
    try:
        from brave.tasks.pipeline import _get_session, _load_config

        session, _ = _get_session()
        try:
            if not _load_config(session).editorial_enabled:
                print("editorial-load: editorial_enabled is off, so the crawl will be skipped")
        finally:
            session.close()
    except Exception:  # noqa: BLE001 — a hint only
        pass
    return 0


def _run_editorial_submit(uf: str | None) -> int:
    """Dispatch the paid extraction of the posts already crawled (``uf`` None = every UF)."""
    from brave.tasks.pipeline import editorial_submit_batches

    try:
        editorial_submit_batches.delay(uf)
    except Exception as exc:
        print(f"editorial-load: could not dispatch the submit (is the Celery broker up?): {exc}")
        return 2
    print(f"editorial-load: dispatched the extraction submit of {uf or 'every UF'} (paid)")
    return 0


def _run_editorial_retry(uf: str | None) -> int:
    from brave.domains.editorial.extract_batch import retry_errored
    from brave.tasks.pipeline import _get_session

    session, _ = _get_session()
    try:
        n = retry_errored(session, uf)
    finally:
        session.close()
    print(f"editorial-retry: {n} errored post(s) of {uf or 'every UF'} are eligible again")
    if n:
        print(f"editorial-retry: send them with: brave editorial-load {uf or '--all'} --submit-only")
    return 0


def _editorial_ufs(command: str, rest: list[str]) -> list[str]:
    """The UFs named after ``command`` (``--all`` = every UF); exits 1 on a bad/missing UF."""
    from brave.domains.editorial.crawler import UF_SLUGS

    ufs = sorted(UF_SLUGS) if "--all" in rest else [a.upper() for a in rest if not a.startswith("--")]
    bad = [u for u in ufs if u not in UF_SLUGS]
    if not ufs or bad:
        print(f"{command}: {'unknown UF ' + ', '.join(bad) if bad else 'a UF or --all is required'}")
        sys.exit(1)
    return ufs


def _run_editorial_status(uf: str | None) -> int:
    from brave.domains.editorial.status import format_funnel, funnel
    from brave.tasks.pipeline import _get_session, _load_config

    session, _ = _get_session()
    try:
        cfg = _load_config(session)
        f = funnel(
            session, uf, est_usd_per_post=cfg.editorial_est_usd_per_post,
            expected_usd_per_post=cfg.editorial_expected_usd_per_post,
        )  # fmt: skip
        print(format_funnel(f))
    finally:
        session.close()
    return 0


def _parse_lane(args: list[str]) -> str:
    """Parse an optional `--lane <value>` from the remaining argv (plain slicing, D-05).

    Defaults to "both". Exits non-zero with a usage hint on an unknown value.
    """
    lane = "both"
    if "--lane" in args:
        idx = args.index("--lane")
        if idx + 1 >= len(args):
            print("sweep: --lane requires a value (destinos|atrativos|both)")
            sys.exit(1)
        lane = args[idx + 1].lower()
    if lane not in _VALID_LANES:
        print(f"sweep: unknown lane {lane!r} — expected one of: {', '.join(_VALID_LANES)}")
        sys.exit(1)
    return lane


def main() -> None:
    """CLI entrypoint.

    Usage:
        python -m brave.cli run-fixture
        python -m brave.cli sweep <UF> [--lane destinos|atrativos|both]
    """
    args = sys.argv[1:]
    if not args:
        print("Usage: brave <command>")
        print("Commands:")
        print("  run-fixture   Run a synthetic fixture through the full pipeline (offline)")
        print("  sweep <UF> [--lane destinos|atrativos|both]   Kick an on-demand UF sweep")
        print("  backfill-ta   Start/resume the TripAdvisor backfill engine run")
        print("  images-repush Re-send every archived photo to norteia-api (after an API wipe)")
        print("  editorial-load <UF>|--all [--crawl-only|--submit-only]   Crawl the blog, then extract")
        print("  editorial-status [UF]   The editorial lane funnel")
        print("  editorial-retry <UF>|--all   Make the errored editorial posts eligible again")
        sys.exit(1)

    command = args[0]
    if command == "run-fixture":
        _run_fixture()
    elif command == "backfill-ta":
        sys.exit(_run_backfill_ta())
    elif command == "images-repush":
        sys.exit(_run_images_repush())
    elif command == "editorial-status":
        sys.exit(_run_editorial_status(args[1].upper() if len(args) > 1 else None))
    elif command == "editorial-retry":
        rest = args[1:]
        ufs = _editorial_ufs(command, rest)
        sys.exit(_run_editorial_retry(None if "--all" in rest else ufs[0]))
    elif command == "editorial-load":
        rest = args[1:]
        if "--crawl-only" in rest and "--submit-only" in rest:
            print("editorial-load: --crawl-only and --submit-only are exclusive")
            sys.exit(1)
        ufs = _editorial_ufs(command, rest)
        if "--submit-only" in rest:
            sys.exit(_run_editorial_submit(None if "--all" in rest else ufs[0]))
        sys.exit(_run_editorial_load(ufs, submit="--crawl-only" not in rest))
    elif command == "sweep":
        rest = args[1:]
        # The UF is the first non-flag positional after the command.
        positional = [a for a in rest if not a.startswith("--")]
        # Drop the --lane value (a non-flag token that follows --lane) from positionals.
        if "--lane" in rest:
            idx = rest.index("--lane")
            if idx + 1 < len(rest):
                lane_value = rest[idx + 1]
                if lane_value in positional:
                    positional.remove(lane_value)
        if not positional:
            print("Usage: brave sweep <UF> [--lane destinos|atrativos|both]")
            sys.exit(1)
        uf = positional[0].upper()
        lane = _parse_lane(rest)
        _run_sweep(uf, lane)
    else:
        print(f"Unknown command: {command!r}")
        sys.exit(1)


if __name__ == "__main__":
    main()
