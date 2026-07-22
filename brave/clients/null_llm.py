"""In-package offline LLMClient stub (production-safe).

Used when AppConfig.run_real_externals is False (local dev, CI, any environment
without OpenRouter/Anthropic credentials). Returns None for extract() and a fixed
canned PT-BR string for generate() so pipeline tasks no-op cleanly without any
LLM call or network I/O.

This lives in brave/ (NOT tests/) so production code never imports from the test
tree. Tests use tests/fakes/FakeLLMClient for call-recording assertions.
"""

from __future__ import annotations

from typing import Any


class NullLLMClient:
    """No-network LLMClient stub (structural protocol match).

    Returns None for extract() and a fixed PT-BR canned string for generate() —
    no OpenRouter/Anthropic call, no network I/O.
    Safe to use when RUN_REAL_EXTERNALS is unset/false.

    The generate() default matches FakeLLMClient's default generate_result
    verbatim so offline behaviour is consistent across test and production-offline
    code paths.
    """

    async def extract(
        self,
        prompt: str,
        schema: type,
        mode: str = "tools",
    ) -> Any:
        """Return None — offline stub performs no LLM extraction.

        Args:
            prompt: Instruction + context (ignored).
            schema: Pydantic model class (ignored).
            mode: instructor mode string (ignored).

        Returns:
            None.
        """
        return None

    async def generate(
        self,
        messages: list[dict[str, Any]],
        model: str = "claude-sonnet-4-5",
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> str:
        """Return canned PT-BR string — offline stub performs no generation.

        Matches FakeLLMClient's default generate_result so offline behaviour is
        consistent between production-offline and test code paths.

        Args:
            messages: Conversation history (ignored).
            model: Model identifier (ignored).
            system: Optional system prompt (ignored).
            tools: Optional tool defs (ignored — offline stub never searches).

        Returns:
            Fixed canned PT-BR follow-up string.
        """
        return "Olá! Da Norteia. Poderia confirmar mais detalhes?"


# Structural type check: NullLLMClient must satisfy LLMClientProtocol
def _check_protocol_compliance() -> None:
    """Compile-time structural typing assertion (not called at runtime)."""
    from brave.clients.base import LLMClientProtocol

    _client: LLMClientProtocol = NullLLMClient()  # noqa: F841
