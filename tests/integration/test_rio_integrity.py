"""Nascente/Rio integrity against Postgres: write races (F22), supersede (F16), reprocess lock.

Requires: docker-compose postgres up + BRAVE_DB_URL set (migration 0020 applied).
"""

import uuid
from unittest.mock import patch

import pytest
from sqlalchemy.exc import IntegrityError

import brave.core.nascente.service as nascente_service
import brave.core.rio.routing as routing
from brave.config.settings import ScoreConfig
from brave.core.models import NascenteRecord
from brave.core.nascente.service import store_raw


def _ref() -> str:
    return f"ibge:{uuid.uuid4().hex[:10]}"


@pytest.mark.integration
def test_version_is_unique_per_source_ref(db_session):
    ref = _ref()
    for content_hash in ("a" * 64, "b" * 64):
        db_session.add(NascenteRecord(
            id=uuid.uuid4(), source="ibge", source_ref=ref, entity_type="destination",
            uf="BA", payload={}, content_hash=content_hash, version=1,
        ))
    with pytest.raises(IntegrityError):
        db_session.flush()


@pytest.mark.integration
def test_store_raw_loser_of_a_race_retries_instead_of_forking(db_session):
    ref = _ref()
    v1 = store_raw(db_session, "ibge", ref, "destination", "BA", {"name": "A"})
    db_session.flush()

    # The racing writer read before v1 existed: both of its lookups come back empty.
    real_scalar = db_session.scalar
    seen = []

    def stale_scalar(*a, **k):
        seen.append(1)
        return None if len(seen) <= 2 else real_scalar(*a, **k)

    with patch.object(db_session, "scalar", side_effect=stale_scalar):
        v2 = store_raw(db_session, "ibge", ref, "destination", "BA", {"name": "B"})

    db_session.flush()  # the session survived the IntegrityError (savepoint)
    db_session.refresh(v1)
    assert v2.version == 2 and v1.superseded_by_id == v2.id


@pytest.mark.integration
def test_concurrent_rio_insert_returns_the_winner(db_session):
    ref = _ref()
    n = store_raw(db_session, "ibge", ref, "destination", "BA", {"name": "Trancoso"})
    winner = routing.process_nascente_record(db_session, n, ScoreConfig())
    db_session.flush()

    real_get = routing._rio_repo.get_by_canonical_key
    seen = []

    def stale_get(s, k):
        seen.append(1)
        return None if len(seen) == 1 else real_get(s, k)

    with patch.object(routing._rio_repo, "get_by_canonical_key", side_effect=stale_get), \
            patch.object(routing, "find_duplicate", return_value=None):
        assert routing.process_nascente_record(db_session, n, ScoreConfig()) is winner
    db_session.flush()


@pytest.mark.integration
def test_superseding_version_updates_rio_and_keeps_enrichment(db_session):
    ref = _ref()
    v1 = store_raw(db_session, "ibge", ref, "destination", "BA",
                   {"name": "Porto Seguro", "canonical": {"participates_mtur": False}})
    rio = routing.process_nascente_record(db_session, v1, ScoreConfig())
    rio.normalized = {**rio.normalized, "descricao_editorial": "curada"}
    db_session.flush()

    v2 = store_raw(db_session, "ibge", ref, "destination", "BA",
                   {"name": "Porto Seguro", "canonical": {"participates_mtur": True}})
    assert routing.process_nascente_record(db_session, v2, ScoreConfig()) is rio
    db_session.flush()
    db_session.expire_all()

    assert rio.nascente_id == v2.id
    assert rio.normalized["participates_mtur"] is True
    assert rio.normalized["descricao_editorial"] == "curada"


@pytest.mark.integration
def test_reprocess_keeps_descarte_under_lock(db_session):
    n = store_raw(db_session, "ibge", _ref(), "destination", "BA", {"name": "X"})
    rio = routing.process_nascente_record(db_session, n, ScoreConfig())
    rio.routing, rio.dlq_reason = "descarte", "steward_rejected"
    db_session.flush()

    routing.reprocess_record(db_session, rio.id, ScoreConfig())

    assert (rio.routing, rio.dlq_reason) == ("descarte", "steward_rejected")


def test_store_raw_reraises_a_second_integrity_error():
    """Not a retry loop: a second collision propagates."""
    from unittest.mock import MagicMock

    session = MagicMock()
    session.scalar.return_value = None
    session.begin_nested.return_value.__enter__.side_effect = IntegrityError("x", {}, Exception())
    with pytest.raises(IntegrityError):
        nascente_service.store_raw(session, "ibge", "ibge:1", "destination", "BA", {})
    assert session.begin_nested.call_count == 2


def _migration_0020():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "alembic/versions/0020_rio_nascente_integrity.py"
    spec = importlib.util.spec_from_file_location("m0020", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.integration
def test_0020_backfill_bumps_processed_at_of_rows_already_in_mar(db_session):
    """The outbox re-pushes an in-Mar row only when processed_at > its last push: the
    backfilled category / participates_mtur must bump it, or the API never gets them."""
    import contextlib
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from sqlalchemy import text

    from brave.core.models import RioRecord

    old = datetime(2026, 1, 1, tzinfo=UTC)
    rows = {}
    for routing_ in ("mar", "dlq"):
        ref = f"tripadvisor:attraction:{uuid.uuid4().int % 10**9}"
        n = store_raw(db_session, "tripadvisor", ref, "attraction", "BA",
                      {"name": "Cachoeira", "category": "Cachoeiras"})
        rio = RioRecord(id=uuid.uuid4(), nascente_id=n.id, entity_type="attraction", uf="BA",
                        routing=routing_, normalized={"name": "Cachoeira"}, canonical_key=ref,
                        processed_at=old)
        db_session.add(rio)
        rows[routing_] = rio
    db_session.flush()

    mod = _migration_0020()
    mod._INDEXES = {}  # CONCURRENTLY cannot run inside the test transaction
    mod.op = SimpleNamespace(
        execute=lambda sql: db_session.execute(text(sql)),
        get_context=lambda: SimpleNamespace(autocommit_block=contextlib.nullcontext),
    )
    mod.upgrade()
    for rio in rows.values():
        db_session.refresh(rio)

    assert rows["mar"].normalized["category"] == "Cachoeiras"
    assert rows["mar"].processed_at > old
    assert rows["dlq"].normalized["category"] == "Cachoeiras"
    assert rows["dlq"].processed_at == old  # not in Mar: nothing to re-push
