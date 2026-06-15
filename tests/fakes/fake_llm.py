"""Fake LLM client for offline testing.

FakeLLMClient implements LLMClientProtocol (structural typing, D-09).
Used in unit and integration tests to avoid real LLM calls.

Usage:
    from tests.fakes.fake_llm import FakeLLMClient

    fake = FakeLLMClient(fixture_result=MySchema(field="value"))
    result = await fake.extract(prompt="...", schema=MySchema)
    assert fake.calls[0]["prompt"] == "..."
"""

from typing import Any

from brave.clients.base import LLMClientProtocol


class FakeLLMClient:
    """Fake LLM client that returns a pre-configured fixture result.

    Structurally satisfies LLMClientProtocol (D-09), including both
    extract() (DeepSeek/instructor) and generate() (Sonnet PT-BR, D-08).
    Records every call to .calls for test assertions.
    Optionally raises an exception to test error paths.
    """

    def __init__(
        self,
        fixture_result: Any = None,
        raise_on_call: Exception | None = None,
        generate_result: str = "Olá! Da Norteia. Poderia confirmar mais detalhes?",
    ) -> None:
        """Initialize with a fixture result to return on extract().

        Args:
            fixture_result:   Value to return from extract() calls.
            raise_on_call:    If set, raise this exception instead of returning.
            generate_result:  String to return from generate() calls.
        """
        self._fixture_result = fixture_result
        self._raise_on_call = raise_on_call
        self._generate_result = generate_result
        self.calls: list[dict[str, Any]] = []
        self.generate_calls: list[dict[str, Any]] = []

    async def extract(
        self,
        prompt: str,
        schema: type,
        mode: str = "tools",
    ) -> Any:
        """Record the call and return the fixture result (or raise).

        Args:
            prompt: Instruction + context string.
            schema: Pydantic model class for output validation.
            mode:   instructor mode string (recorded but not used by fake).

        Returns:
            fixture_result passed at construction time.

        Raises:
            raise_on_call if set at construction time.
        """
        self.calls.append({
            "prompt": prompt,
            "schema": schema.__name__ if hasattr(schema, "__name__") else str(schema),
            "mode": mode,
        })
        if self._raise_on_call is not None:
            raise self._raise_on_call
        return self._fixture_result

    async def generate(
        self,
        messages: list[dict[str, Any]],
        model: str = "claude-sonnet-4-5",
    ) -> str:
        """Record the generate call and return the fixture generate_result.

        Simulates Sonnet PT-BR conversation turn generation (D-08).

        Args:
            messages: Conversation history [{role, content}].
            model:    Model identifier (recorded but not used by fake).

        Returns:
            generate_result passed at construction time (default PT-BR follow-up).
        """
        self.generate_calls.append({"messages": messages, "model": model})
        if self._raise_on_call is not None:
            raise self._raise_on_call
        return self._generate_result


# Structural type check: FakeLLMClient must satisfy LLMClientProtocol
def _check_protocol_compliance() -> None:
    """Compile-time structural typing assertion (not called at runtime)."""
    _client: LLMClientProtocol = FakeLLMClient()  # noqa: F841
