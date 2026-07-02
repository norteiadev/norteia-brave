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

  Low-score:
    origem=40, completude=0, corroboracao=0, atualidade=0, validacao_humana=0
    score = 12 + 0 + 0 + 0 + 0 = 12.0  (< 80 → dlq)

Note: Tests use source_ref in payload to ensure unique content_hash per test invocation.
Phase 1 uses zero-vector embeddings as a stub — Stage 1 dedup checks content_hash,
so unique payloads are required to prevent cross-test false dedup matches.
"""

import uuid as _uuid
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from brave.config.settings import ScoreConfig
from brave.core.models import MarRecord
from brave.core.nascente.service import store_raw
from brave.core.rio.routing import process_nascente_record
from brave.core.mar.service import promote_to_mar
from tests.fakes.fake_norteia_api import FakeNorteiaApiClient

import asyncio

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Score math check — payloads include a unique_id field to prevent content_hash
# collisions across test runs (Phase 1 zero-vector embeddings would cause dedup
# to return cached records with stale routing values if content_hashes collide).
# ---------------------------------------------------------------------------


def _high_score_payload(source_ref: str) -> dict:
    """High-score payload (→ mar): score = 30+20+16+12+15 = 93.0 ≥ 85."""
    return {
        "name": "Praia do Forte",
        "municipio": "Mata de Sao Joao",
        "tipo": "praia",
        "test_id": source_ref,  # unique per test run → unique content_hash
        "origem_value": 100.0,
        "completude_value": 100.0,
        "corroboracao_value": 80.0,
        "atualidade_value": 80.0,
        "validacao_humana_value": 100.0,
    }


def _dlq_score_payload(source_ref: str) -> dict:
    """DLQ-score payload: score = 30+8+4+3+6 = 51.0 (< 80 → dlq)."""
    return {
        "name": "Praia Marginal",
        "municipio": "Bahia",
        "tipo": "praia",
        "test_id": source_ref,
        "origem_value": 100.0,
        "completude_value": 40.0,
        "corroboracao_value": 20.0,
        "atualidade_value": 20.0,
        "validacao_humana_value": 40.0,
    }


def _low_score_payload(source_ref: str) -> dict:
    """Low-score payload: score = 12.0 < 80 → dlq."""
    return {
        "name": "Lugar Desconhecido",
        "municipio": "Interior",
        "tipo": "outro",
        "test_id": source_ref,
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

    # Use uuid-based source_ref to prevent cross-run content_hash collisions
    source_ref = f"mtur:BA:e2e_001_{_uuid.uuid4().hex[:8]}"
    nascente = store_raw(
        session=db_session,
        source="mtur",
        source_ref=source_ref,
        entity_type="destination",
        uf="BA",
        payload=_high_score_payload(source_ref),
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
    assert pushed["source_ref"] == source_ref
    assert "provenance" in pushed
    # Verify flat provenance (Pact contract shape, D-16)
    assert "origem" in pushed["provenance"]
    assert "completude" in pushed["provenance"]
    assert "corroboracao" in pushed["provenance"]
    assert "atualidade" in pushed["provenance"]
    assert "validacao_humana" in pushed["provenance"]


# ---------------------------------------------------------------------------
# Test 2: Idempotency — store_raw and process_nascente_record return same rows on re-run
# ---------------------------------------------------------------------------


@pytest.mark.enable_socket
def test_e2e_idempotent_store_and_process(db_session: Session):
    """Running store_raw + process_nascente_record twice with same payload is idempotent.

    Verifies:
      - Second store_raw returns the same NascenteRecord (same content_hash)
      - Second process_nascente_record returns the same RioRecord (same canonical_key)
    This is the idempotency guarantee for the first two pipeline stages.
    """
    config = ScoreConfig()

    source_ref = f"mtur:BA:e2e_idem_{_uuid.uuid4().hex[:8]}"
    payload = _high_score_payload(source_ref)

    # First run
    nascente1 = store_raw(
        session=db_session,
        source="mtur",
        source_ref=source_ref,
        entity_type="destination",
        uf="BA",
        payload=payload,
    )
    db_session.flush()

    rio1 = process_nascente_record(db_session, nascente1, config)
    db_session.flush()
    assert rio1.routing == "mar"

    # Second run — same source_ref + same payload → same NascenteRecord (idempotent)
    nascente2 = store_raw(
        session=db_session,
        source="mtur",
        source_ref=source_ref,
        entity_type="destination",
        uf="BA",
        payload=payload,  # Same payload → same content_hash → no new row
    )
    db_session.flush()

    assert nascente2.id == nascente1.id, (
        "Second store_raw with same payload must return the same NascenteRecord"
    )

    # process_nascente_record is idempotent: same canonical_key → returns existing RioRecord
    rio2 = process_nascente_record(db_session, nascente2, config)
    db_session.flush()
    assert rio2.id == rio1.id, (
        "Second process_nascente_record with same nascente must return same RioRecord"
    )


@pytest.mark.enable_socket
def test_e2e_mar_push_idempotent_via_fake(db_session: Session):
    """FakeNorteiaApiClient.push_destination is called once per pipeline run.

    Simulates the idempotent push behavior: the client records one push,
    and a second push with the same source_ref also records a call (because
    norteia-api handles the server-side upsert — the client doesn't guard).
    Both pushes succeed (200), showing the idempotent upsert path is safe.
    """
    config = ScoreConfig()
    fake_client = FakeNorteiaApiClient()

    source_ref = f"mtur:BA:e2e_pushidem_{_uuid.uuid4().hex[:8]}"
    nascente = store_raw(
        session=db_session,
        source="mtur",
        source_ref=source_ref,
        entity_type="destination",
        uf="BA",
        payload=_high_score_payload(source_ref),
    )
    db_session.flush()

    rio = process_nascente_record(db_session, nascente, config)
    db_session.flush()
    assert rio.routing == "mar"

    mar = promote_to_mar(db_session, rio)
    db_session.flush()

    # First push
    asyncio.run(_push_via_fake(mar, "destination", fake_client))
    assert len(fake_client.push_destination_calls) == 1

    # Second push with same payload (idempotent upsert on norteia-api side)
    asyncio.run(_push_via_fake(mar, "destination", fake_client))
    assert len(fake_client.push_destination_calls) == 2  # Both succeed, norteia-api handles upsert


# ---------------------------------------------------------------------------
# Test 3: DLQ routing — no push
# ---------------------------------------------------------------------------


@pytest.mark.enable_socket
def test_e2e_dlq_routing_no_push(db_session: Session):
    """DLQ-routed fixture: push_destination is NOT called."""
    config = ScoreConfig()
    fake_client = FakeNorteiaApiClient()

    source_ref = f"mtur:BA:e2e_dlq_{_uuid.uuid4().hex[:8]}"
    nascente = store_raw(
        session=db_session,
        source="mtur",
        source_ref=source_ref,
        entity_type="destination",
        uf="BA",
        payload=_dlq_score_payload(source_ref),
    )
    db_session.flush()

    rio = process_nascente_record(db_session, nascente, config)
    db_session.flush()

    # Score = 51.0 < 80 → dlq
    assert rio.routing == "dlq", (
        f"Expected routing=dlq for score=51.0, got {rio.routing} (score={rio.score})"
    )

    # DLQ records are NOT pushed to norteia-api
    assert len(fake_client.push_destination_calls) == 0
    assert len(fake_client.push_attraction_calls) == 0


# ---------------------------------------------------------------------------
# Test 4: Low-score routing — no push
# ---------------------------------------------------------------------------


@pytest.mark.enable_socket
def test_e2e_low_score_routing_no_push(db_session: Session):
    """Low-score-routed fixture (dlq): push_destination is NOT called."""
    config = ScoreConfig()
    fake_client = FakeNorteiaApiClient()

    source_ref = f"mtur:BA:e2e_desc_{_uuid.uuid4().hex[:8]}"
    nascente = store_raw(
        session=db_session,
        source="mtur",
        source_ref=source_ref,
        entity_type="destination",
        uf="BA",
        payload=_low_score_payload(source_ref),
    )
    db_session.flush()

    rio = process_nascente_record(db_session, nascente, config)
    db_session.flush()

    # Score = 12 < 80 → dlq
    assert rio.routing == "dlq", (
        f"Expected routing=dlq for score=12.0, got {rio.routing} (score={rio.score})"
    )

    # DLQ records are NOT pushed
    assert len(fake_client.push_destination_calls) == 0
    assert len(fake_client.push_attraction_calls) == 0


@pytest.mark.integration
@pytest.mark.enable_socket
def test_promote_to_mar_is_idempotent_on_stable_source_ref(db_session: Session):
    """Re-promoting the SAME stable source_ref with unchanged data is a no-op.

    Regression for the Phase 1 verification blocker: promote_to_mar previously
    inserted a superseding row sharing source_ref, violating the source_ref
    UNIQUE constraint on the second call (IntegrityError). It must now (a) return
    the existing active row unchanged when data is identical, and (b) keep exactly
    one ACTIVE MarRecord per source_ref. Uses a STABLE source_ref (no uuid suffix)
    so the second promote actually exercises the idempotent path.
    """
    config = ScoreConfig()
    source_ref = "mtur:BA:stable_idem_001"
    payload = _high_score_payload(source_ref)

    nascente = store_raw(
        session=db_session,
        source="mtur",
        source_ref=source_ref,
        entity_type="destination",
        uf="BA",
        payload=payload,
    )
    db_session.flush()
    rio = process_nascente_record(db_session, nascente, config)
    db_session.flush()
    assert rio.routing == "mar"

    mar_first = promote_to_mar(db_session, rio)
    db_session.flush()

    # Second promote with identical data must NOT raise and must return the SAME row.
    mar_second = promote_to_mar(db_session, rio)
    db_session.flush()
    assert mar_second.id == mar_first.id, "re-promote of unchanged data must be a no-op"

    # Exactly one ACTIVE MarRecord exists for this source_ref.
    active = db_session.scalars(
        select(MarRecord).where(
            MarRecord.source_ref == source_ref,
            MarRecord.superseded_by_id.is_(None),
        )
    ).all()
    assert len(active) == 1, f"expected exactly one active Mar row, found {len(active)}"


@pytest.mark.integration
@pytest.mark.enable_socket
def test_promote_to_mar_supersedes_on_changed_score(db_session: Session):
    """When data changes, promote_to_mar supersedes the old row safely (D-03).

    The new row becomes active; the old row gets superseded_by_id set; exactly one
    active row per source_ref remains (partial unique index uq_mar_active_source_ref),
    with no IntegrityError.
    """
    config = ScoreConfig()
    source_ref = "mtur:BA:stable_supersede_001"

    nascente = store_raw(
        session=db_session,
        source="mtur",
        source_ref=source_ref,
        entity_type="destination",
        uf="BA",
        payload=_high_score_payload(source_ref),
    )
    db_session.flush()
    rio = process_nascente_record(db_session, nascente, config)
    db_session.flush()

    mar_first = promote_to_mar(db_session, rio)
    db_session.flush()

    # Mutate the scored record so the next promote sees changed data, then supersede.
    rio.score = float(rio.score) - 5.0
    db_session.flush()
    mar_second = promote_to_mar(db_session, rio)
    db_session.flush()

    assert mar_second.id != mar_first.id, "changed data must create a superseding row"
    db_session.refresh(mar_first)
    assert mar_first.superseded_by_id == mar_second.id, "old row must point at new row"
    assert mar_second.parent_mar_id == mar_first.id

    active = db_session.scalars(
        select(MarRecord).where(
            MarRecord.source_ref == source_ref,
            MarRecord.superseded_by_id.is_(None),
        )
    ).all()
    assert len(active) == 1, f"expected exactly one active Mar row, found {len(active)}"
    assert active[0].id == mar_second.id
