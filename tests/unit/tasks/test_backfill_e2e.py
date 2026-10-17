"""Bulk → backfill → Places cap → resume, with the REAL pieces joined (no fake seams).

produce_paginated (real bulk ingest) writes the records; _backfill_batch drives the real
TripAdvisorAtrativosIngest.backfill_record and a real PlacesEnrichmentAgent whose Places
client hits our cap on the 2nd record. Offline: fake TA client / geocoder / Places client,
fakeredis; the test DB inside a rolled-back transaction (skipped without BRAVE_DB_URL).
"""

from __future__ import annotations

import asyncio
import random
from datetime import UTC, datetime, timedelta
from typing import Any

import fakeredis
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from brave.config.settings import ScoreConfig, TripAdvisorConfig
from brave.core.models import RioRecord
from brave.domains.places.places_enrichment import PlacesEnrichmentAgent
from brave.domains.tripadvisor.atrativos import TripAdvisorAtrativosIngest
from brave.domains.tripadvisor.ibge import IbgeMunicipio
from brave.shared.exceptions import CostGuardError
from brave.tasks import pipeline
from tests.fakes.fake_nominatim import FakeGeocoderClient
from tests.fakes.fake_places import FakePlacesClient
from tests.fakes.fake_tripadvisor import FakeTripAdvisorClient

pytestmark = pytest.mark.integration

_UBERLANDIA = IbgeMunicipio("3170107", "Uberlândia", "MG", -18.9186, -48.2772)
_LAT, _LNG = -18.92, -48.28


@pytest.fixture
def tx_session(db_engine):
    conn = db_engine.connect()
    outer = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        outer.rollback()
        conn.close()


class _CappedPlaces(FakePlacesClient):
    """Places client whose cost guard trips before the ``cap_at``-th Text Search."""

    def __init__(self, names: list[str], cap_at: int | None) -> None:
        result = {"place_id": "", "location": {"lat": _LAT, "lng": _LNG},
                  "types": ["tourist_attraction"]}
        super().__init__(
            fixture_results={n: [{**result, "name": n, "place_id": f"ChIJ-{n}"}] for n in names},
            fixture_details={f"ChIJ-{n}": {
                "place_id": f"ChIJ-{n}", "business_status": "OPERATIONAL",
                "weekday_text": ["seg: 8-18"], "reviews": [], "formatted_address": "x",
                "location": {"lat": _LAT, "lng": _LNG},
            } for n in names},
        )
        self._cap_at = cap_at

    async def text_search(self, query: str, uf: str) -> list[dict[str, Any]]:
        if self._cap_at is not None and len(self.text_search_calls) + 1 >= self._cap_at:
            raise CostGuardError("places cap")  # raised BEFORE the call, like the real one
        return await super().text_search(query, uf)


def _ingest(session, ta, places_client=None):
    agent = None
    if places_client is not None:
        agent = PlacesEnrichmentAgent(
            places_client=places_client, session=session, config=ScoreConfig(),
            description_enabled=False,
        )
    return TripAdvisorAtrativosIngest(
        ta_client=ta, session=session, config=ScoreConfig(), ibge_records=[_UBERLANDIA],
        geocoder=FakeGeocoderClient(), ta_config=TripAdvisorConfig(page_throttle_seconds=0),
        places_agent=agent,
    )


def test_bulk_backfill_cap_pause_and_resume(tx_session):
    base = random.randint(10**9, 2 * 10**9)
    ids = [base + i for i in range(3)]
    names = [f"Atrativo {i}" for i in ids]
    cards = [{"locationId": i, "name": n, "review_count": 30, "rating": 4.5, "category": "Parks"}
             for i, n in zip(ids, names, strict=True)]
    national = {str(i): {"lat": _LAT, "lon": _LNG, "osm_id": i, "municipio_name": "Uberlândia",
                         "uf": "MG"} for i in ids}
    recent = datetime.now(UTC) - timedelta(days=4)
    ta = FakeTripAdvisorClient(
        gql_pages=[(0, cards)],
        fixture_geo={i: {"location_id": i, "city_name": "Uberlandia",
                         "state_name": "State of Minas Gerais", "city_geo_id": 1,
                         "state_geo_id": 2} for i in ids},
        recent_review={"review_count": 40, "rating": None, "most_recent_review_at": recent},
    )
    rc = fakeredis.FakeRedis()

    # 1. Bulk: cheap listing → provisional, parent-less, unmarked records.
    bulk = _ingest(tx_session, ta)
    bulk._geocoder = FakeGeocoderClient(fixture_national_results=national)
    asyncio.run(bulk.produce_paginated(294280, 1, 1, rc, run_rio=True))
    keys = [f"tripadvisor:attraction:{i}" for i in ids]
    rows = list(tx_session.scalars(
        select(RioRecord).where(RioRecord.canonical_key.in_(keys)).order_by(RioRecord.id)
    ))
    assert len(rows) == 3 and not any(r.normalized.get("ta_backfilled_at") for r in rows)
    row_ids = [r.id for r in rows]

    def pending() -> set:
        stmt = select(RioRecord.id).where(
            *pipeline.backfill_pending_filter(True), RioRecord.id.in_(row_ids)
        )
        return set(tx_session.scalars(stmt).all())

    # 2. Backfill: the Places cap trips on the 2nd record.
    capped = _CappedPlaces([r.normalized["name"] for r in rows], cap_at=2)
    state: dict[str, Any] = {"cursor": None, "stop": None}
    asyncio.run(pipeline._backfill_batch(
        tx_session, _ingest(tx_session, ta, capped), row_ids, rc, state, None, 0.0
    ))

    assert state["stop"] == "cost"
    assert state["cursor"] == str(row_ids[0])
    tx_session.expire_all()
    first, in_flight, untouched = (tx_session.get(RioRecord, i) for i in row_ids)
    assert first.normalized["ta_backfilled_at"] and first.normalized["google_enriched"] is True
    assert first.normalized["parent_source_ref"]  # bulk record linked to its destino
    # The record in flight kept its (committed) TA side; Places is NOT marked.
    assert in_flight.normalized["ta_backfilled_at"]
    assert in_flight.normalized["most_recent_review_at"]
    assert in_flight.normalized.get("google_enriched") is not True
    assert not untouched.normalized.get("ta_backfilled_at")
    assert pending() == {in_flight.id, untouched.id}
    ta_calls_before = len(ta.geo_calls)

    # 3. Resume (new key / next day): only what is pending runs; the in-flight record
    # does NOT redo its TA calls.
    state = {"cursor": None, "stop": None}
    ok_places = _CappedPlaces([r.normalized["name"] for r in rows], cap_at=None)
    asyncio.run(pipeline._backfill_batch(
        tx_session, _ingest(tx_session, ta, ok_places), sorted(pending()), rc, state, None, 0.0
    ))

    assert state["stop"] is None
    assert pending() == set()
    assert len(ta.geo_calls) - ta_calls_before == 1  # only the untouched record
