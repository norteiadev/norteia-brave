"""Integration test for Alembic migration 0006 (TA-05).

Verifies that migration 0006 (add rio_records.mar_ready column) can be
applied and reversed without error.

Skips if BRAVE_DB_URL is not set (CI runs without a DB container).
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.integration


def test_migration_0006_revision_metadata() -> None:
    """migration 0006 file has correct revision and down_revision values."""
    from alembic.script import ScriptDirectory
    from alembic.config import Config

    # Locate alembic.ini relative to this project
    project_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    alembic_cfg = Config(os.path.join(project_root, "alembic.ini"))
    scripts = ScriptDirectory.from_config(alembic_cfg)

    rev = scripts.get_revision("0006")
    assert rev is not None, "Revision 0006 not found in alembic versions"
    assert rev.revision == "0006"
    assert rev.down_revision == "0005"


@pytest.mark.skipif(
    not os.environ.get("BRAVE_DB_URL"),
    reason="BRAVE_DB_URL not set — skipping DB migration test",
)
def test_migration_0006_up_down(db_engine) -> None:
    """Migration 0006 upgrade adds mar_ready column; downgrade removes it.

    Requires a live Postgres database. The test runs upgrade 0006, verifies the
    column exists, then runs downgrade -1 to reverse it.
    """
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect as sa_inspect

    project_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    alembic_cfg = Config(os.path.join(project_root, "alembic.ini"))
    alembic_cfg.set_main_option("sqlalchemy.url", os.environ["BRAVE_DB_URL"])

    # Apply migration 0006
    command.upgrade(alembic_cfg, "0006")

    # Verify column exists
    inspector = sa_inspect(db_engine)
    columns = {col["name"] for col in inspector.get_columns("rio_records")}
    assert "mar_ready" in columns, "mar_ready column not found after upgrade 0006"

    # Verify index exists
    indexes = {idx["name"] for idx in inspector.get_indexes("rio_records")}
    assert "ix_rio_records_mar_ready" in indexes, "ix_rio_records_mar_ready index not found"

    # Downgrade
    command.downgrade(alembic_cfg, "-1")

    # Verify column removed
    inspector2 = sa_inspect(db_engine)
    columns_after = {col["name"] for col in inspector2.get_columns("rio_records")}
    assert "mar_ready" not in columns_after, "mar_ready column still present after downgrade"

    # Re-apply so DB is back at 0006 for other tests
    command.upgrade(alembic_cfg, "0006")
