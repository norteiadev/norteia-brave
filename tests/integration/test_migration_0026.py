"""Migration 0026 (claim validation): columns and the wider status, up and down."""

from __future__ import annotations

import pytest
from alembic.autogenerate import compare_metadata
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect

from brave.core.models import Base
from tests.integration.editorial_migrations import chain, downgrade_to, upgrade_from

pytestmark = pytest.mark.integration

_NEW = {"validation_verdict", "validation_sources", "validation_note", "validated_at"}


def _claims(conn) -> dict:
    return {c["name"]: c for c in inspect(conn).get_columns("editorial_claims")}


def test_up_adds_the_validation_columns_and_widens_status_down_restores(db_engine) -> None:
    steps = chain()
    with db_engine.connect() as conn:
        trans = conn.begin()
        try:
            with Operations.context(MigrationContext.configure(conn)):
                downgrade_to(steps, 26)
                cols = _claims(conn)
                assert not _NEW & set(cols) and cols["status"]["type"].length == 16

                upgrade_from(steps, 26)
                cols = _claims(conn)
                assert set(cols) >= _NEW and cols["status"]["type"].length == 24
                diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
                ours = [d for d in diff if "editorial_claims" in repr(d)]
                assert ours == [], ours

                downgrade_to(steps, 26)
                assert not _NEW & set(_claims(conn))
        finally:
            trans.rollback()
