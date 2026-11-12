"""Migration 0024 (document versions): up, down, and the backfill of the initial version."""

from __future__ import annotations

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect, text

from tests.integration.editorial_migrations import chain, downgrade_to, upgrade_from

pytestmark = pytest.mark.integration


def test_existing_documents_get_an_initial_version_and_down_is_clean(db_engine) -> None:
    steps = chain()
    with db_engine.connect() as conn:
        trans = conn.begin()
        try:
            with Operations.context(MigrationContext.configure(conn)):
                downgrade_to(steps, 24)
                assert "editorial_document_versions" not in inspect(conn).get_table_names()
                conn.execute(
                    text(
                        "INSERT INTO editorial_documents (url, source, uf, published_time,"
                        " content_hash, body_text, kml_points, fetched_at) VALUES ('https://x/a',"
                        " 'viagensecaminhos', 'ES', '2020-02-17', :h, 'corpo do post', '[]',"
                        " '2026-11-10T10:00:00Z')"
                    ),
                    {"h": "a" * 64},
                )
                upgrade_from(steps, 24)
                row = conn.execute(
                    text(
                        "SELECT v.html, v.body_text, v.content_hash, v.published_time,"
                        " v.fetched_at, d.current_version_id = v.id"
                        " FROM editorial_documents d JOIN editorial_document_versions v"
                        " ON v.document_url = d.url WHERE d.url = 'https://x/a'"
                    )
                ).one()
                assert row[0] is None and row[1] == "corpo do post" and row[2] == "a" * 64
                assert str(row[3]) == "2020-02-17" and row[4].year == 2026 and row[5] is True

                downgrade_to(steps, 24)
                assert "editorial_document_versions" not in inspect(conn).get_table_names()
                cols = {c["name"] for c in inspect(conn).get_columns("editorial_documents")}
                assert "current_version_id" not in cols
        finally:
            trans.rollback()
