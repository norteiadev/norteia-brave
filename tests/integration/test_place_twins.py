"""resolve_place_twins against Postgres: one atrativo per (município, google_place_id)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from brave.config.settings import ScoreConfig
from brave.core.mar import sync
from brave.core.mar.service import upsert_mar_snapshot
from brave.core.models import MarRecord, RioRecord
from brave.core.nascente.service import store_raw
from brave.core.rio.dedup import PLACE_TWIN_REASON, resolve_place_twins
from brave.core.rio.routing import process_nascente_record

pytestmark = pytest.mark.integration

_LONG_AGO = datetime.now(UTC) - timedelta(days=2)


def _ta(db, *, place="ChIJtwin", municipio_id="3304557", routing="mar", on_api=False,
        score=85, reviews=30) -> RioRecord:
    ref = f"tripadvisor:attraction:t{uuid.uuid4().hex[:10]}"
    payload = {"name": "Pedra da Gávea", "municipio_id": municipio_id, "uf": "RJ",
               "review_count": reviews, "locationId": ref}
    nascente = store_raw(db, "tripadvisor", ref, "attraction", "RJ", payload)
    db.flush()
    rio = process_nascente_record(db, nascente, ScoreConfig())
    rio.normalized = {**(rio.normalized or {}), "google_place_id": place, "review_count": reviews}
    rio.routing, rio.dlq_reason, rio.score = routing, None, score
    rio.processed_at = _LONG_AGO
    db.flush()
    if on_api:
        mar = upsert_mar_snapshot(db, rio)
        mar.push_hash, mar.pushed_at, mar.push_attempted_at = "h", _LONG_AGO, _LONG_AGO
        mar.published_at = _LONG_AGO
        db.flush()
    return rio


def _mar(db, rio: RioRecord) -> MarRecord:
    return db.scalars(
        select(MarRecord).where(MarRecord.rio_id == rio.id, MarRecord.superseded_by_id.is_(None))
    ).one()


def test_the_atrativo_on_the_api_is_kept_and_its_twins_discarded(db_session):
    place = f"ChIJ{uuid.uuid4().hex[:8]}"
    kept = _ta(db_session, place=place, on_api=True, score=86)
    better_dlq = _ta(db_session, place=place, routing="dlq", score=99, reviews=900)
    other_city = _ta(db_session, place=place, municipio_id="3303302", routing="dlq")
    other_place = _ta(db_session, place=f"{place}x", routing="dlq")

    losers = resolve_place_twins(db_session, better_dlq, actor="test")

    assert losers == [better_dlq]
    assert (better_dlq.routing, better_dlq.dlq_reason) == ("descarte", PLACE_TWIN_REASON)
    assert kept.routing == "mar"
    assert other_city.routing == other_place.routing == "dlq"


def test_a_twin_already_on_the_api_is_merged_and_hidden(db_session):
    place = f"ChIJ{uuid.uuid4().hex[:8]}"
    kept = _ta(db_session, place=place, on_api=True, reviews=500)
    twin = _ta(db_session, place=place, on_api=True, reviews=10)

    assert resolve_place_twins(db_session, kept, actor="test") == [twin]

    assert twin.routing == "descarte"
    assert twin.canonical_key in _mar(db_session, kept).provenance["merged_source_refs"]
    # Out of Mar with a visible API copy: the outbox pushes it as visibility=hidden.
    assert twin.id in {rio_id for rio_id, _ in sync.pending_push_rows(db_session, limit=100_000)}


def test_a_lone_place_is_left_alone(db_session):
    rio = _ta(db_session, place=f"ChIJ{uuid.uuid4().hex[:8]}")
    assert resolve_place_twins(db_session, rio, actor="test") == []
    assert rio.routing == "mar"
