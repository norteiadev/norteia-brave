"""Opt-in real-network smoke test for RealLLMClient.

Requires both RUN_REAL_EXTERNALS=true AND BRAVE_LLM_OPENROUTER_API_KEY to be set.
Skipped automatically in CI (keyless). This test makes a real network call to OpenRouter.

Run manually with:
    RUN_REAL_EXTERNALS=true BRAVE_LLM_OPENROUTER_API_KEY=<key> \\
        .venv/bin/python -m pytest tests/integration/test_real_llm_smoke.py -v -s
"""

import os

import pytest

_HAS_OPENROUTER_KEY = bool(os.environ.get("BRAVE_LLM_OPENROUTER_API_KEY"))
_HAS_REAL_EXTERNALS = os.environ.get("RUN_REAL_EXTERNALS", "").lower() in ("1", "true", "yes")
_SMOKE_ENABLED = _HAS_OPENROUTER_KEY and _HAS_REAL_EXTERNALS


@pytest.mark.skipif(
    not _SMOKE_ENABLED,
    reason="BRAVE_LLM_OPENROUTER_API_KEY + RUN_REAL_EXTERNALS not set — opt-in only",
)
async def test_smoke_extract_real_openrouter():
    """Real extract() call to OpenRouter/DeepSeek. Opt-in only.

    Sends a minimal prompt and validates that a structured Pydantic response
    is returned. Does NOT use redis_client or session (pure transport mode —
    acceptable for smoke; full tracking wiring is verified by offline tests
    and the pipeline call sites).
    """
    from pydantic import BaseModel

    from brave.clients.llm import RealLLMClient
    from brave.config.settings import LLMConfig

    class _Ping(BaseModel):
        answer: str

    config = LLMConfig()
    client = RealLLMClient(config=config)
    result = await client.extract(prompt="Reply with answer='pong'", schema=_Ping)

    assert isinstance(result, _Ping), f"Expected _Ping instance, got {type(result)}"
    assert result.answer, f"Expected non-empty answer, got {result.answer!r}"
