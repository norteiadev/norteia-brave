"""ix_rio_description_candidates must stay usable by the describe_uf candidate query.

The partial-index predicate spells a CAST exactly as SQLAlchemy renders
description_candidates_filter(). If either side drifts the planner silently stops using the
index (no error, just a seq scan over rio_records) — this is the only thing that notices.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import select

from brave.core.models import RioRecord
from brave.lanes.atrativos.copy_batch import description_candidates_filter

pytestmark = pytest.mark.integration


@pytest.mark.skipif(not os.environ.get("BRAVE_DB_URL"), reason="BRAVE_DB_URL not set")
def test_describe_uf_query_matches_partial_index(db_session) -> None:
    stmt = (
        select(RioRecord.id)
        .where(RioRecord.uf == "ES", *description_candidates_filter())
        .where(RioRecord.id > uuid.UUID(int=0))
        .order_by(RioRecord.id)
        .limit(25)
    )
    compiled = stmt.compile(dialect=db_session.get_bind().dialect)
    # Raw cursor: EXPLAIN needs the driver's own bind-param path, as the real query uses.
    cur = db_session.connection().connection.dbapi_connection.cursor()
    # Tiny/empty test tables make a seq scan cheapest; we assert the index is ELIGIBLE.
    cur.execute("SET LOCAL enable_seqscan = off")
    cur.execute("EXPLAIN " + str(compiled), compiled.params)
    plan = "\n".join(row[0] for row in cur.fetchall())
    assert "ix_rio_description_candidates" in plan, plan
