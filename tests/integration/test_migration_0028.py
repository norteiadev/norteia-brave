"""Migration 0028 (superseded_from_status): the column, up and down."""

from __future__ import annotations

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect

from tests.integration.editorial_migrations import chain, downgrade_to, upgrade_from

pytestmark = pytest.mark.integration


def test_up_adds_superseded_from_status_down_drops_it(db_engine) -> None:
    steps = chain()
    with db_engine.connect() as conn:
        trans = conn.begin()
        try:
            with Operations.context(MigrationContext.configure(conn)):
                downgrade_to(steps, 28)
                cols = {c["name"] for c in inspect(conn).get_columns("editorial_claims")}
                assert "superseded_from_status" not in cols
                upgrade_from(steps, 28)
                cols = {c["name"] for c in inspect(conn).get_columns("editorial_claims")}
                assert "superseded_from_status" in cols
                downgrade_to(steps, 28)
                cols = {c["name"] for c in inspect(conn).get_columns("editorial_claims")}
                assert "superseded_from_status" not in cols
        finally:
            trans.rollback()
