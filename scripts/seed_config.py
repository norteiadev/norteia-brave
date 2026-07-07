"""Seed the config_settings overlay with default rows — idempotent (Phase D).

Inserts the default operator-tunable config rows (score weights + threshold,
per-source enabled flags, engine mode) IF ABSENT. Values equal the current
env-effective AppConfig, so seeding never changes pipeline behavior — it just
materializes the persistent baseline the config-management surface edits.

Safe to re-run: existing rows are left untouched.

reset-brave-db interaction: the reset script truncates config_settings. Run this
AFTER a reset to repopulate the defaults:
    set -a; source .env; set +a          # ensure BRAVE_DB_URL is set
    .venv/bin/python -m scripts.seed_config

Usage (env must be loaded so BRAVE_DB_URL is set):
    set -a; source .env; set +a
    .venv/bin/python -m scripts.seed_config
"""

from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from brave.config.runtime import seed_default_config


def main() -> int:
    db_url = os.environ.get("BRAVE_DB_URL")
    if not db_url:
        print("ERROR: BRAVE_DB_URL not set. Run: set -a; source .env; set +a")
        return 1

    engine = create_engine(db_url, echo=False)
    session_factory = sessionmaker(bind=engine)

    with session_factory() as session:
        inserted = seed_default_config(session)
        session.commit()

    print(f"config_settings seeded: {inserted} row(s) inserted (absent-only, idempotent).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
