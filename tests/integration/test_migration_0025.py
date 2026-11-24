"""Migration 0025 (claims are never deleted): columns, backfill, no ON DELETE CASCADE."""

from __future__ import annotations

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect, text

from tests.integration.editorial_migrations import chain, downgrade_to, upgrade_from

pytestmark = pytest.mark.integration


def _claim_fk_options(conn) -> dict:
    fks = inspect(conn).get_foreign_keys("editorial_claims")
    return next(fk for fk in fks if fk["referred_table"] == "editorial_documents")["options"]


def test_up_backfills_the_version_and_drops_the_cascade_down_restores_it(db_engine) -> None:
    steps = chain()
    with db_engine.connect() as conn:
        trans = conn.begin()
        try:
            with Operations.context(MigrationContext.configure(conn)):
                downgrade_to(steps, 25)
                assert _claim_fk_options(conn).get("ondelete") == "CASCADE"
                conn.execute(
                    text(
                        "INSERT INTO editorial_documents (url, source, uf, content_hash, body_text,"
                        " kml_points, current_version_id) VALUES ('https://x/a', 's', 'ES', 'h',"
                        " 'b', '[]', NULL)"
                    )
                )
                conn.execute(
                    text(
                        "INSERT INTO editorial_document_versions (id, document_url, content_hash,"
                        " body_text, kml_raw) VALUES ('00000000-0000-0000-0000-000000000001',"
                        " 'https://x/a', 'h', 'b', '{}')"
                    )
                )
                conn.execute(
                    text(
                        "UPDATE editorial_documents SET current_version_id ="
                        " '00000000-0000-0000-0000-000000000001' WHERE url = 'https://x/a'"
                    )
                )
                conn.execute(
                    text(
                        "INSERT INTO editorial_claims (id, document_url, source, entity_kind,"
                        " entity_name, field, value, stability, status) VALUES"
                        " ('00000000-0000-0000-0000-0000000000c1', 'https://x/a', 's', 'atrativo',"
                        " 'Pedra', 'curiosities', '\"x\"', 'estavel', 'matched')"
                    )
                )
                upgrade_from(steps, 25)
                row = conn.execute(
                    text("SELECT version_id::text, superseded_at FROM editorial_claims")
                ).one()
                assert row[0] == "00000000-0000-0000-0000-000000000001" and row[1] is None
                assert not _claim_fk_options(conn).get("ondelete")  # a delete of the post now fails

                downgrade_to(steps, 25)
                assert _claim_fk_options(conn).get("ondelete") == "CASCADE"
                cols = {c["name"] for c in inspect(conn).get_columns("editorial_claims")}
                assert not {"version_id", "superseded_at"} & cols
        finally:
            trans.rollback()
