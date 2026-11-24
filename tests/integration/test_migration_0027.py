"""Migration 0027 (bounded validation attempts): columns and default, up and down."""

from __future__ import annotations

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect, text

from tests.integration.editorial_migrations import chain, downgrade_to, upgrade_from

pytestmark = pytest.mark.integration

_NEW = {"validation_attempts", "last_validation_error", "last_attempt_at"}


def test_up_adds_the_attempt_columns_with_a_zero_default_down_drops_them(db_engine) -> None:
    steps = chain()
    with db_engine.connect() as conn:
        trans = conn.begin()
        try:
            with Operations.context(MigrationContext.configure(conn)):
                downgrade_to(steps, 27)
                conn.execute(
                    text(
                        "INSERT INTO editorial_documents (url, source, uf, content_hash, body_text,"
                        " kml_points) VALUES ('https://x/a', 's', 'ES', 'h', 'b', '[]')"
                    )
                )
                conn.execute(
                    text(
                        "INSERT INTO editorial_claims (id, document_url, source, entity_kind,"
                        " entity_name, field, value, stability, status) VALUES"
                        " ('00000000-0000-0000-0000-0000000000c1', 'https://x/a', 's', 'atrativo',"
                        " 'Pedra', 'tips', '\"x\"', 'estavel', 'pending_validation')"
                    )
                )
                upgrade_from(steps, 27)
                cols = {c["name"] for c in inspect(conn).get_columns("editorial_claims")}
                assert cols >= _NEW
                assert (
                    conn.execute(text("SELECT validation_attempts FROM editorial_claims")).scalar()
                    == 0
                )

                downgrade_to(steps, 27)
                cols = {c["name"] for c in inspect(conn).get_columns("editorial_claims")}
                assert not _NEW & cols
        finally:
            trans.rollback()
