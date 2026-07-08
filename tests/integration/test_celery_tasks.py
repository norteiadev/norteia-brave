"""Integration tests for Celery pipeline tasks (idempotency, poison quarantine).

Requires: docker-compose postgres up + BRAVE_DB_URL set.
Marked @pytest.mark.integration — skipped when DB unavailable.
"""

import uuid

import pytest

from brave.core.models import PoisonQuarantine, RioRecord
from brave.core.nascente.service import store_raw


@pytest.mark.integration
def test_process_nascente_task_idempotent(db_session):
    """process_nascente task called twice with same nascente_id produces exactly one RioRecord."""
    from brave.core.rio.routing import process_nascente_record
    from brave.config.settings import ScoreConfig

    source_ref = f"mtur:BA:{uuid.uuid4().hex[:8]}"
    nascente = store_raw(
        session=db_session,
        source="mtur",
        source_ref=source_ref,
        entity_type="destination",
        uf="BA",
        payload={"name": "Trancoso", "origem_value": 85.0, "completude_value": 85.0,
                 "corroboracao_value": 85.0, "atualidade_value": 85.0, "validacao_humana_value": 0.0},
    )
    db_session.flush()

    config = ScoreConfig()
    # Call twice
    rio1 = process_nascente_record(db_session, nascente, config)
    db_session.flush()
    rio2 = process_nascente_record(db_session, nascente, config)
    db_session.flush()

    # Should return the same record both times
    assert rio1.id == rio2.id

    # Count RioRecords for this nascente — should be exactly 1
    from sqlalchemy import select
    count_query = select(RioRecord).where(RioRecord.nascente_id == nascente.id)
    rio_records = list(db_session.scalars(count_query).all())
    assert len(rio_records) == 1


@pytest.mark.integration
def test_quarantine_poison_creates_row(db_session):
    """quarantine_poison inserts a PoisonQuarantine row."""
    from brave.tasks.pipeline import quarantine_poison

    nascente_id = uuid.uuid4()
    quarantine_poison(
        session=db_session,
        nascente_id=nascente_id,
        task_name="brave.process_nascente",
        error="Simulated permanent failure",
    )
    db_session.flush()

    from sqlalchemy import select
    rows = list(
        db_session.scalars(
            select(PoisonQuarantine).where(PoisonQuarantine.nascente_id == nascente_id)
        ).all()
    )
    assert len(rows) == 1
    assert rows[0].task_name == "brave.process_nascente"


@pytest.mark.integration
def test_poison_quarantine_not_dlq(db_session):
    """Poison quarantine row does NOT set routing='dlq' on RioRecord.

    The reliability DLQ (routing='dlq') and Celery poison quarantine are distinct.
    Poison → PoisonQuarantine table; DLQ → RioRecord.routing='dlq'.
    """
    from brave.tasks.pipeline import quarantine_poison

    nascente_id = uuid.uuid4()
    quarantine_poison(
        session=db_session,
        nascente_id=nascente_id,
        task_name="brave.process_nascente",
        error="Permanent failure",
    )
    db_session.flush()

    # Confirm no RioRecord with routing='dlq' was created for this poison
    from sqlalchemy import select
    rio_records = list(
        db_session.scalars(
            select(RioRecord).where(
                RioRecord.nascente_id == nascente_id,
                RioRecord.routing == "dlq",
            )
        ).all()
    )
    assert len(rio_records) == 0, (
        "Poison quarantine must NOT create a routing='dlq' RioRecord. "
        "Use PoisonQuarantine table for Celery failures; DLQ is for reliability score routing."
    )
