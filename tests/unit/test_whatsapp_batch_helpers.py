"""Offline unit tests for Phase F manual DLQ→WhatsApp helpers.

Covers (no DB, no network, CI-keyless):
  - dlq._is_whatsapp_eligible: eligible iff NO horário AND NO preço.
  - number_discovery.discover_number: LLM → phone; Null/None/error → None.

The endpoint + discovery task DB behaviour is covered by the integration suite
(tests/integration/test_dlq_whatsapp_batch.py).
"""

from __future__ import annotations

import pytest

from brave.api.routers.dlq import _is_whatsapp_eligible
from brave.clients.null_llm import NullLLMClient
from brave.lanes.atrativos.number_discovery import discover_number
from brave.lanes.atrativos.schemas import WhatsAppNumberDiscovery
from tests.fakes.fake_llm import FakeLLMClient

# ---------------------------------------------------------------------------
# _is_whatsapp_eligible — server-side 422 gate (no horário AND no preço)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "normalized",
    [
        None,
        {},
        {"name": "Praia X"},
        {"weekday_text": []},  # empty Places hours = no horário
        {"owner_horarios": ""},  # empty string = no horário
        {"owner_valor": ""},  # empty string = no preço
        {"contact": {"whatsapp_candidate": "+5573*****01"}},  # candidate is not horário/preço
    ],
)
def test_eligible_when_no_horario_and_no_preco(normalized) -> None:
    assert _is_whatsapp_eligible(normalized) is True


@pytest.mark.parametrize(
    "normalized",
    [
        {"weekday_text": ["Monday: 9:00 AM – 5:00 PM"]},  # Places hours present
        {"owner_horarios": "Seg-Sex 9h-17h"},  # owner-confirmed schedule
        {"owner_valor": "R$ 10"},  # owner-confirmed price
        {"weekday_text": ["Mon: 9-5"], "owner_valor": "R$ 10"},  # both
    ],
)
def test_ineligible_when_horario_or_preco_present(normalized) -> None:
    assert _is_whatsapp_eligible(normalized) is False


# ---------------------------------------------------------------------------
# discover_number — LLM extract → phone; offline / error → None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discover_number_returns_phone_from_llm() -> None:
    fake = FakeLLMClient(
        fixture_result=WhatsAppNumberDiscovery(phone="+5573999990001", confidence=0.9)
    )
    got = await discover_number(fake, name="Praia do Teste", uf="BA")
    assert got == "+5573999990001"
    # The prompt was actually sent to the LLM (extract recorded a call).
    assert fake.calls and fake.calls[0]["schema"] == "WhatsAppNumberDiscovery"


@pytest.mark.asyncio
async def test_discover_number_null_client_returns_none() -> None:
    # Offline default: NullLLMClient.extract → None → no number found.
    got = await discover_number(NullLLMClient(), name="Praia do Teste", uf="BA")
    assert got is None


@pytest.mark.asyncio
async def test_discover_number_none_phone_field_returns_none() -> None:
    fake = FakeLLMClient(fixture_result=WhatsAppNumberDiscovery(phone=None))
    got = await discover_number(fake, name="X", uf="BA")
    assert got is None


@pytest.mark.asyncio
async def test_discover_number_accepts_dict_result() -> None:
    class _DictLLM:
        async def extract(self, prompt, schema, mode="tools"):
            return {"phone": "+5573999990002"}

        async def generate(self, messages, model="claude-sonnet-4-5"):
            return ""

    got = await discover_number(_DictLLM(), name="X", uf="BA")
    assert got == "+5573999990002"


@pytest.mark.asyncio
async def test_discover_number_swallows_llm_error() -> None:
    fake = FakeLLMClient(raise_on_call=RuntimeError("boom"))
    got = await discover_number(fake, name="X", uf="BA")
    assert got is None
