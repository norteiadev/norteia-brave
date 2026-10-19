"""COPYWRITER_SYSTEM / _build_context invariants (shared by inline, cascade and batch)."""

from brave.domains.places.copywriter import COPYWRITER_SYSTEM, _build_context


def test_system_prompt_is_tool_agnostic_and_dash_free():
    # The cascade model has no tool: asking it to search invites "conforme pesquisei".
    assert "ferramenta" not in COPYWRITER_SYSTEM
    # The only dash characters allowed are the ones the prohibition itself names.
    dashed = [line for line in COPYWRITER_SYSTEM.splitlines() if "—" in line or "–" in line]
    assert len(dashed) == 1 and "travessão" in dashed[0]
    assert "NUNCA invente informações para vender" in COPYWRITER_SYSTEM
    assert "LEITURA FLUIDA" in COPYWRITER_SYSTEM and "20 palavras" in COPYWRITER_SYSTEM


def test_context_is_dash_free():
    user = _build_context("Praia da Pipa", "Tibau do Sul", "RN", {}, fontes="trecho")
    assert "—" not in user and "–" not in user
