"""Integration tests for Nascente service (store_raw, get_nascente).

Requires: docker-compose postgres up + BRAVE_DB_URL set.
Marked @pytest.mark.integration — skipped when DB unavailable.
"""

import uuid

import pytest

from brave.core.models import NascenteRecord
from brave.core.nascente.service import get_nascente, store_raw


@pytest.mark.integration
def test_store_raw_creates_nascente_record(db_session):
    """store_raw creates a NascenteRecord with correct fields."""
    record = store_raw(
        session=db_session,
        source="mtur",
        source_ref=f"mtur:BA:{uuid.uuid4().hex[:8]}",
        entity_type="destination",
        uf="BA",
        payload={"name": "Trancoso", "municipio": "Porto Seguro"},
    )
    db_session.flush()

    assert record.id is not None
    assert record.source == "mtur"
    assert record.entity_type == "destination"
    assert record.uf == "BA"
    assert record.version == 1
    assert len(record.content_hash) == 64  # SHA-256 hex


@pytest.mark.integration
def test_store_raw_content_hash_deterministic(db_session):
    """content_hash is deterministic SHA-256 of sorted-key JSON payload."""
    import hashlib
    import json

    payload = {"name": "Trancoso", "municipio": "Porto Seguro"}
    expected_hash = hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode()
    ).hexdigest()

    record = store_raw(
        session=db_session,
        source="mtur",
        source_ref=f"mtur:BA:{uuid.uuid4().hex[:8]}",
        entity_type="destination",
        uf="BA",
        payload=payload,
    )
    db_session.flush()
    assert record.content_hash == expected_hash


@pytest.mark.integration
def test_store_raw_idempotent(db_session):
    """Calling store_raw twice with identical (source, source_ref, payload) returns same record."""
    source_ref = f"mtur:BA:{uuid.uuid4().hex[:8]}"
    payload = {"name": "Trancoso"}

    record1 = store_raw(
        session=db_session,
        source="mtur",
        source_ref=source_ref,
        entity_type="destination",
        uf="BA",
        payload=payload,
    )
    db_session.flush()

    record2 = store_raw(
        session=db_session,
        source="mtur",
        source_ref=source_ref,
        entity_type="destination",
        uf="BA",
        payload=payload,
    )
    db_session.flush()

    assert record1.id == record2.id
    assert record1.version == record2.version == 1


@pytest.mark.integration
def test_store_raw_new_payload_supersedes_old(db_session):
    """store_raw with updated payload creates new version and sets superseded_by_id."""
    source_ref = f"mtur:BA:{uuid.uuid4().hex[:8]}"

    old_record = store_raw(
        session=db_session,
        source="mtur",
        source_ref=source_ref,
        entity_type="destination",
        uf="BA",
        payload={"name": "Trancoso"},
    )
    db_session.flush()

    new_record = store_raw(
        session=db_session,
        source="mtur",
        source_ref=source_ref,
        entity_type="destination",
        uf="BA",
        payload={"name": "Trancoso", "municipio": "Porto Seguro"},
    )
    db_session.flush()
    db_session.refresh(old_record)

    assert old_record.id != new_record.id
    assert new_record.version == old_record.version + 1
    assert old_record.superseded_by_id == new_record.id


@pytest.mark.integration
def test_get_nascente_returns_record(db_session):
    """get_nascente returns the NascenteRecord for a given ID."""
    source_ref = f"mtur:BA:{uuid.uuid4().hex[:8]}"
    record = store_raw(
        session=db_session,
        source="mtur",
        source_ref=source_ref,
        entity_type="destination",
        uf="BA",
        payload={"name": "Arraial d'Ajuda"},
    )
    db_session.flush()

    fetched = get_nascente(db_session, record.id)
    assert fetched is not None
    assert fetched.id == record.id


@pytest.mark.integration
def test_get_nascente_returns_none_for_missing(db_session):
    """get_nascente returns None for a non-existent ID."""
    result = get_nascente(db_session, uuid.uuid4())
    assert result is None
