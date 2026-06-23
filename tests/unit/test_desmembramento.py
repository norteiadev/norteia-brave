"""DesmembramentoAgent unit tests (DEST-03, TEST-02).

All tests offline — FakeLLMClient + FakeMturClient; no real OpenRouter/Mtur calls.

Tests cover:
  - Happy path: valid LLM result → NascenteRecord with source="desm", origem=40
  - Quarantine path: LLM raises → PoisonQuarantine row; no NascenteRecord written
  - Empty destinos: LLM returns empty list → no error, no NascenteRecords
  - Skip non-Oferta-Principal: Complementar municipality → zero LLM calls
"""

import pytest

from brave.config.settings import ScoreConfig
from brave.lanes.destinos.desmembramento import DesmembramentoAgent
from brave.lanes.destinos.schemas import DesmembramentoResult, DestinoItem
from tests.fakes.fake_llm import FakeLLMClient
from tests.fakes.fake_mtur import FakeMturClient


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def score_config() -> ScoreConfig:
    """ScoreConfig with default §7.6 weights."""
    return ScoreConfig()


@pytest.fixture
def porto_seguro_fixture() -> list[dict]:
    """Standard Porto Seguro BA fixture — Oferta Principal."""
    return [
        {
            "ibge_code": "2927408",
            "name": "Porto Seguro",
            "categoria": "Oferta Principal",
            "uf": "BA",
        }
    ]


# ---------------------------------------------------------------------------
# Happy path test
# ---------------------------------------------------------------------------


async def test_desmembramento_agent_happy_path(
    db_session, score_config, porto_seguro_fixture
):
    """DesmembramentoAgent writes destinos to Nascente with source='desm' and origem=40.

    FakeLLMClient returns a valid DesmembramentoResult with one DestinoItem.
    After produce("BA"):
      - One NascenteRecord exists with source="desm"
      - Record has origem_value=40.0 and source_note="LLM-generated, pending validation"
      - FakeLLMClient.calls has one entry (one LLM call was made)
    """
    from brave.core.models import NascenteRecord

    fake_result = DesmembramentoResult(
        municipio_ibge="2927408",
        municipio_nome="Porto Seguro",
        destinos=[
            DestinoItem(
                nome="Trancoso",
                tipo="vila",
                posicionamento="Vila histórica com ruas de pedra e quadrado central",
            ),
        ],
    )
    fake_llm = FakeLLMClient(fixture_result=fake_result)
    fake_mtur = FakeMturClient(fixtures=porto_seguro_fixture)

    agent = DesmembramentoAgent(
        llm_client=fake_llm,
        mtur_client=fake_mtur,
        session=db_session,
        config=score_config,
    )
    await agent.produce("BA")

    # Exactly one NascenteRecord with source="desm"
    records = (
        db_session.query(NascenteRecord)
        .filter_by(source="desm")
        .all()
    )
    assert len(records) == 1, f"Expected 1 nascente record, got {len(records)}"

    record = records[0]
    assert record.payload["origem_value"] == 40.0
    assert record.payload["source_note"] == "LLM-generated, pending validation"
    assert record.payload["validacao_humana_value"] == 0.0
    assert record.source_ref.startswith("desm:BA:2927408:")
    assert record.entity_type == "destination"
    assert record.uf == "BA"

    # One LLM call was made
    assert len(fake_llm.calls) == 1
    assert fake_llm.calls[0]["schema"] == "DesmembramentoResult"
    assert fake_llm.calls[0]["mode"] == "tools"


# ---------------------------------------------------------------------------
# Offline NullLLMClient path test (regression: result-is-None must not crash)
# ---------------------------------------------------------------------------


async def test_desmembramento_offline_null_llm_does_not_crash(
    db_session, score_config, porto_seguro_fixture
):
    """NullLLMClient.extract() returns None — produce() must skip, not crash.

    Regression (quick task 260623-jw3): accessing result.destinos on a None result
    raised AttributeError out of produce(), which rolled back the whole sweep_uf
    transaction and silently discarded the already-written Mtur seed. produce()
    must instead treat None as "no sub-destinos extracted":
      - does NOT raise
      - writes zero source="desm" NascenteRecords
    """
    from brave.clients.null_llm import NullLLMClient
    from brave.core.models import NascenteRecord

    agent = DesmembramentoAgent(
        llm_client=NullLLMClient(),
        mtur_client=FakeMturClient(fixtures=porto_seguro_fixture),
        session=db_session,
        config=score_config,
    )

    # Must not raise even though the município is Oferta Principal (extract is called).
    await agent.produce("BA")

    records = db_session.query(NascenteRecord).filter_by(source="desm").all()
    assert records == [], f"Expected no desm records, got {len(records)}"


# ---------------------------------------------------------------------------
# Quarantine path test
# ---------------------------------------------------------------------------


async def test_desmembramento_agent_malformed_output_quarantined(
    db_session, score_config, porto_seguro_fixture
):
    """FakeLLMClient raises → PoisonQuarantine created; no NascenteRecord written.

    When instructor retry is exhausted (or any exception from extract), the agent
    must:
      - Write a PoisonQuarantine row with task_name="brave.desmembramento"
      - NOT write any NascenteRecord with source="desm"
      - NOT propagate the exception (produce completes normally)
    """
    from brave.core.models import NascenteRecord, PoisonQuarantine

    fake_llm = FakeLLMClient(
        raise_on_call=ValueError("instructor retry exhausted")
    )
    fake_mtur = FakeMturClient(fixtures=porto_seguro_fixture)

    agent = DesmembramentoAgent(
        llm_client=fake_llm,
        mtur_client=fake_mtur,
        session=db_session,
        config=score_config,
    )

    # Must not raise — exception is caught and quarantined
    await agent.produce("BA")

    # No NascenteRecord with source="desm" was created
    desm_records = (
        db_session.query(NascenteRecord)
        .filter_by(source="desm")
        .all()
    )
    assert len(desm_records) == 0, (
        f"Expected 0 nascente records with source='desm', got {len(desm_records)}"
    )

    # One PoisonQuarantine row with task_name="brave.desmembramento"
    quarantine_rows = (
        db_session.query(PoisonQuarantine)
        .filter_by(task_name="brave.desmembramento")
        .all()
    )
    assert len(quarantine_rows) == 1, (
        f"Expected 1 quarantine row, got {len(quarantine_rows)}"
    )
    assert "instructor retry exhausted" in quarantine_rows[0].error_message
    assert quarantine_rows[0].nascente_id is None  # No nascente ID for failed LLM call
    # Payload carries municipio context for debugging
    assert quarantine_rows[0].payload.get("municipio_ibge") == "2927408"


# ---------------------------------------------------------------------------
# Empty destinos test
# ---------------------------------------------------------------------------


async def test_desmembramento_agent_empty_destinos_skips(
    db_session, score_config, porto_seguro_fixture
):
    """FakeLLMClient returns DesmembramentoResult with empty destinos list.

    produce("BA") must:
      - Complete without error
      - Write zero NascenteRecords (nothing to store)
    """
    from brave.core.models import NascenteRecord

    fake_result = DesmembramentoResult(
        municipio_ibge="2927408",
        municipio_nome="Porto Seguro",
        destinos=[],  # Empty list — LLM found no sub-destinos
    )
    fake_llm = FakeLLMClient(fixture_result=fake_result)
    fake_mtur = FakeMturClient(fixtures=porto_seguro_fixture)

    agent = DesmembramentoAgent(
        llm_client=fake_llm,
        mtur_client=fake_mtur,
        session=db_session,
        config=score_config,
    )

    # Must not raise
    await agent.produce("BA")

    # No NascenteRecords created
    records = (
        db_session.query(NascenteRecord)
        .filter_by(source="desm")
        .all()
    )
    assert len(records) == 0, (
        f"Expected 0 nascente records for empty destinos, got {len(records)}"
    )


# ---------------------------------------------------------------------------
# Skip non-Oferta-Principal test
# ---------------------------------------------------------------------------


async def test_desmembramento_agent_skips_non_oferta_principal(
    db_session, score_config
):
    """FakeMturClient returns a Complementar municipality → zero LLM calls.

    The agent must filter to Oferta Principal only. A Complementar (or Apoio)
    municipality must not trigger any LLM call.
    """
    complementar_fixture = [
        {
            "ibge_code": "2900702",
            "name": "Alagoinhas",
            "categoria": "Complementar",
            "uf": "BA",
        }
    ]
    fake_llm = FakeLLMClient(fixture_result=None)
    fake_mtur = FakeMturClient(fixtures=complementar_fixture)

    agent = DesmembramentoAgent(
        llm_client=fake_llm,
        mtur_client=fake_mtur,
        session=db_session,
        config=score_config,
    )

    await agent.produce("BA")

    # Zero LLM calls — Complementar municipalities are filtered out
    assert len(fake_llm.calls) == 0, (
        f"Expected 0 LLM calls for non-Oferta-Principal, got {len(fake_llm.calls)}"
    )
