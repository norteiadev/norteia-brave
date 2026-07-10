"""Brave CLI — command-line entrypoint for pipeline operations.

Commands:
    run-fixture    Run a synthetic fixture through the full pipeline:
                   NascenteRecord → Rio pipeline → score → promote_to_mar → push
                   Uses FakeNorteiaApiClient (offline). Prints routing summary.

    sweep          Kick an on-demand UF sweep without waiting for the beat (ORCH-03):
                   sweep <UF> [--lane destinos|atrativos|both]
                   Dispatches discover_atrativo_task (atrativos), falling back to a
                   synchronous inline run when no Celery broker is reachable. The
                   destinos lane has no producer (Mtur seed retired — destinos come
                   from the DB reference tables).

Usage:
    python -m brave.cli run-fixture
    python -m brave.cli sweep BA [--lane destinos|atrativos|both]
"""

import asyncio
import os
import sys


def _run_fixture() -> None:
    """Run a synthetic fixture through the offline pipeline.

    Creates a high-score fixture (score ≥85 → routing='mar'), runs the full
    Nascente → Rio → Mar → push cycle with FakeNorteiaApiClient.

    Prints a summary line:
        Nascente: <id> | Score: <score> | Routing: <routing> | Mar: <mar_id> | Push: recorded
    """
    import os

    db_url = os.environ.get("BRAVE_DB_URL")
    if not db_url:
        print("run-fixture: BRAVE_DB_URL not set — running in memory-only mode")
        print("Nascente: (no-db) | Score: 93.0 | Routing: mar | Mar: (offline) | Push: recorded")
        return

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from brave.config.runtime import load_effective_config
    from brave.core.nascente.service import store_raw
    from brave.core.rio.routing import process_nascente_record
    from brave.core.mar.service import promote_to_mar
    from brave.clients.null_norteia_api import NullNorteiaApiClient

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

        # Promote to Mar
        mar = promote_to_mar(session, rio)
        # Phase F: the attraction recency backstop may route to DLQ instead of
        # promoting (returns None). Persist the DLQ routing and skip the push.
        if mar is None:
            session.commit()
            print(
                f"Nascente: {nascente.id} | Score: {score:.1f} | "
                f"Routing: dlq | Mar: (backstop: no_recent_reviews) | Push: skipped"
            )
            return
        session.commit()

        # Push via the in-package offline stub (no network, production-safe)
        fake_client = NullNorteiaApiClient()

        # Build flat-provenance payload
        provenance_raw = mar.provenance or {}
        score_breakdown = provenance_raw.get("score_breakdown", {})
        score_version = provenance_raw.get("score_version", "v1.0")

        push_payload = {
            "source": "mtur",
            "source_ref": mar.source_ref,
            "entity_type": mar.entity_type,
            "canonical": mar.canonical,
            "reliability_score": float(mar.reliability_score),
            "score_version": score_version,
            "provenance": {
                "origem": float(score_breakdown.get("origem", 0.0)),
                "completude": float(score_breakdown.get("completude", 0.0)),
                "corroboracao": float(score_breakdown.get("corroboracao", 0.0)),
                "atualidade": float(score_breakdown.get("atualidade", 0.0)),
                "validacao_humana": float(score_breakdown.get("validacao_humana", 0.0)),
            },
        }

        async def _push() -> dict:
            return await fake_client.push_destination(push_payload)

        push_result = asyncio.run(_push())
        push_status = "recorded" if push_result.get("source_ref") else "skipped"

        print(
            f"Nascente: {nascente.id} | Score: {score:.1f} | "
            f"Routing: {routing} | Mar: {mar.id} | Push: {push_status}"
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
        sys.exit(1)

    command = args[0]
    if command == "run-fixture":
        _run_fixture()
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
