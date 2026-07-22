"""Unit tests for TourismCopywriter — atrativo description generation.

100% offline: FakeLLMClient (records the call, returns a canned string) — no network,
no Anthropic call. Covers the deterministic em-dash strip guard and the offline path.
"""

from __future__ import annotations

import pytest

from brave.lanes.atrativos.copywriter import (
    WEB_SEARCH_TOOL,
    TourismCopywriter,
    _strip_dashes,
)
from tests.fakes.fake_llm import FakeLLMClient


def test_strip_dashes_removes_em_and_en_dash() -> None:
    assert _strip_dashes("A praia — larga — e calma.") == "A praia, larga, e calma."
    assert "—" not in _strip_dashes("Vista—mar")
    assert "–" not in _strip_dashes("Vista–mar")
    # Bare word with no dash is untouched (modulo whitespace collapse).
    assert _strip_dashes("Praia de Camburi") == "Praia de Camburi"


@pytest.mark.asyncio
async def test_write_strips_dashes_from_llm_output() -> None:
    """An LLM output containing an em-dash is written stripped (guard, not prompt)."""
    fake = FakeLLMClient(generate_result="Camburi é a orla — extensa — de Vitória.")
    cw = TourismCopywriter(fake, model="claude-sonnet-4-5")
    out = await cw.write("Praia de Camburi", "Vitória", "ES", {"types": ["beach"]})
    assert out == "Camburi é a orla, extensa, de Vitória."
    # System prompt + web_search tool were passed to generate().
    call = fake.generate_calls[-1]
    assert call["system"] is not None and "Norteia" in call["system"]
    assert call["tools"] == [WEB_SEARCH_TOOL]


@pytest.mark.asyncio
async def test_write_no_web_search_when_disabled() -> None:
    fake = FakeLLMClient(generate_result="Prosa curta.")
    cw = TourismCopywriter(fake, enable_web_search=False)
    await cw.write("X", "Vitória", "ES", {})
    assert fake.generate_calls[-1]["tools"] is None


@pytest.mark.asyncio
async def test_write_empty_result_returns_none() -> None:
    fake = FakeLLMClient(generate_result="   ")
    cw = TourismCopywriter(fake)
    assert await cw.write("X", "Vitória", "ES", {}) is None


@pytest.mark.asyncio
async def test_write_missing_name_returns_none() -> None:
    fake = FakeLLMClient(generate_result="anything")
    cw = TourismCopywriter(fake)
    assert await cw.write("", "Vitória", "ES", {}) is None
    assert fake.generate_calls == []  # never called the LLM
