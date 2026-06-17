"""Real Destinos ingest driver (no external API, no WhatsApp).

Runs the Mtur seed lane for real against the live DB:
  bundled gov CSV (data/mtur/municipios_mtur_*.csv)
    -> MturClient.fetch_municipalities(uf)
    -> MturSeedIngest.produce(uf): store_raw -> Rio (dedup/normalize/score/route)
    -> commit

Destinos land in DLQ by default (origem=100 but validacao_humana=0), where a
steward validates them batch-by-state in the dashboard -> Mar.

Usage (env must be loaded so BRAVE_DB_URL is set):
    set -a; source .env; set +a
    .venv/bin/python -m scripts.ingest_destinos BA RJ SP
    # defaults to BA if no UF given
"""

from __future__ import annotations

import asyncio
import os
import sys

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from brave.clients.mtur import MturClient
from brave.config.settings import ScoreConfig
from brave.core.models import RioRecord
from brave.lanes.destinos.mtur import MturSeedIngest


def main() -> None:
    ufs = [a.upper() for a in sys.argv[1:]] or ["BA"]

    db_url = os.environ.get("BRAVE_DB_URL")
    if not db_url:
        print("ERROR: BRAVE_DB_URL not set. Run: set -a; source .env; set +a")
        sys.exit(1)

    engine = create_engine(db_url, echo=False)
    SessionFactory = sessionmaker(bind=engine)

    client = MturClient()
    config = ScoreConfig()

    with SessionFactory() as session:
        for uf in ufs:
            asyncio.run(MturSeedIngest(client, session, config).produce(uf))
            session.commit()
            print(f"[{uf}] ingested")

        # Routing summary for the ingested UFs
        rows = session.execute(
            select(RioRecord.uf, RioRecord.routing, func.count(RioRecord.id))
            .where(RioRecord.entity_type == "destination", RioRecord.uf.in_(ufs))
            .group_by(RioRecord.uf, RioRecord.routing)
            .order_by(RioRecord.uf, RioRecord.routing)
        ).all()

    print("\nrouting summary (entity_type=destination):")
    for uf, routing, n in rows:
        print(f"  {uf}  {routing:<12} {n}")


if __name__ == "__main__":
    main()
