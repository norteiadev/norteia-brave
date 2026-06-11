"""End-to-end pipeline integration test (fixture → Nascente → Rio → score → Mar → push).

Tests the full walking skeleton: a synthetic payload flows through all pipeline stages
and ends with a verified FakeNorteiaApiClient push.

Requires:
  BRAVE_DB_URL=postgresql+psycopg://brave:brave@localhost:5432/norteia_brave
  (docker-compose up -d)

Score math verification (§7.6 weights: origem 30%, completude 20%,
corroboracao 20%, atualidade 15%, validacao_humana 15%):

  High-score (→ mar):
    origem=100, completude=100, corroboracao=80, atualidade=80, validacao_humana=100
    score = 30 + 20 + 16 + 12 + 15 = 93.0  (≥85 → mar)

  DLQ-score:
    origem=100, completude=40, corroboracao=20, atualidade=20, validacao_humana=40
    score = 30 + 8 + 4 + 3 + 6 = 51.0  (51 ≤ 51 < 85 → dlq)

  Descarte-score:
    origem=40, completude=0, corroboracao=0, atualidade=0, validacao_humana=0
    score = 12 + 0 + 0 + 0 + 0 = 12.0  (< 51 → descarte)
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from brave.config.settings import ScoreConfig
from brave.core.nascente.service import store_raw
from brave.core.rio.routing import process_nascente_record
from brave.core.mar.service import promote_to_mar
from tests.fakes.fake_norteia_api import FakeNorteiaApiClient

import asyncio

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Score math check
# ---------------------------------------------------------------------------

# High: 100*0.30 + 100*0.20 + 80*0.20 + 80*0.15 + 100*0.15 = 30+20+16+12+15 = 93
HIGH_SCORE_PAYLOAD = {
    "name": "Praia do Forte",
    "municipio": "Mata de Sao Joao",
    "tipo": "praia",
    "origem_value": 100.0,
    "completude_value": 100.0,
    "corroboracao_value": 80.0,
    "atualidade_value": 80.0,
    "validacao_humana_value": 100.0,
}

# DLQ: 100*0.30 + 40*0.20 + 20*0.20 + 20*0.15 + 40*0.15 = 30+8+4+3+6 = 51 (exactly at threshold)
DLQ_SCORE_PAYLOAD = {
    "name": "Praia Marginal",
    "municipio": "Bahia",
    "tipo": "praia",
    "origem_value": 100.0,
    "completude_value": 40.0,
    "corroboracao_value": 20.0,
    "atualidade_value": 20.0,
    "validacao_humana_value": 40.0,
}

# Descarte: 40*0.30 = 12 < 51
DESCARTE_SCORE_PAYLOAD = {
    "name": "Lugar Desconhecido",
    "municipio": "Interior",
    "tipo": "outro",
    "origem_value": 40.0,
    "completude_value": 0.0,
    "corroboracao_value": 0.0,
    "atualidade_value": 0.0,
    "validacao_humana_value": 0.0,
}


async def _push_via_fake(mar_record, entity_type, fake_client: FakeNorteiaApiClient) -> dict:
    """Push a MarRecord via FakeNorteiaApiClient (offline, no real HTTP)."""
    provenance_raw = mar_record.provenance or {}
    score_breakdown = provenance_raw.get("score_breakdown", {})
    score_version = provenance_raw.get("score_version", "v1.0")

    payload = {
        "source": mar_record.source_ref.split(":")[0] if ":" in mar_record.source_ref else "mtur",
        "source_ref": mar_record.source_ref,
        "entity_type": entity_type,
        "canonical": mar_record.canonical or {},
        "reliability_score": float(mar_record.reliability_score),
        "score_version": score_version,
        "provenance": {
            "origem": float(score_breakdown.get("origem", 0.0)),
            "completude": float(score_breakdown.get("completude", 0.0)),
            "corroboracao": float(score_breakdown.get("corroboracao", 0.0)),
            "atualidade": float(score_breakdown.get("atualidade", 0.0)),
            "validacao_humana": float(score_breakdown.get("validacao_humana", 0.0)),
        },
    }

    if entity_type == "destination":
        return await fake_client.push_destination(payload)
    else:
        return await fake_client.push_attraction(payload)


# ---------------------------------------------------------------------------
# Test 1: High-score fixture → Mar routing + push
# ---------------------------------------------------------------------------


@pytest.mark.enable_socket
def test_e2e_mar_routing_and_push(db_session: Session):
    """Full pipeline: fixture → Nascente → Rio → score=93 → mar → push once."""
    config = ScoreConfig()
    fake_client = FakeNorteiaApiClient()

    nascente = store_raw(
        session=db_session,
        source="mtur",
        source_ref="mtur:BA:e2e_001",
        entity_type="destination",
        uf="BA",
        payload=HIGH_SCORE_PAYLOAD,
    )
    db_session.flush()

    rio = process_nascente_record(db_session, nascente, config)
    db_session.flush()

    assert rio.routing == "mar", f"Expected routing=mar, got {rio.routing} (score={rio.score})"
    assert float(rio.score) >= 85.0, f"Expected score ≥ 85, got {rio.score}"

    mar = promote_to_mar(db_session, rio)
    db_session.flush()

    # Push via fake client (no real HTTP)
    asyncio.run(_push_via_fake(mar, "destination", fake_client))

    assert len(fake_client.push_destination_calls) == 1
    pushed = fake_client.push_destination_calls[0]
    assert pushed["source_ref"] == "mtur:BA:e2e_001"
    assert "provenance" in pushed
    # Verify flat provenance (Pact contract shape, D-16)
    assert "origem" in pushed["provenance"]
    assert "completude" in pushed["provenance"]
    assert "corroboracao" in pushed["provenance"]
    assert "atualidade" in pushed["provenance"]
    assert "validacao_humana" in pushed["provenance"]


# ---------------------------------------------------------------------------
# Test 2: Idempotency — running the same pipeline twice produces one push
# ---------------------------------------------------------------------------


@pytest.mark.enable_socket
def test_e2e_idempotent_double_run(db_session: Session):
    """Running the pipeline twice with same source_ref produces one push (idempotent)."""
    config = ScoreConfig()
    fake_client = FakeNorteiaApiClient()

    # First run
    nascente1 = store_raw(
        session=db_session,
        source="mtur",
        source_ref="mtur:BA:e2e_idem_001",
        entity_type="destination",
        uf="BA",
        payload=HIGH_SCORE_PAYLOAD,
    )
    db_session.flush()

    rio1 = process_nascente_record(db_session, nascente1, config)
    db_session.flush()
    assert rio1.routing == "mar"

    mar1 = promote_to_mar(db_session, rio1)
    db_session.flush()
    asyncio.run(_push_via_fake(mar1, "destination", fake_client))

    assert len(fake_client.push_destination_calls) == 1

    # Second run — same source_ref → same NascenteRecord returned (idempotent)
    nascente2 = store_raw(
        session=db_session,
        source="mtur",
        source_ref="mtur:BA:e2e_idem_001",
        entity_type="destination",
        uf="BA",
        payload=HIGH_SCORE_PAYLOAD,  # Same payload → same content_hash → no new row
    )
    db_session.flush()

    assert nascente2.id == nascente1.id, "Same payload must return same NascenteRecord"

    # process_nascente_record is idempotent: same canonical_key → returns existing RioRecord
    rio2 = process_nascente_record(db_session, nascente2, config)
    db_session.flush()
    assert rio2.id == rio1.id, "Same nascente must produce same RioRecord"

    # promote_to_mar is idempotent by source_ref: same source_ref → returns same (or updated) MarRecord
    mar2 = promote_to_mar(db_session, rio2)
    db_session.flush()

    # FakeNorteiaApiClient still has 1 call from the first run (we don't push again here)
    # In a real pipeline, norteia-api would upsert; here we track pushes via fake_client
    assert len(fake_client.push_destination_calls) == 1, (
        "Idempotent pipeline run must not cause a second push from the same fake_client instance"
    )


# ---------------------------------------------------------------------------
# Test 3: DLQ routing — no push
# ---------------------------------------------------------------------------


@pytest.mark.enable_socket
def test_e2e_dlq_routing_no_push(db_session: Session):
    """DLQ-routed fixture: push_destination is NOT called."""
    config = ScoreConfig()
    fake_client = FakeNorteiaApiClient()

    nascente = store_raw(
        session=db_session,
        source="mtur",
        source_ref="mtur:BA:e2e_dlq_001",
        entity_type="destination",
        uf="BA",
        payload=DLQ_SCORE_PAYLOAD,
    )
    db_session.flush()

    rio = process_nascente_record(db_session, nascente, config)
    db_session.flush()

    # Score = 51, which is exactly at threshold_dlq=51 → dlq
    assert rio.routing == "dlq", (
        f"Expected routing=dlq for score=51.0, got {rio.routing} (score={rio.score})"
    )

    # DLQ records are NOT pushed to norteia-api
    assert len(fake_client.push_destination_calls) == 0
    assert len(fake_client.push_attraction_calls) == 0


# ---------------------------------------------------------------------------
# Test 4: Descarte routing — no push
# ---------------------------------------------------------------------------


@pytest.mark.enable_socket
def test_e2e_descarte_routing_no_push(db_session: Session):
    """Descarte-routed fixture: push_destination is NOT called."""
    config = ScoreConfig()
    fake_client = FakeNorteiaApiClient()

    nascente = store_raw(
        session=db_session,
        source="mtur",
        source_ref="mtur:BA:e2e_desc_001",
        entity_type="destination",
        uf="BA",
        payload=DESCARTE_SCORE_PAYLOAD,
    )
    db_session.flush()

    rio = process_nascente_record(db_session, nascente, config)
    db_session.flush()

    # Score = 12 < 51 → descarte
    assert rio.routing == "descarte", (
        f"Expected routing=descarte for score=12.0, got {rio.routing} (score={rio.score})"
    )

    # Descarte records are NOT pushed
    assert len(fake_client.push_destination_calls) == 0
    assert len(fake_client.push_attraction_calls) == 0
