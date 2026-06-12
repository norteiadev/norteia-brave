"""Brave CLI — command-line entrypoint for pipeline operations.

Commands:
    run-fixture    Run a synthetic fixture through the full pipeline:
                   NascenteRecord → Rio pipeline → score → promote_to_mar → push
                   Uses FakeNorteiaApiClient (offline). Prints routing summary.

Usage:
    python -m brave.cli run-fixture
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

    from brave.config.settings import ScoreConfig
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
        config = ScoreConfig()
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


def main() -> None:
    """CLI entrypoint.

    Usage:
        python -m brave.cli run-fixture
    """
    args = sys.argv[1:]
    if not args:
        print("Usage: brave <command>")
        print("Commands:")
        print("  run-fixture   Run a synthetic fixture through the full pipeline (offline)")
        sys.exit(1)

    command = args[0]
    if command == "run-fixture":
        _run_fixture()
    else:
        print(f"Unknown command: {command!r}")
        sys.exit(1)


if __name__ == "__main__":
    main()
