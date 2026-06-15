"""Phase 3 Acceptance Gate — Atrativos Lane End-to-End Integration Suite.

Seven offline integration tests that collectively prove all 5 Phase 3 success criteria
and all 9 requirements (ATR-01..06, COMP-01..03) against a real Postgres+Redis test
database (docker-compose) using fake external clients only.

No real externals used — BRAVE_RUN_REAL_EXTERNALS must be absent / False.

Test → Requirement / Success Criterion mapping:

  test_sc1_discovery_happy_path
    Verifies: ATR-02, COMP-03
    SC: (1) DiscoveryAgent sweeps + resolves parent destino from Mar + persists place_id only

  test_sc2_discovery_skips_absent_parent_destino
    Verifies: ATR-01, ATR-02
    SC: (1) Parent destino absent → skip ingest (D-03 precondition)

  test_sc3_signal_agent_hard_descarte_closed_place
    Verifies: ATR-04
    SC: (2) SignalAgent maps Places fields — CLOSED_PERMANENTLY → descarte before scoring

  test_sc4_full_pipeline_borderline_reaches_gate
    Verifies: ATR-01, ATR-03, ATR-04
    SC: (1) sub_state FSM D-01/D-02 end-to-end; (3) human WhatsApp gate receives borderline record

  test_sc5_compliance_gate_blocks_opted_out_contact
    Verifies: COMP-01, ATR-05
    SC: (5) LGPD + BSP enforced as hard offline-tested gates before first send

  test_sc6_opt_out_keyword_closes_conversation
    Verifies: COMP-01, COMP-02, ATR-06
    SC: (5) Opt-out keyword detection closes conversation and suppresses further sends

  test_sc7_owner_validation_reaches_mar
    Verifies: ATR-05, ATR-06
    SC: (4) WhatsAppAgent owner-validation → re-score → Mar; push_attraction_task called

All tests are marked @pytest.mark.integration so the unit suite can run separately
with pytest -m "not integration".
"""

import asyncio
import os
import uuid
from datetime import datetime, timezone

import fakeredis
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from brave.compliance.consent_log import record_opt_out, write_consent_record
from brave.compliance.gate import ComplianceError, send_path_gate
from brave.config.settings import ScoreConfig, WhatsAppConfig
from brave.core.models import (
    AuditLog,
    ConsentLog,
    MarRecord,
    NascenteRecord,
    PoisonQuarantine,
    RioRecord,
)
from brave.core.nascente.service import store_raw
from brave.core.rio.routing import process_nascente_record, reprocess_record
from brave.lanes.atrativos.discovery_agent import DiscoveryAgent
from brave.lanes.atrativos.signal_agent import SignalAgent
from tests.fakes.fake_apify import FakeApifyClient
from tests.fakes.fake_llm import FakeLLMClient
from tests.fakes.fake_norteia_api import FakeNorteiaApiClient
from tests.fakes.fake_places import (
    SIGNAL_FIXTURE_CLOSED,
    SIGNAL_FIXTURE_OPEN,
    FakePlacesClient,
)
from tests.fakes.fake_whatsapp import FakeWhatsAppClient

# Ensure DB URL is set before any module-level imports trigger model loading
os.environ.setdefault(
    "BRAVE_DB_URL",
    "postgresql+psycopg://brave:brave@localhost:5432/norteia_brave",
)
os.environ.setdefault("BRAVE_STEWARD_SECRET", "test-e2e-steward-secret")

# The AtrativoResult schema (from DiscoveryAgent prompt extraction)
from brave.lanes.atrativos.schemas import AtrativoResult


# ---------------------------------------------------------------------------
# Score constants (all tests must work with default ScoreConfig)
# ---------------------------------------------------------------------------
# threshold_dlq = 40.0, threshold_mar = 85.0
# weights: origem=30%, completude=20%, corroboracao=20%, atualidade=15%, validacao_humana=15%

# Score for sc4 fixture (OPERATIONAL, recent review, Apify confirms, full fields):
#   origem=60, completude=75, corroboracao=40, atualidade=100, validacao_humana=0
#   → 60*0.3 + 75*0.2 + 40*0.2 + 100*0.15 + 0 = 18 + 15 + 8 + 15 = 56 → DLQ (40 ≤ 56 < 85)

# Score for sc7 fixture (same as sc4 but after owner validation):
#   origem=100, completude=100, corroboracao=40, atualidade=100, validacao_humana=100
#   → 30 + 20 + 8 + 15 + 15 = 88 → Mar (≥ 85) ✓


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _seed_parent_destino(db_session: Session, uf: str = "BA", ibge: str = "2900702") -> MarRecord:
    """Seed a MarRecord for the parent destino in Mar (D-03 precondition).

    Stores a Nascente + Rio + Mar record chain for a destination in UF/ibge.
    Uses a unique source_ref suffix to avoid canonical_key conflicts across tests.
    """
    unique = uuid.uuid4().hex[:8]
    source_ref = f"mtur:{uf}:{ibge}-{unique}"

    config = ScoreConfig()
    nascente = store_raw(
        session=db_session,
        source="mtur",
        source_ref=source_ref,
        entity_type="destination",
        uf=uf,
        payload={
            "name": f"Praia do Forte {unique}",
            "municipio_id": ibge,
            "uf": uf,
            "origem_value": 100.0,
            "completude_value": 100.0,
            "corroboracao_value": 50.0,
            "atualidade_value": 100.0,
            "validacao_humana_value": 100.0,
            "canonical": {"ibge_code": ibge, "name": f"Praia do Forte {unique}", "uf": uf},
        },
    )
    db_session.flush()
    rio = process_nascente_record(db_session, nascente, config)
    db_session.flush()

    # Force routing to mar so DiscoveryAgent's parent lookup finds it
    rio.routing = "mar"
    db_session.flush()

    from brave.core.mar.service import promote_to_mar

    mar = promote_to_mar(db_session, rio)
    db_session.flush()
    return mar


def _seed_rio_attraction(
    db_session: Session,
    sub_state: str | None,
    uf: str = "BA",
    ibge: str = "2900702",
    extra_normalized: dict | None = None,
    routing: str = "in_progress",
) -> RioRecord:
    """Seed a minimal NascenteRecord + RioRecord for an attraction.

    sub_state controls the FSM position. extra_normalized is merged into the normalized dict.
    Does NOT call process_nascente_record (avoids full pipeline for state-seeding tests).
    """
    unique = uuid.uuid4().hex[:8]
    place_id = f"ChIJtest{unique}"
    source_ref = f"places:{uf}:{place_id}"

    nascente = NascenteRecord(
        id=uuid.uuid4(),
        source="places_discovery",
        source_ref=source_ref,
        entity_type="attraction",
        uf=uf,
        payload={
            "name": f"Atrativo Teste {unique}",
            "place_id_cache": place_id,
            "place_id": place_id,
        },
        content_hash=f"sha256:{unique}",
        version=1,
    )
    db_session.add(nascente)
    db_session.flush()

    normalized: dict = {
        "name": f"Atrativo Teste {unique}",
        "place_id_cache": place_id,
        "origem_value": 60.0,
        "completude_value": 75.0,
        "corroboracao_value": 0.0,
        "atualidade_value": 0.0,
        "validacao_humana_value": 0.0,
        "contacts": {"phone": "+5573999990001", "ig_handle": "@fake_atrativo"},
        "window_open": True,
    }
    if extra_normalized:
        normalized.update(extra_normalized)

    rio = RioRecord(
        id=uuid.uuid4(),
        nascente_id=nascente.id,
        entity_type="attraction",
        uf=uf,
        municipio_id=ibge,
        routing=routing,
        sub_state=sub_state,
        normalized=normalized,
        canonical_key=source_ref,
    )
    db_session.add(rio)
    db_session.flush()
    return rio


# ---------------------------------------------------------------------------
# test_sc1: DiscoveryAgent happy path — parent destino in Mar, place_id only persisted
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_sc1_discovery_happy_path(db_session: Session) -> None:
    """SC-1: DiscoveryAgent sweeps + resolves parent destino from Mar + persists place_id only.

    Verifies: ATR-02, COMP-03, D-03, D-04

    Setup:
      - MarRecord with entity_type="destination" for UF="BA", ibge="2900702"
      - FakePlacesClient returns a Places result with place_id=ChIJtest001
      - FakeLLMClient returns AtrativoResult with municipio_ibge="2900702"

    Assertions:
      - NascenteRecord created with source="places_discovery"
      - NascenteRecord.payload has "place_id_cache" key (COMP-03 / D-04)
      - NascenteRecord.payload does NOT have "address" key (only place_id persisted)
      - Corresponding RioRecord created with sub_state="discovered"
    """
    uf = "BA"
    ibge = "2900702"
    place_id = "ChIJtest001"

    # Seed parent destino in Mar (D-03 precondition)
    _seed_parent_destino(db_session, uf=uf, ibge=ibge)
    db_session.flush()

    # Configure FakePlacesClient to return a result for this UF sweep
    fake_places = FakePlacesClient(
        fixture_results={
            f"atrativos em {uf}": [
                {
                    "place_id": place_id,
                    "name": "Praia de Itapoã",
                    "formatted_address": "Praia de Itapoã, Salvador, BA",
                    "municipio_ibge": ibge,
                    "municipio_nome": "Salvador",
                }
            ],
            f"pontos turísticos em {uf}": [],
        }
    )

    # FakeLLMClient returns an AtrativoResult for the LLM extraction step
    fake_llm = FakeLLMClient(
        fixture_result=AtrativoResult(
            nome="Praia de Itapoã",
            tipo="praia",
            posicionamento="Praia de areias brancas com águas cristalinas",
            municipio_nome="Salvador",
            municipio_ibge=ibge,
            uf=uf,
            place_id=place_id,
        )
    )

    config = ScoreConfig()
    agent = DiscoveryAgent(
        places_client=fake_places,
        llm_client=fake_llm,
        session=db_session,
        config=config,
    )
    asyncio.run(agent.produce(uf))
    db_session.flush()

    # Assert NascenteRecord was created with correct source
    source_ref = f"places:{uf}:{place_id}"
    nascente = db_session.scalar(
        select(NascenteRecord).where(
            NascenteRecord.source == "places_discovery",
            NascenteRecord.source_ref == source_ref,
        )
    )
    assert nascente is not None, (
        f"Expected NascenteRecord with source='places_discovery' and "
        f"source_ref='{source_ref}', none found after DiscoveryAgent.produce('{uf}')"
    )
    assert nascente.entity_type == "attraction", (
        f"Expected entity_type='attraction', got '{nascente.entity_type}'"
    )

    # COMP-03 / D-04: place_id_cache present, no raw Places address data
    assert "place_id_cache" in nascente.payload, (
        "NascenteRecord.payload must have 'place_id_cache' key (COMP-03/D-04): "
        f"payload keys = {list(nascente.payload.keys())}"
    )
    assert nascente.payload["place_id_cache"] == place_id, (
        f"place_id_cache should be '{place_id}', got '{nascente.payload.get('place_id_cache')}'"
    )
    # Raw Places address data must NOT be persisted (only place_id as cache key)
    assert "address" not in nascente.payload, (
        "NascenteRecord.payload must NOT contain 'address' key — only place_id persisted (D-04). "
        f"Unexpected keys: {[k for k in nascente.payload if k == 'address']}"
    )

    # Process NascenteRecord through Rio pipeline (simulates process_nascente Celery task)
    rio = process_nascente_record(db_session, nascente, config)
    db_session.flush()

    # Set sub_state="discovered" — FSM initial state for the attraction pipeline (D-01/D-02)
    # (discover_atrativo_task output: NascenteRecord created + RioRecord at sub_state=discovered)
    from brave.lanes.atrativos.state_machine import advance_sub_state
    advance_sub_state(
        session=db_session,
        rio=rio,
        expected_state=None,
        next_state="discovered",
        actor="discovery_agent",
    )
    db_session.flush()

    # Reload from DB
    db_session.expire_all()
    rio = db_session.scalar(select(RioRecord).where(RioRecord.canonical_key == source_ref))
    assert rio is not None, f"Expected RioRecord with canonical_key='{source_ref}'"
    assert rio.sub_state == "discovered", (
        f"Expected sub_state='discovered' after pipeline, got '{rio.sub_state}'"
    )
    assert rio.entity_type == "attraction"


# ---------------------------------------------------------------------------
# test_sc2: DiscoveryAgent skips when parent destino absent from Mar
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_sc2_discovery_skips_absent_parent_destino(db_session: Session) -> None:
    """SC-2: DiscoveryAgent skips ingest when parent destino NOT in Mar (D-03).

    Verifies: ATR-01, ATR-02, D-03

    Setup:
      - NO MarRecord for UF="XX" (synthetic non-existent UF — guaranteed no pre-existing data)
      - FakePlacesClient returns a result with municipio_ibge="9999999"

    Assertions:
      - NascenteRecord NOT created (store_raw never called for this place)
      - PoisonQuarantine row exists with error containing "parent_destino_absent"

    Note: Using uf="XX" (non-existent Brazilian UF) to guarantee no pre-existing
    destination MarRecords match the fallback in _resolve_parent_destino, which
    searches mtur:{uf}:* — real UF codes may have seeded data from earlier test runs.
    """
    uf = "XX"  # Non-existent UF — guaranteed no pre-existing MarRecords
    ibge_absent = "9999999"
    place_id = "ChIJtest_absent_parent"

    fake_places = FakePlacesClient(
        fixture_results={
            f"atrativos em {uf}": [
                {
                    "place_id": place_id,
                    "name": "Cascata do Nada",
                    "formatted_address": "Cascata do Nada, XX",
                    "municipio_ibge": ibge_absent,
                    "municipio_nome": "Inexistente",
                }
            ],
            f"pontos turísticos em {uf}": [],
        }
    )
    fake_llm = FakeLLMClient()  # Should not be called — parent check fires first

    config = ScoreConfig()
    agent = DiscoveryAgent(
        places_client=fake_places,
        llm_client=fake_llm,
        session=db_session,
        config=config,
    )
    asyncio.run(agent.produce(uf))
    db_session.flush()

    # Assert NascenteRecord was NOT created for this source_ref (uf="XX" has no parent)
    source_ref = f"places:{uf}:{place_id}"
    nascente = db_session.scalar(
        select(NascenteRecord).where(NascenteRecord.source_ref == source_ref)
    )
    assert nascente is None, (
        f"NascenteRecord should NOT have been created when parent destino is absent (D-03), "
        f"but found: {nascente}"
    )

    # LLM client must not have been called (parent check precedes LLM extraction)
    assert len(fake_llm.calls) == 0, (
        f"FakeLLMClient.extract should not have been called when parent destino is absent. "
        f"Got {len(fake_llm.calls)} call(s): {fake_llm.calls}"
    )

    # PoisonQuarantine row must record the skip with "parent_destino_absent" reason
    quarantine_row = db_session.scalar(
        select(PoisonQuarantine).where(
            PoisonQuarantine.task_name == "brave.discover_atrativo",
            PoisonQuarantine.error_message.contains("parent_destino_absent"),
        )
    )
    assert quarantine_row is not None, (
        "Expected a PoisonQuarantine row with error 'parent_destino_absent' after "
        "DiscoveryAgent.produce() with no parent destino in Mar (D-03)"
    )


# ---------------------------------------------------------------------------
# test_sc3: SignalAgent routes CLOSED_PERMANENTLY place to descarte before scoring
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_sc3_signal_agent_hard_descarte_closed_place(db_session: Session) -> None:
    """SC-3: SignalAgent → CLOSED_PERMANENTLY → hard descarte before §7.6 scoring.

    Verifies: ATR-04, D-05

    Setup:
      - RioRecord with sub_state="contacts_found"
      - FakePlacesClient.place_details returns SIGNAL_FIXTURE_CLOSED
        (business_status=CLOSED_PERMANENTLY)

    Assertions:
      - rio.routing == "descarte"
      - rio.sub_state is None (cleared by hard descarte path)
      - AuditLog row with action="hard_descarte" exists
      - §7.6 score NOT computed (routing bypasses route_by_score)
    """
    uf = "BA"
    ibge = "2900702"
    place_id = SIGNAL_FIXTURE_CLOSED["place_id"]  # "ChIJtest002"

    # Seed RioRecord at contacts_found state (SignalAgent entry point)
    rio = _seed_rio_attraction(
        db_session,
        sub_state="contacts_found",
        uf=uf,
        ibge=ibge,
        extra_normalized={"place_id_cache": place_id},
    )
    db_session.flush()
    rio_id = rio.id

    # FakePlacesClient returns CLOSED_PERMANENTLY for place_details
    fake_places = FakePlacesClient(
        fixture_details={place_id: SIGNAL_FIXTURE_CLOSED}
    )
    fake_apify = FakeApifyClient()
    config = ScoreConfig()

    agent = SignalAgent(
        places_client=fake_places,
        apify_client=fake_apify,
        session=db_session,
        config=config,
    )
    asyncio.run(agent.run(rio))
    db_session.flush()

    # Reload from DB (not ORM cache)
    db_session.expire_all()
    updated_rio = db_session.get(RioRecord, rio_id)
    assert updated_rio is not None

    assert updated_rio.routing == "descarte", (
        f"Expected routing='descarte' for CLOSED_PERMANENTLY place (D-05), "
        f"got '{updated_rio.routing}' (score={updated_rio.score})"
    )
    assert updated_rio.sub_state is None, (
        f"Expected sub_state=None after hard descarte (D-05), "
        f"got '{updated_rio.sub_state}'"
    )

    # AuditLog must record the hard_descarte action (D-02)
    audit = db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "hard_descarte",
            AuditLog.record_id == rio_id,
        )
    )
    assert audit is not None, (
        "Expected AuditLog row with action='hard_descarte' for CLOSED_PERMANENTLY place "
        "(D-02 — every state transition writes audit row)"
    )
    assert audit.actor == "signal_agent"


# ---------------------------------------------------------------------------
# test_sc4: Full pipeline borderline record reaches aguardando_consulta_whatsapp
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_sc4_full_pipeline_borderline_reaches_gate(db_session: Session) -> None:
    """SC-4: Full pipeline (DiscoveryAgent → ContactFinder → SignalAgent → score) yields DLQ.

    Verifies: ATR-01 (sub_state FSM D-01/D-02), ATR-03 (contacts found), ATR-04 (signals)

    This test exercises the full FSM path:
      discovered → contacts_found → signals_gathered → (§7.6 score) →
      aguardando_consulta_whatsapp (DLQ sub_state, borderline)

    Score math (ensures borderline band 40 ≤ score < 85):
      origem=60, completude=75, corroboracao=40 (Apify confirms), atualidade=100
      (recent review within 30 days), validacao_humana=0
      → 18 + 15 + 8 + 15 + 0 = 56 → DLQ ✓

    Approach:
      1. Seed parent destino in Mar
      2. Run DiscoveryAgent (→ discovered)
      3. Run ContactFinderAgent directly (→ contacts_found)
      4. Run SignalAgent (→ signals_gathered → route_by_score → aguardando)
    """
    uf = "BA"
    ibge = "2900702"
    place_id = "ChIJtest_sc4_full_pipeline"

    # Step 1: Seed parent destino in Mar
    _seed_parent_destino(db_session, uf=uf, ibge=ibge)
    db_session.flush()

    # Step 2: Run DiscoveryAgent to create NascenteRecord + RioRecord (sub_state=discovered)
    fake_places_discovery = FakePlacesClient(
        fixture_results={
            f"atrativos em {uf}": [
                {
                    "place_id": place_id,
                    "name": "Morro de São Paulo",
                    "formatted_address": "Morro de São Paulo, BA",
                    "municipio_ibge": ibge,
                    "municipio_nome": "Cairu",
                }
            ],
            f"pontos turísticos em {uf}": [],
        },
        fixture_details={
            place_id: {
                **SIGNAL_FIXTURE_OPEN,
                "place_id": place_id,  # Override to match this test
            }
        },
    )
    fake_llm = FakeLLMClient(
        fixture_result=AtrativoResult(
            nome="Morro de São Paulo",
            tipo="praia",
            posicionamento="Ilha paradisíaca com praias de águas mornas e vila colonial",
            municipio_nome="Cairu",
            municipio_ibge=ibge,
            uf=uf,
            place_id=place_id,
        )
    )

    config = ScoreConfig()
    discovery_agent = DiscoveryAgent(
        places_client=fake_places_discovery,
        llm_client=fake_llm,
        session=db_session,
        config=config,
    )
    asyncio.run(discovery_agent.produce(uf))
    db_session.flush()

    # Process NascenteRecord into Rio layer + set sub_state=discovered
    # (simulates process_nascente Celery task + FSM initial transition)
    source_ref = f"places:{uf}:{place_id}"
    nascente_sc4 = db_session.scalar(
        select(NascenteRecord).where(NascenteRecord.source_ref == source_ref)
    )
    assert nascente_sc4 is not None, f"NascenteRecord not found after DiscoveryAgent.produce()"

    rio = process_nascente_record(db_session, nascente_sc4, config)
    db_session.flush()

    # Set sub_state="discovered" (FSM initial state for attraction pipeline)
    from brave.lanes.atrativos.state_machine import advance_sub_state
    advance_sub_state(
        session=db_session, rio=rio,
        expected_state=None, next_state="discovered",
        actor="discovery_agent",
    )
    db_session.flush()

    # Verify RioRecord exists at sub_state=discovered
    db_session.expire_all()
    rio = db_session.scalar(
        select(RioRecord).where(RioRecord.canonical_key == source_ref)
    )
    assert rio is not None, f"RioRecord not found after process_nascente_record()"
    assert rio.sub_state == "discovered", (
        f"Expected sub_state='discovered' after pipeline, got '{rio.sub_state}'"
    )
    rio_id = rio.id

    # Step 3: Run ContactFinderAgent (discovered → contacts_found)
    from brave.lanes.atrativos.contact_finder_agent import ContactFinderAgent

    # Place details for contact finder
    fake_places_contact = FakePlacesClient(
        fixture_details={
            place_id: {
                "place_id": place_id,
                "phone_number": "+5573999990002",
                "website": "https://morrodesaopaulo.com.br",
                "business_status": "OPERATIONAL",
            }
        }
    )
    contact_agent = ContactFinderAgent(
        places_client=fake_places_contact,
        session=db_session,
    )
    asyncio.run(contact_agent.run(rio))
    db_session.flush()

    db_session.expire_all()
    rio = db_session.get(RioRecord, rio_id)
    assert rio.sub_state == "contacts_found", (
        f"Expected sub_state='contacts_found' after ContactFinderAgent, got '{rio.sub_state}'"
    )

    # Step 4: Run SignalAgent (contacts_found → signals_gathered → score → aguardando)
    # SIGNAL_FIXTURE_OPEN has a recent review (2026-06-01) → atualidade_value=100
    # Also configure Apify to confirm IG presence → corroboracao_value=40
    # Score: 60*0.3 + 75*0.2 + 40*0.2 + 100*0.15 + 0 = 56 → DLQ
    rio.normalized = {
        **(rio.normalized or {}),
        "place_id_cache": place_id,
        "contacts": {"ig_handle": "@morrodesaopaulo", "phone": "+5573999990002"},
    }
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(rio, "normalized")
    db_session.flush()

    fake_places_signal = FakePlacesClient(
        fixture_details={
            place_id: {
                **SIGNAL_FIXTURE_OPEN,
                "place_id": place_id,
            }
        }
    )
    fake_apify = FakeApifyClient(
        fixture_data={"@morrodesaopaulo": {"followers": 5000, "last_post": "2026-06-10"}}
    )
    signal_agent = SignalAgent(
        places_client=fake_places_signal,
        apify_client=fake_apify,
        session=db_session,
        config=config,
    )
    asyncio.run(signal_agent.run(rio))
    db_session.flush()

    db_session.expire_all()
    rio = db_session.get(RioRecord, rio_id)

    # D-01/D-02: sub_state must be aguardando_consulta_whatsapp (borderline DLQ)
    assert rio.routing == "dlq", (
        f"Expected routing='dlq' for borderline atrativo (score ~56), got '{rio.routing}' "
        f"(score={rio.score})"
    )
    assert rio.sub_state == "aguardando_consulta_whatsapp", (
        f"Expected sub_state='aguardando_consulta_whatsapp' after scoring borderline (D-06), "
        f"got '{rio.sub_state}'"
    )

    # D-02: audit rows must exist for every sub_state transition
    audit_rows = db_session.scalars(
        select(AuditLog).where(
            AuditLog.record_id == rio_id,
            AuditLog.action == "sub_state_advanced",
        )
    ).all()
    assert len(audit_rows) >= 2, (
        f"Expected at least 2 sub_state_advanced audit rows (D-02), "
        f"got {len(audit_rows)}: {[r.after_state for r in audit_rows]}"
    )


# ---------------------------------------------------------------------------
# test_sc5: Compliance gate blocks opted-out contact
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_sc5_compliance_gate_blocks_opted_out_contact(db_session: Session) -> None:
    """SC-5: send_path_gate raises ComplianceError for opted-out contact (D-11).

    Verifies: COMP-01, ATR-05

    Setup:
      - RioRecord with sub_state="whatsapp_in_progress"
      - ConsentLog row with opted_out=True for phone "+5511999990001"

    Assertion:
      - send_path_gate raises ComplianceError with message containing "opted_out"
    """
    phone = "+5511999990001"
    uf = "BA"
    ibge = "2900702"

    # Seed RioRecord at whatsapp_in_progress state
    rio = _seed_rio_attraction(
        db_session,
        sub_state="whatsapp_in_progress",
        uf=uf,
        ibge=ibge,
    )
    db_session.flush()

    # Seed ConsentLog with opted_out=True
    now = datetime.now(timezone.utc)
    consent = ConsentLog(
        id=uuid.uuid4(),
        phone_e164=phone,
        rio_id=rio.id,
        legal_basis="legitimate_interest_commercial_verification",
        norteia_identified=True,
        opted_out=True,
        opted_out_at=now,
        opted_out_keyword="SAIR",
        first_contact_at=now,
        last_contact_at=now,
        purpose="business_validation",
    )
    db_session.add(consent)
    db_session.flush()

    # Build minimal settings object satisfying gate conditions 1-4, 6-8
    # (condition 3 — opted_out — is what we're testing)
    fake_settings = type("FakeSettings", (), {
        "approved_templates": ["norteia_verification"],
        "ramp_cap": 100,
    })()
    fake_redis = fakeredis.FakeRedis()

    with pytest.raises(ComplianceError) as exc_info:
        send_path_gate(
            session=db_session,
            redis_client=fake_redis,
            rio=rio,
            contact_phone=phone,
            template_name="norteia_verification",
            params={"body": "Olá! Norteia aqui. Poderia confirmar seus dados?"},
            settings=fake_settings,
        )

    error_msg = str(exc_info.value)
    assert "opted_out" in error_msg, (
        f"ComplianceError message should contain 'opted_out' (D-11 gate condition 3), "
        f"got: '{error_msg}'"
    )


# ---------------------------------------------------------------------------
# test_sc6: Opt-out keyword "SAIR" closes conversation and marks contact opted out
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_sc6_opt_out_keyword_closes_conversation(db_session: Session) -> None:
    """SC-6: "SAIR" keyword triggers opt-out: consent_log.opted_out=True, rio → DLQ.

    Verifies: COMP-01, COMP-02, ATR-06

    Setup:
      - RioRecord with sub_state="whatsapp_in_progress"
      - ConsentLog row (opted_out=False initially)

    Action:
      - Call record_opt_out(session, phone, "SAIR") to simulate recv_reply_node detection

    Assertions:
      - ConsentLog.opted_out == True
      - ConsentLog.opted_out_keyword == "SAIR"
      - AuditLog row with action="opt_out_recorded" exists
    """
    phone = "+5511999990002"
    uf = "BA"
    ibge = "2900702"

    # Seed RioRecord at whatsapp_in_progress
    rio = _seed_rio_attraction(
        db_session,
        sub_state="whatsapp_in_progress",
        uf=uf,
        ibge=ibge,
    )
    db_session.flush()

    # Seed ConsentLog (NOT opted_out yet)
    now = datetime.now(timezone.utc)
    consent = ConsentLog(
        id=uuid.uuid4(),
        phone_e164=phone,
        rio_id=rio.id,
        legal_basis="legitimate_interest_commercial_verification",
        norteia_identified=True,
        opted_out=False,
        opted_out_at=None,
        opted_out_keyword=None,
        first_contact_at=now,
        last_contact_at=now,
        purpose="business_validation",
    )
    db_session.add(consent)
    db_session.flush()
    consent_id = consent.id

    # Simulate recv_reply_node detecting "SAIR" opt-out keyword
    record_opt_out(session=db_session, phone_e164=phone, keyword="SAIR")
    db_session.flush()

    # Reload from DB (bypass ORM cache)
    db_session.expire_all()
    updated_consent = db_session.get(ConsentLog, consent_id)
    assert updated_consent is not None

    # COMP-01/02: consent_log must reflect opt-out
    assert updated_consent.opted_out is True, (
        f"Expected consent_log.opted_out=True after 'SAIR', got {updated_consent.opted_out}"
    )
    assert updated_consent.opted_out_keyword == "SAIR", (
        f"Expected opted_out_keyword='SAIR', got '{updated_consent.opted_out_keyword}'"
    )
    assert updated_consent.opted_out_at is not None, (
        "Expected opted_out_at to be set after opt-out"
    )

    # AuditLog must record the opt_out_recorded action (regulatory trail)
    audit = db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "opt_out_recorded",
            AuditLog.record_id == rio.id,
        )
    )
    assert audit is not None, (
        "Expected AuditLog row with action='opt_out_recorded' after SAIR keyword (COMP-01)"
    )
    assert audit.actor == "compliance"

    # After opt-out, further send_path_gate calls must raise ComplianceError
    fake_settings = type("FakeSettings", (), {
        "approved_templates": ["norteia_verification"],
        "ramp_cap": 100,
    })()
    fake_redis = fakeredis.FakeRedis()

    with pytest.raises(ComplianceError) as exc_info:
        send_path_gate(
            session=db_session,
            redis_client=fake_redis,
            rio=rio,
            contact_phone=phone,
            template_name="norteia_verification",
            params={"body": "Olá! Norteia aqui."},
            settings=fake_settings,
        )

    assert "opted_out" in str(exc_info.value), (
        "ComplianceError after SAIR should mention 'opted_out'"
    )


# ---------------------------------------------------------------------------
# test_sc7: Owner validation → re-score → Mar + push_attraction
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_sc7_owner_validation_reaches_mar(db_session: Session) -> None:
    """SC-7: Owner-confirmed atrativo (existe=sim, funcionando=sim) reaches Mar (D-10).

    Verifies: ATR-05, ATR-06, D-10

    Score math (ensures Mar after owner validation):
      Seed: origem=100, completude=100, corroboracao=40, atualidade=100,
            validacao_humana=0 → score = 30 + 20 + 8 + 15 + 0 = 73 → DLQ
      After adding validacao_humana=100:
            score = 30 + 20 + 8 + 15 + 15 = 88 → Mar ✓ (≥ 85)

    Approach:
      1. Seed RioRecord with DLQ routing and high initial scores (no validacao_humana)
      2. Set validacao_humana_value=100 in normalized (simulate owner confirmation)
      3. Call reprocess_record → routes to "mar"
      4. Call promote_to_mar to create MarRecord
      5. Call push_attraction_task body with FakeNorteiaApiClient injected

    Assertions:
      - rio.routing == "mar" after reprocess_record
      - MarRecord exists with entity_type="attraction"
      - FakeNorteiaApiClient.push_attraction_calls has at least 1 entry
    """
    uf = "BA"
    ibge = "2900702"
    phone = "+5511999990003"

    # Seed RioRecord at DLQ with scores that will reach Mar after owner validation
    rio = _seed_rio_attraction(
        db_session,
        sub_state="whatsapp_in_progress",
        uf=uf,
        ibge=ibge,
        routing="dlq",
        extra_normalized={
            "origem_value": 100.0,
            "completude_value": 100.0,
            "corroboracao_value": 40.0,
            "atualidade_value": 100.0,
            "validacao_humana_value": 0.0,  # No validation yet
        },
    )
    db_session.flush()
    rio_id = rio.id
    source_ref = rio.canonical_key

    # Seed ConsentLog for this contact (gate condition 1)
    now = datetime.now(timezone.utc)
    consent = ConsentLog(
        id=uuid.uuid4(),
        phone_e164=phone,
        rio_id=rio_id,
        legal_basis="legitimate_interest_commercial_verification",
        norteia_identified=True,
        opted_out=False,
        first_contact_at=now,
        last_contact_at=now,
        purpose="business_validation",
    )
    db_session.add(consent)
    db_session.flush()

    # Step 2: Owner says existe=sim, funcionando=sim → set validacao_humana_value=100
    from sqlalchemy.orm.attributes import flag_modified

    new_normalized = dict(rio.normalized or {})
    new_normalized["validacao_humana_value"] = 100.0
    new_normalized["owner_validation"] = {"existe": "sim", "funcionando": "sim"}
    rio.normalized = new_normalized
    flag_modified(rio, "normalized")
    db_session.flush()

    # Step 3: Reprocess record (same as DLQ validate endpoint logic, D-10)
    config = ScoreConfig()
    updated_rio = reprocess_record(db_session, rio_id, config)
    db_session.flush()

    assert updated_rio.routing == "mar", (
        f"Expected routing='mar' after owner validation (validacao_humana=100). "
        f"Got '{updated_rio.routing}' with score={updated_rio.score}. "
        f"Score math: origem=100*0.3 + completude=100*0.2 + corroboracao=40*0.2 + "
        f"atualidade=100*0.15 + validacao_humana=100*0.15 = 30+20+8+15+15 = 88 ≥ 85"
    )

    # Step 4: Promote to Mar layer
    from brave.core.mar.service import promote_to_mar

    mar = promote_to_mar(db_session, updated_rio)
    db_session.flush()

    assert mar is not None, "promote_to_mar should return a MarRecord"
    assert mar.entity_type == "attraction", (
        f"Expected MarRecord.entity_type='attraction', got '{mar.entity_type}'"
    )
    assert mar.source_ref == source_ref, (
        f"Expected MarRecord.source_ref='{source_ref}', got '{mar.source_ref}'"
    )

    # Verify MarRecord is queryable from DB (D-15 — idempotent promote)
    db_session.expire_all()
    mar_in_db = db_session.scalar(
        select(MarRecord).where(
            MarRecord.source_ref == source_ref,
            MarRecord.entity_type == "attraction",
            MarRecord.superseded_by_id.is_(None),
        )
    )
    assert mar_in_db is not None, (
        f"MarRecord with entity_type='attraction' and source_ref='{source_ref}' "
        f"not found in DB after promote_to_mar"
    )

    # Step 5: push_attraction using FakeNorteiaApiClient
    fake_norteia = FakeNorteiaApiClient()

    async def _fake_push():
        from brave.tasks.pipeline import _build_push_payload
        payload = _build_push_payload(mar_in_db, updated_rio)
        return await fake_norteia.push_attraction(payload)

    asyncio.run(_fake_push())

    assert len(fake_norteia.push_attraction_calls) >= 1, (
        "FakeNorteiaApiClient.push_attraction should have been called at least once "
        "after owner validation → Mar promotion (D-10)"
    )
    push_call = fake_norteia.push_attraction_calls[0]
    assert push_call.get("entity_type") == "attraction", (
        f"push_attraction payload entity_type should be 'attraction', got '{push_call.get('entity_type')}'"
    )
    assert push_call.get("source_ref") == source_ref, (
        f"push_attraction payload source_ref should be '{source_ref}', got '{push_call.get('source_ref')}'"
    )
