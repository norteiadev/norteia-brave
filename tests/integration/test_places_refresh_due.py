"""brave.refresh_places selection against Postgres: 60-day cadence, 90 for established.

Requires: docker-compose postgres up + BRAVE_DB_URL set.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from brave.core.models import RioRecord
from brave.core.nascente.service import store_raw
from brave.tasks import pipeline


@pytest.mark.integration
def test_due_window_depends_on_review_count(db_session):
    now = datetime.now(UTC)

    def rio(days_ago: int, review_count: int | None) -> uuid.UUID:
        ref = f"tripadvisor:attraction:{uuid.uuid4().hex[:10]}"
        n = store_raw(db_session, "tripadvisor", ref, "attraction", "BA", {"name": "X"})
        normalized = {
            "name": "X",
            "place_id_cache": "ChIJ" + uuid.uuid4().hex[:8],
            "places_refreshed_at": (now - timedelta(days=days_ago)).isoformat(),
        }
        if review_count is not None:
            normalized["review_count"] = review_count
        r = RioRecord(id=uuid.uuid4(), nascente_id=n.id, entity_type="attraction", uf="BA",
                      routing="mar", normalized=normalized, canonical_key=ref)
        db_session.add(r)
        return r.id

    few_61 = rio(61, 5)
    unknown_61 = rio(61, None)
    few_59 = rio(59, 5)
    known_61 = rio(61, 40)
    known_91 = rio(91, 40)
    db_session.flush()

    due = set(pipeline._places_refresh_due(db_session, 10_000))
    assert {few_61, unknown_61, known_91} <= due
    assert not {few_59, known_61} & due
