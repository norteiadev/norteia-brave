"""Unit tests for DescriptionEnrichmentAgent (description-enrichment lane).

100% offline: FakeMelhoresDestinosClient + FakeLLMClient + MagicMock rio/session.
Template: test_signal_agent.py.

Covers:
  - Idempotency guard (sub_state != signals_gathered → no-op)
  - match + rewrite ok → descricao_editorial = Norteia-voice text; completude 75 → 90
  - match + rewrite FAILS → keeps the scraped text (never propagates the error)
  - no MD match → floor kept (no descricao_editorial), completude unchanged, still advances
  - completude below the ceiling is NOT bumped
  - route_by_score is invoked (re-score); dlq → sub_state cleared (bounce)
  - borderline re-score can promote dlq → mar (sub_state stays description_enriched)
"""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from brave.config.settings import ScoreConfig
from brave.shared.ibge_distritos import IbgeDistrito, load_distritos_csv
from tests.fakes.fake_llm import FakeLLMClient
from tests.fakes.fake_melhores_destinos import FakeMelhoresDestinosClient

PRAIA_URL = "https://guia.melhoresdestinos.com.br/praia-do-forte-54-249-l.html"
_MODULE = "brave.lanes.atrativos.description"

# Real IBGE DTB CSV — the golden Arraial d'Ajuda (292530307) / Porto Seguro (2925303) case.
_CSV_PATH = Path(__file__).resolve().parents[3] / "data" / "ibge" / "ibge_distritos.csv"


@pytest.fixture(scope="module")
def distritos() -> list[IbgeDistrito]:
    """Load the real ibge_distritos.csv once for the distrito-breadcrumb tests."""
    records = load_distritos_csv(_CSV_PATH)
    assert records, "ibge_distritos.csv loaded empty"
    return records


def _make_rio(sub_state: str = "signals_gathered", completude: float = 75.0) -> MagicMock:
    rio = MagicMock()
    rio.id = uuid.uuid4()
    rio.sub_state = sub_state
    rio.routing = "mar"
    rio.dlq_reason = None
    rio.entity_type = "attraction"
    rio.uf = "BA"
    rio.normalized = {
        "name": "Praia do Forte",
        "municipio": "Mata de São João",
        "origem_value": 60.0,
        "completude_value": completude,
        "corroboracao_value": 0.0,
        "atualidade_value": 100.0,
        "validacao_humana_value": 0.0,
    }
    return rio


def _make_session() -> MagicMock:
    session = MagicMock()
    session.flush.return_value = None
    return session


def _agent(md, llm, session, config=None, md_config=None, distritos=None):
    from brave.lanes.atrativos.description import DescriptionEnrichmentAgent

    return DescriptionEnrichmentAgent(
        md_client=md,
        llm_client=llm,
        session=session,
        config=config,
        md_config=md_config,
        distritos=distritos,
    )


@pytest.mark.asyncio
async def test_idempotency_guard_wrong_state() -> None:
    """sub_state != signals_gathered → immediate no-op (no MD/LLM calls)."""
    md = FakeMelhoresDestinosClient()
    llm = FakeLLMClient()
    rio = _make_rio(sub_state="contacts_found")

    agent = _agent(md, llm, _make_session())
    await agent.run(rio)

    assert md.find_calls == []
    assert rio.sub_state == "contacts_found"


@pytest.mark.asyncio
async def test_match_and_rewrite_ok_bumps_completude() -> None:
    """MD match + rewrite ok → Norteia-voice description; completude 75 → 90."""
    md = FakeMelhoresDestinosClient(
        url_by_name={"Praia do Forte": PRAIA_URL},
        description_by_url={PRAIA_URL: "Prosa editorial raspada do MD."},
    )
    llm = FakeLLMClient(generate_result="Descrição na voz da Norteia.")
    rio = _make_rio()

    agent = _agent(md, llm, _make_session())
    with patch(f"{_MODULE}.write_audit"), patch(f"{_MODULE}.route_by_score") as route:
        await agent.run(rio)

    assert rio.normalized["descricao_editorial"] == "Descrição na voz da Norteia."
    assert rio.normalized["completude_value"] == 90.0
    assert rio.sub_state == "description_enriched"
    assert route.called
    assert llm.generate_calls, "LLM rewrite must be attempted on a scraped description"


@pytest.mark.asyncio
async def test_rewrite_failure_keeps_scraped() -> None:
    """LLM rewrite exception → keep the scraped text (error never propagates)."""
    md = FakeMelhoresDestinosClient(
        url_by_name={"Praia do Forte": PRAIA_URL},
        description_by_url={PRAIA_URL: "Prosa editorial raspada do MD."},
    )
    llm = FakeLLMClient(raise_on_call=RuntimeError("llm down"))
    rio = _make_rio()

    agent = _agent(md, llm, _make_session())
    with patch(f"{_MODULE}.write_audit"), patch(f"{_MODULE}.route_by_score"):
        await agent.run(rio)  # must NOT raise

    assert rio.normalized["descricao_editorial"] == "Prosa editorial raspada do MD."
    assert rio.normalized["completude_value"] == 90.0
    assert rio.sub_state == "description_enriched"


@pytest.mark.asyncio
async def test_no_match_keeps_floor() -> None:
    """No MD page → no descricao_editorial, completude unchanged, still advances."""
    md = FakeMelhoresDestinosClient(url_by_name={})  # miss
    llm = FakeLLMClient()
    rio = _make_rio()

    agent = _agent(md, llm, _make_session())
    with patch(f"{_MODULE}.write_audit"), patch(f"{_MODULE}.route_by_score") as route:
        await agent.run(rio)

    assert "descricao_editorial" not in rio.normalized
    assert rio.normalized["completude_value"] == 75.0
    assert rio.sub_state == "description_enriched"
    assert route.called  # re-score still runs (no-op on unchanged inputs)
    assert llm.generate_calls == []


@pytest.mark.asyncio
async def test_below_ceiling_not_bumped() -> None:
    """A record below the 75 ceiling keeps its completude even with a description."""
    md = FakeMelhoresDestinosClient(
        url_by_name={"Praia do Forte": PRAIA_URL},
        description_by_url={PRAIA_URL: "Prosa."},
    )
    llm = FakeLLMClient(generate_result="Voz Norteia.")
    rio = _make_rio(completude=50.0)

    agent = _agent(md, llm, _make_session())
    with patch(f"{_MODULE}.write_audit"), patch(f"{_MODULE}.route_by_score"):
        await agent.run(rio)

    assert rio.normalized["descricao_editorial"] == "Voz Norteia."
    assert rio.normalized["completude_value"] == 50.0  # not bumped to 90


@pytest.mark.asyncio
async def test_md_fetch_exception_keeps_floor() -> None:
    """An exception from the MD scraper (e.g. 404/403 on fetch) degrades to the floor.

    Regression: fetch_description can raise on a non-retryable HTTP status; the agent
    must swallow it, keep the floor, and still advance to description_enriched (never
    strand the record at signals_gathered).
    """
    from unittest.mock import AsyncMock

    md = FakeMelhoresDestinosClient(url_by_name={"Praia do Forte": PRAIA_URL})
    md.fetch_description = AsyncMock(side_effect=RuntimeError("HTTP 404"))
    llm = FakeLLMClient(generate_result="unused")
    rio = _make_rio()

    agent = _agent(md, llm, _make_session())
    with patch(f"{_MODULE}.write_audit"), patch(f"{_MODULE}.route_by_score"):
        await agent.run(rio)  # must NOT raise

    assert "descricao_editorial" not in rio.normalized
    assert rio.normalized["completude_value"] == 75.0
    assert rio.sub_state == "description_enriched"
    assert llm.generate_calls == []  # no rewrite attempted


@pytest.mark.asyncio
async def test_dlq_bounce_clears_sub_state() -> None:
    """route_by_score → dlq → sub_state cleared to None (bounce back to DLQ)."""
    md = FakeMelhoresDestinosClient(url_by_name={})  # keep floor
    llm = FakeLLMClient()
    rio = _make_rio()

    def _route_to_dlq(session, rio_record, config):
        rio_record.routing = "dlq"
        return rio_record

    agent = _agent(md, llm, _make_session())
    with patch(f"{_MODULE}.write_audit"), patch(f"{_MODULE}.route_by_score", _route_to_dlq):
        await agent.run(rio)

    assert rio.routing == "dlq"
    assert rio.sub_state is None


@pytest.mark.asyncio
async def test_rescore_promotes_borderline_to_mar() -> None:
    """Real re-score: completude 75→90 crosses a threshold → mar (stays description_enriched).

    Uses a completude-only ScoreConfig so the degrau alone flips the routing.
    """
    md = FakeMelhoresDestinosClient(
        url_by_name={"Praia do Forte": PRAIA_URL},
        description_by_url={PRAIA_URL: "Prosa."},
    )
    llm = FakeLLMClient(generate_result="Voz Norteia.")
    rio = _make_rio()

    # completude-only weights: score == completude_value. 90 >= 80 → mar.
    config = ScoreConfig(
        weight_origem=0.0,
        weight_completude=100.0,
        weight_corroboracao=0.0,
        weight_atualidade=0.0,
        weight_validacao_humana=0.0,
        threshold_mar=80.0,
    )

    agent = _agent(md, llm, _make_session(), config=config)
    with patch(f"{_MODULE}.write_audit"):
        await agent.run(rio)

    assert rio.normalized["completude_value"] == 90.0
    assert rio.routing == "mar"
    assert rio.sub_state == "description_enriched"


# ---------------------------------------------------------------------------
# Distrito relation via the MD breadcrumb <Place> (IBGE DTB, scoped to município)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_distrito_breadcrumb_resolves_and_writes_relation(distritos) -> None:
    """Breadcrumb <Place> "Arraial d'Ajuda" + município 2925303 → distrito relation written.

    Golden: place "Arraial d'Ajuda" cross-referenced against Porto Seguro's distritos
    resolves to distrito 292530307, and the agent writes distrito_code +
    distrito_municipio_ibge (the parent município relation) + distrito_source.
    """
    md = FakeMelhoresDestinosClient(
        url_by_name={"Praia do Forte": PRAIA_URL},
        description_by_url={PRAIA_URL: "Prosa."},
        place_by_url={PRAIA_URL: "Arraial d'Ajuda"},
    )
    llm = FakeLLMClient(generate_result="Voz Norteia.")
    rio = _make_rio()
    rio.municipio_id = "2925303"  # Porto Seguro — scopes the distrito candidate set.

    agent = _agent(md, llm, _make_session(), distritos=distritos)
    with patch(f"{_MODULE}.write_audit"), patch(f"{_MODULE}.route_by_score"):
        await agent.run(rio)

    assert rio.normalized["distrito_name"] == "Arraial D'Ajuda"
    assert rio.normalized["distrito_code"] == "292530307"
    assert rio.normalized["distrito_municipio_ibge"] == "2925303"
    assert rio.normalized["distrito_source"] == "md_breadcrumb"
    assert rio.normalized["subdistrito_name"] is None
    assert rio.normalized["subdistrito_code"] is None
    assert md.breadcrumb_calls == [PRAIA_URL]


@pytest.mark.asyncio
async def test_distrito_seat_place_writes_no_relation(distritos) -> None:
    """Breadcrumb <Place> == the parent município seat ("Porto Seguro") → no distrito keys.

    The seat guard (resolve_distrito_place) drops a match whose name folds to the
    município's own name — assigning the seat adds no finer-than-município signal, so
    every distrito_* key stays absent (floor preserved).
    """
    md = FakeMelhoresDestinosClient(
        url_by_name={"Praia do Forte": PRAIA_URL},
        description_by_url={PRAIA_URL: "Prosa."},
        place_by_url={PRAIA_URL: "Porto Seguro"},
    )
    llm = FakeLLMClient(generate_result="Voz Norteia.")
    rio = _make_rio()
    rio.municipio_id = "2925303"

    agent = _agent(md, llm, _make_session(), distritos=distritos)
    with patch(f"{_MODULE}.write_audit"), patch(f"{_MODULE}.route_by_score"):
        await agent.run(rio)

    for key in (
        "distrito_name",
        "distrito_code",
        "distrito_municipio_ibge",
        "distrito_source",
        "subdistrito_name",
        "subdistrito_code",
    ):
        assert key not in rio.normalized
