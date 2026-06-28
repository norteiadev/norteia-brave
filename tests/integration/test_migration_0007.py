"""Integration test for Alembic migration 0007 (UI-PAINEL-2, Varreduras).

Verifies that migration 0007 (add runs_history table) can be applied and
reversed without error.

The revision-metadata test runs fully offline (no DB). The up/down test skips
if BRAVE_DB_URL is not set (CI runs without a DB container).
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.integration


def test_migration_0007_revision_metadata() -> None:
    """migration 0007 file has correct revision and down_revision values."""
    from alembic.script import ScriptDirectory
    from alembic.config import Config

    # Locate alembic.ini relative to this project
    project_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    alembic_cfg = Config(os.path.join(project_root, "alembic.ini"))
    scripts = ScriptDirectory.from_config(alembic_cfg)

    rev = scripts.get_revision("0007")
    assert rev is not None, "Revision 0007 not found in alembic versions"
    assert rev.revision == "0007"
    assert rev.down_revision == "0006"


@pytest.mark.skipif(
    not os.environ.get("BRAVE_DB_URL"),
    reason="BRAVE_DB_URL not set — skipping DB migration test",
)
def test_migration_0007_up_down(db_engine) -> None:
    """Migration 0007 upgrade creates runs_history; downgrade removes it.

    Requires a live Postgres database. The test runs upgrade 0007, verifies the
    table + index exist, then runs downgrade -1 to reverse it.
    """
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect as sa_inspect

    project_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    alembic_cfg = Config(os.path.join(project_root, "alembic.ini"))
    alembic_cfg.set_main_option("sqlalchemy.url", os.environ["BRAVE_DB_URL"])

    # Apply migration 0007
    command.upgrade(alembic_cfg, "0007")

    # Verify table exists
    inspector = sa_inspect(db_engine)
    assert "runs_history" in inspector.get_table_names(), (
        "runs_history table not found after upgrade 0007"
    )

    # Verify index exists
    indexes = {idx["name"] for idx in inspector.get_indexes("runs_history")}
    assert "ix_runs_history_started_at" in indexes, (
        "ix_runs_history_started_at index not found"
    )

    # Downgrade
    command.downgrade(alembic_cfg, "-1")

    # Verify table removed
    inspector2 = sa_inspect(db_engine)
    assert "runs_history" not in inspector2.get_table_names(), (
        "runs_history table still present after downgrade"
    )

    # Re-apply so DB is back at 0007 for other tests
    command.upgrade(alembic_cfg, "0007")
