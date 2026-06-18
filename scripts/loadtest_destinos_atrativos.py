"""Real end-to-end load-test harness: ingest destinos → promote 10 to Mar → targeted atrativos discovery.

Drives the full real flow against a live DB + Google Places + DeepSeek LLM.
Acceptance bar: 10 Mar destination records + ≥10 Rio attraction records per parent_mar_id.

Prerequisites:
  BRAVE_DB_URL         — PostgreSQL connection string
  BRAVE_PLACES_API_KEY — Google Places API (New) key
  BRAVE_LLM_OPENROUTER_API_KEY — OpenRouter key for DeepSeek extraction
  RUN_REAL_EXTERNALS=true — enables real client construction
  BRAVE_LLM_USD_DAILY_BUDGET=10.0 (default) — raise to 50.0 if CostGuardError fires

Usage:
  set -a; source .env; set +a
  .venv/bin/python -m scripts.loadtest_destinos_atrativos BA
  # defaults to BA if no UF given

WARNING: This script writes to the real database.
For a clean baseline run TRUNCATE manually first:
  TRUNCATE nascente_records, rio_records, mar_records, consent_log CASCADE;
"""

from __future__ import annotations

import asyncio
import os
import sys

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from brave.clients.llm import RealLLMClient
from brave.clients.mtur import MturClient
from brave.clients.places import RealPlacesClient, build_mtur_ibge_lookup
from brave.config.settings import LLMConfig, ScoreConfig
from brave.core.dlq.service import validate_and_promote_rio
from brave.core.models import MarRecord, RioRecord
from brave.lanes.atrativos.discovery_agent import DiscoveryAgent
from brave.lanes.destinos.mtur import MturSeedIngest

# All Brazilian UFs — used to build the full ibge_lookup from Mtur data
_ALL_UFS = [
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA",
    "MT", "MS", "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN",
    "RS", "RO", "RR", "SC", "SP", "SE", "TO",
]


def main() -> None:
    ufs = [a.upper() for a in sys.argv[1:]] or ["BA"]
    target_destinos = int(os.environ.get("LOAD_TARGET_DESTINOS", "10"))
    target_atrativos = int(os.environ.get("ATRATIVO_TARGET_COUNT", "10"))

    db_url = os.environ.get("BRAVE_DB_URL")
    if not db_url:
        print("ERROR: BRAVE_DB_URL not set. Run: set -a; source .env; set +a")
        sys.exit(1)

    places_key = os.environ.get("BRAVE_PLACES_API_KEY")
    if not places_key:
        print("ERROR: BRAVE_PLACES_API_KEY not set. Export this key before running.")
        sys.exit(1)

    llm_key = os.environ.get("BRAVE_LLM_OPENROUTER_API_KEY")
    if not llm_key:
        print("ERROR: BRAVE_LLM_OPENROUTER_API_KEY not set. Export this key before running.")
        sys.exit(1)

    # Must be set BEFORE constructing RealPlacesClient / RealLLMClient
    os.environ["RUN_REAL_EXTERNALS"] = "true"

    print(
        "NOTE: Default BRAVE_LLM_USD_DAILY_BUDGET=10.0 "
        f"(~$0.02 for {target_destinos} destinos × {target_atrativos} atrativos "
        "DeepSeek extractions)."
    )
    print(
        "      If CostGuardError fires, set BRAVE_LLM_USD_DAILY_BUDGET=50.0 and re-run."
    )
    print()

    mtur_client = MturClient()
    llm_config = LLMConfig()
    score_config = ScoreConfig()
    engine = create_engine(db_url, echo=False)
    SessionFactory = sessionmaker(bind=engine)

    # Build ibge_lookup from all UFs for name→IBGE resolution (best-effort)
    print("Building IBGE lookup from Mtur data (all UFs)...")
    all_mtur_rows: list[dict] = []
    for uf in _ALL_UFS:
        try:
            all_mtur_rows.extend(asyncio.run(mtur_client.fetch_municipalities(uf)))
        except Exception:
            pass  # best-effort; sample CSV only has a subset of UFs
    ibge_lookup = build_mtur_ibge_lookup(all_mtur_rows)
    print(f"IBGE lookup built: {len(ibge_lookup)} entries\n")

    with SessionFactory() as session:
        # ----------------------------------------------------------------
        # Step 0 — DB non-clean warning
        # ----------------------------------------------------------------
        existing_mar = session.scalar(
            select(func.count(MarRecord.id)).where(
                MarRecord.entity_type == "destination",
                MarRecord.superseded_by_id.is_(None),
            )
        )
        if existing_mar and existing_mar > 0:
            print(f"WARNING: {existing_mar} active Mar destinos already exist.")
            print(
                "For a clean baseline run TRUNCATE manually before re-running:\n"
                "  TRUNCATE nascente_records, rio_records, mar_records, consent_log CASCADE;\n"
            )

        # ----------------------------------------------------------------
        # Step 1 — Ingest destinos via MturSeedIngest
        # ----------------------------------------------------------------
        print("=== STEP 1: Ingest destinos ===")
        for uf in ufs:
            asyncio.run(MturSeedIngest(mtur_client, session, score_config).produce(uf))
            session.commit()
            print(f"[{uf}] Step 1: destinos ingested")

        # Routing summary for ingested destinos
        ingest_rows = session.execute(
            select(RioRecord.uf, RioRecord.routing, func.count(RioRecord.id))
            .where(RioRecord.entity_type == "destination", RioRecord.uf.in_(ufs))
            .group_by(RioRecord.uf, RioRecord.routing)
            .order_by(RioRecord.uf, RioRecord.routing)
        ).all()
        print("\nDestino routing summary after ingest:")
        for uf_row, routing, n in ingest_rows:
            print(f"  {uf_row}  {routing:<12} {n}")
        print()

        # ----------------------------------------------------------------
        # Step 2 — Promote up to target_destinos DLQ destinos to Mar
        # ----------------------------------------------------------------
        print(f"=== STEP 2: Promote up to {target_destinos} DLQ destinos to Mar ===")
        promoted: list[MarRecord] = []
        for uf in ufs:
            if len(promoted) >= target_destinos:
                break
            remaining = target_destinos - len(promoted)
            dlq_rows = list(
                session.scalars(
                    select(RioRecord)
                    .where(
                        RioRecord.routing == "dlq",
                        RioRecord.entity_type == "destination",
                        RioRecord.uf == uf,
                    )
                    .limit(remaining)
                ).all()
            )
            for rio in dlq_rows:
                if len(promoted) >= target_destinos:
                    break
                mar = validate_and_promote_rio(session, rio, score_config)
                if mar:
                    promoted.append(mar)
            session.commit()

        print(f"Step 2: {len(promoted)} destinos promoted to Mar\n")

        # ----------------------------------------------------------------
        # Step 3 — Targeted atrativos discovery for each promoted Mar destino
        # ----------------------------------------------------------------
        print(f"=== STEP 3: Targeted atrativos discovery ({target_atrativos} per destino) ===")
        places_client = RealPlacesClient(api_key=places_key, ibge_lookup=ibge_lookup)
        llm_client = RealLLMClient(config=llm_config)

        for mar in promoted:
            agent = DiscoveryAgent(places_client, llm_client, session, score_config)
            count = asyncio.run(
                agent.produce_for_destino(mar, target_count=target_atrativos)
            )
            session.commit()
            canonical = mar.canonical or {}
            nome = (
                canonical.get("municipio")
                or canonical.get("name")
                or str(mar.id)
            )
            print(f"  [{nome}] atrativos created in Rio: {count}")

        print()

        # ----------------------------------------------------------------
        # Step 4 — Summary
        # ----------------------------------------------------------------
        print("=== LOAD TEST SUMMARY ===")

        mar_count = session.scalar(
            select(func.count(MarRecord.id)).where(
                MarRecord.entity_type == "destination",
                MarRecord.superseded_by_id.is_(None),
            )
        )
        print(f"Mar destination records (active): {mar_count}")

        atr_rows = session.execute(
            select(RioRecord.parent_mar_id, func.count(RioRecord.id))
            .where(RioRecord.entity_type == "attraction")
            .group_by(RioRecord.parent_mar_id)
            .order_by(RioRecord.parent_mar_id)
        ).all()

        print("Attraction Rio records by parent_mar_id:")
        for parent_id, cnt in atr_rows:
            status = "OK" if cnt >= target_atrativos else "FAIL"
            print(f"  {parent_id}: {cnt} atrativos [{status}]")

        parents_ok = sum(1 for _, cnt in atr_rows if cnt >= target_atrativos)

        print(
            f"\nAcceptance: {mar_count} Mar destinos, "
            f"{parents_ok}/{len(atr_rows)} parents with >={target_atrativos} atrativos"
        )

        # Routing summary (mirrors ingest_destinos.py)
        routing_rows = session.execute(
            select(RioRecord.uf, RioRecord.routing, func.count(RioRecord.id))
            .where(RioRecord.entity_type == "destination", RioRecord.uf.in_(ufs))
            .group_by(RioRecord.uf, RioRecord.routing)
            .order_by(RioRecord.uf, RioRecord.routing)
        ).all()

    print("\nrouting summary (entity_type=destination):")
    for uf_row, routing, n in routing_rows:
        print(f"  {uf_row}  {routing:<12} {n}")

    if mar_count is not None and mar_count >= target_destinos and parents_ok >= target_destinos:
        print("\nACCEPTANCE: PASS")
    else:
        print("\nACCEPTANCE: FAIL — check logs for quarantine reasons")


if __name__ == "__main__":
    main()
