"""Integration tests for the Rio pipeline (process_nascente_record, route_by_score).

Requires: docker-compose postgres up + BRAVE_DB_URL set.
Marked @pytest.mark.integration — skipped when DB unavailable.
"""

import uuid

import pytest

from brave.config.settings import ScoreConfig
from brave.core.models import RioRecord
from brave.core.nascente.service import store_raw


@pytest.mark.integration
def test_process_nascente_record_creates_rio(db_session):
    """process_nascente_record creates a RioRecord for a given NascenteRecord."""
    from brave.core.rio.routing import process_nascente_record

    source_ref = f"mtur:BA:{uuid.uuid4().hex[:8]}"
    nascente = store_raw(
        session=db_session,
        source="mtur",
        source_ref=source_ref,
        entity_type="destination",
        uf="BA",
        payload={
            "name": "Trancoso",
            "municipio": "Porto Seguro",
            "origem_value": 100.0,
            "completude_value": 100.0,
            "corroboracao_value": 100.0,
            "atualidade_value": 100.0,
            "validacao_humana_value": 0.0,
        },
    )
    db_session.flush()

    config = ScoreConfig()
    rio = process_nascente_record(db_session, nascente, config)

    assert rio is not None
    assert isinstance(rio, RioRecord)
    assert rio.nascente_id == nascente.id
    assert rio.routing in ("mar", "dlq")
    assert rio.score_version == config.score_version


@pytest.mark.integration
def test_process_nascente_record_normalizes_lng_key(db_session):
    """Regression: a payload storing longitude under 'lng' (TA/Places convention)
    still populates normalized['lon']. Reading only payload['lon'] previously left
    normalized['lon'] perpetually null for every TA/Places record."""
    from brave.core.rio.routing import process_nascente_record

    source_ref = f"tripadvisor:attraction:{uuid.uuid4().hex[:8]}"
    nascente = store_raw(
        session=db_session,
        source="tripadvisor",
        source_ref=source_ref,
        entity_type="attraction",
        uf="ES",
        payload={
            "name": "Fundação Projeto Tamar",
            "lat": -20.3155,
            "lng": -40.2869,  # NOTE: 'lng', not 'lon'
            "origem_value": 65.0,
            "completude_value": 60.0,
            "corroboracao_value": 0.0,
            "atualidade_value": 0.0,
            "validacao_humana_value": 0.0,
        },
    )
    db_session.flush()

    rio = process_nascente_record(db_session, nascente, ScoreConfig())

    assert rio is not None
    assert rio.normalized["lat"] == pytest.approx(-20.3155)
    assert rio.normalized["lon"] == pytest.approx(-40.2869)


@pytest.mark.integration
def test_process_nascente_record_idempotent(db_session):
    """Calling process_nascente_record twice produces exactly one RioRecord."""
    from brave.core.rio.routing import process_nascente_record

    source_ref = f"mtur:BA:{uuid.uuid4().hex[:8]}"
    nascente = store_raw(
        session=db_session,
        source="mtur",
        source_ref=source_ref,
        entity_type="destination",
        uf="BA",
        payload={"name": "Caraíva", "origem_value": 90.0, "completude_value": 90.0,
                 "corroboracao_value": 90.0, "atualidade_value": 90.0, "validacao_humana_value": 0.0},
    )
    db_session.flush()

    config = ScoreConfig()
    rio1 = process_nascente_record(db_session, nascente, config)
    db_session.flush()
    rio2 = process_nascente_record(db_session, nascente, config)
    db_session.flush()

    assert rio1.id == rio2.id


@pytest.mark.integration
def test_route_by_score_mar_routing(db_session):
    """A record with all high values routes to 'mar' (≥85)."""
    from brave.core.rio.routing import process_nascente_record

    source_ref = f"mtur:BA:{uuid.uuid4().hex[:8]}"
    nascente = store_raw(
        session=db_session,
        source="mtur",
        source_ref=source_ref,
        entity_type="destination",
        uf="BA",
        payload={
            "origem_value": 100.0,
            "completude_value": 100.0,
            "corroboracao_value": 100.0,
            "atualidade_value": 100.0,
            "validacao_humana_value": 100.0,
        },
    )
    db_session.flush()

    config = ScoreConfig()
    rio = process_nascente_record(db_session, nascente, config)
    assert rio.routing == "mar"
    assert float(rio.score) == 100.0


@pytest.mark.integration
def test_route_by_score_dlq_routing(db_session):
    """A record with medium values routes to 'dlq' (51-84.9)."""
    from brave.core.rio.routing import process_nascente_record

    source_ref = f"mtur:BA:{uuid.uuid4().hex[:8]}"
    nascente = store_raw(
        session=db_session,
        source="mtur",
        source_ref=source_ref,
        entity_type="destination",
        uf="BA",
        payload={
            "origem_value": 100.0,
            "completude_value": 80.0,
            "corroboracao_value": 60.0,
            "atualidade_value": 40.0,
            "validacao_humana_value": 0.0,
        },
    )
    db_session.flush()

    config = ScoreConfig()
    rio = process_nascente_record(db_session, nascente, config)
    assert rio.routing == "dlq"
    assert float(rio.score) == 64.0


@pytest.mark.integration
def test_route_by_score_low_routing_dlq(db_session):
    """A record with low values routes to 'dlq' (binary gate, score < 80)."""
    from brave.core.rio.routing import process_nascente_record

    source_ref = f"mtur:BA:{uuid.uuid4().hex[:8]}"
    nascente = store_raw(
        session=db_session,
        source="mtur",
        source_ref=source_ref,
        entity_type="destination",
        uf="BA",
        payload={
            "origem_value": 40.0,
            "completude_value": 20.0,
            "corroboracao_value": 0.0,
            "atualidade_value": 0.0,
            "validacao_humana_value": 0.0,
        },
    )
    db_session.flush()

    config = ScoreConfig()
    rio = process_nascente_record(db_session, nascente, config)
    assert rio.routing == "dlq"
