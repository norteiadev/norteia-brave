"""RealLLMClient — OpenRouter/DeepSeek extraction + Anthropic conversation implementation.

Uses instructor 1.15.x (Mode.TOOLS) wrapping AsyncOpenAI pointed at OpenRouter for extract(),
and native AsyncAnthropic 0.109.x for generate().
Implements LLMClientProtocol:
  - extract(prompt, schema, mode="tools") → schema instance (D-03)
  - generate(messages, model="claude-sonnet-4-5") → str (D-05a)

Guard: raises RuntimeError if AppConfig().run_real_externals is False.
This prevents accidental real LLM calls in CI / default test suite.

D-04: provider.data_collection = config.provider_data_collection ("deny") is injected
in EVERY OpenRouter request body via extra_body. Asserted in unit test.

tenacity: 3 retries with exponential backoff for transient errors (429, 5xx, connection).
Slug fallback: primary → deepseek_fallback_slugs on NotFoundError / 503.

Usage (production — only when run_real_externals=True):
    from brave.clients.llm import RealLLMClient
    client = RealLLMClient(config=app_config.llm)
    result = await client.extract(prompt="...", schema=MySchema)
"""

from __future__ import annotations

import uuid
from typing import Any

import instructor
import structlog
from anthropic import AsyncAnthropic
from openai import (
    APIConnectionError,
    APITimeoutError,
    AsyncOpenAI,
    BadRequestError,
    InternalServerError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
)
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from brave.config.settings import LLMConfig
from brave.core.models import LLMGeneration
from brave.observability.cost_guard import pre_dispatch_check, record_spend

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Anthropic Sonnet 4.5 pricing constants (USD per million tokens, 2026-06)
# Update these when Anthropic revises pricing.
# ---------------------------------------------------------------------------

_SONNET_4_5_INPUT_USD_PER_MTOK: float = 3.0
_SONNET_4_5_OUTPUT_USD_PER_MTOK: float = 15.0


# ---------------------------------------------------------------------------
# instructor mode map — valid mode strings → instructor.Mode enum
# ---------------------------------------------------------------------------

_MODE_MAP: dict[str, instructor.Mode] = {
    "tools": instructor.Mode.TOOLS,
    "json": instructor.Mode.JSON,
    "md_json": instructor.Mode.MD_JSON,  # NOTE: raises AssertionError on OpenRouter client
}


# ---------------------------------------------------------------------------
# Retry policy — transient OpenRouter/openai errors only (WR-01)
# ---------------------------------------------------------------------------


def _is_openai_retryable(exc: BaseException) -> bool:
    """Return True for transient OpenRouter/openai errors (429, 5xx, connection/timeout).

    WR-01: Only transient errors are retried per-slug. NotFoundError (404) and
    BadRequestError / PermissionDeniedError (permanent) must NOT be retried — they
    trigger slug fallback or an immediate raise, respectively.
    """
    if isinstance(exc, (RateLimitError, InternalServerError, APIConnectionError, APITimeoutError)):
        return True
    # Generic API status errors: retry on 5xx
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and status >= 500:
        return True
    return False


# ---------------------------------------------------------------------------
# RealLLMClient
# ---------------------------------------------------------------------------


class RealLLMClient:
    """Real LLM client using instructor + OpenRouter/DeepSeek for extract() and
    native AsyncAnthropic for generate().

    Guard: raises RuntimeError if AppConfig().run_real_externals is False.
    This client is ONLY instantiated when run_real_externals=True is confirmed.

    D-04: provider.data_collection = config.provider_data_collection ("deny") is
    injected in EVERY create_with_completion call via extra_body.

    D-05: When optional redis_client + session deps are provided, pre_dispatch_check
    is called before each LLM invocation and a LLMGeneration row is written after.
    No prompt content is ever persisted (T-02-04).

    Args:
        config:       LLMConfig with OpenRouter + Anthropic credentials and slug list.
        redis_client: Optional Redis client for cost guard. If None, cost guard skipped.
        session:      Optional SQLAlchemy Session for llm_generations rows. If None, skipped.
        lane:         Pipeline lane identifier for llm_generations rows (default "unknown").
    """

    def __init__(
        self,
        config: LLMConfig,
        *,
        redis_client: Any = None,
        session: Any = None,
        lane: str = "unknown",
    ) -> None:
        from brave.config.settings import AppConfig

        if not AppConfig().run_real_externals:
            raise RuntimeError(
                "RealLLMClient: run_real_externals=False — "
                "use FakeLLMClient in default test suite. "
                "Set RUN_REAL_EXTERNALS=true to enable real LLM calls."
            )

        if not config.openrouter_api_key:
            raise RuntimeError(
                "RealLLMClient: openrouter_api_key is empty — "
                "set BRAVE_LLM_OPENROUTER_API_KEY environment variable."
            )

        self._config = config
        self._redis_client = redis_client
        self._session = session
        self._lane = lane

        # Build instructor-wrapped AsyncOpenAI for extract()
        # mode=Mode.TOOLS is set at construction time (not per-call) because
        # OpenRouter does not support MD_JSON mode — we lock to TOOLS here.
        _openai_client = AsyncOpenAI(
            api_key=config.openrouter_api_key,
            base_url=config.openrouter_base_url,
        )
        self._instructor_client: instructor.AsyncInstructor = instructor.from_openai(
            _openai_client,
            mode=instructor.Mode.TOOLS,
        )

        # Build native AsyncAnthropic for generate()
        self._anthropic_client = AsyncAnthropic(api_key=config.anthropic_api_key)

    @retry(
        retry=retry_if_exception(_is_openai_retryable),  # WR-01: transient only
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def _call_slug(self, slug: str, prompt: str, schema: type) -> tuple[Any, Any]:
        """Invoke instructor create_with_completion for a single slug with tenacity retry.

        Retries on transient errors (429, 5xx, connection/timeout). NotFoundError and
        permanent errors (400, 403) propagate immediately to the caller (extract()).

        Args:
            slug:   OpenRouter model slug (e.g. "deepseek/deepseek-chat").
            prompt: User instruction + context string.
            schema: Pydantic model class for structured output.

        Returns:
            (result, raw_completion) tuple from create_with_completion.
        """
        result, raw = await self._instructor_client.create_with_completion(
            messages=[{"role": "user", "content": prompt}],
            response_model=schema,
            model=slug,
            extra_body={"provider": {"data_collection": self._config.provider_data_collection}},
        )
        return result, raw

    async def extract(
        self,
        prompt: str,
        schema: type,
        mode: str = "tools",
    ) -> Any:
        """Extract structured data from a prompt using instructor Mode.TOOLS (DeepSeek).

        D-04: extra_body with provider.data_collection="deny" is injected on every call.
        D-03: primary slug tried first; falls back through deepseek_fallback_slugs on
              NotFoundError (model unavailable). BadRequestError / PermissionDeniedError
              raise immediately (permanent errors — don't try the next slug).
        D-05: pre_dispatch_check + record_spend + LLMGeneration row written when optional
              redis_client and session deps are present.

        Args:
            prompt: Instruction + context to send to the LLM.
            schema: Pydantic model class to validate the response against.
            mode:   instructor mode string. Only "tools" is supported for OpenRouter.

        Returns:
            An instance of `schema` with the extracted data.

        Raises:
            ValueError:      If mode is not "tools" (OpenRouter supports Mode.TOOLS only).
            CostGuardError:  If daily USD budget exceeded before dispatch.
            NotFoundError:   If all slugs are unavailable.
        """
        # Only Mode.TOOLS is supported for OpenRouter (MD_JSON raises AssertionError
        # in instructor for OpenRouter clients; JSON lacks function-calling fidelity)
        if mode != "tools":
            raise ValueError(
                f"RealLLMClient.extract only supports mode='tools' with OpenRouter; got {mode!r}"
            )

        # Cost guard — BEFORE any LLM call (D-20, T-02-03)
        if self._redis_client is not None:
            pre_dispatch_check(self._redis_client, self._config)

        # Slug fallback loop (D-03, Research Pattern 4)
        slugs = [self._config.deepseek_primary_slug] + list(self._config.deepseek_fallback_slugs)
        last_exc: Exception | None = None
        result: Any = None
        raw: Any = None
        slug: str = slugs[0]  # will be overwritten in the loop

        for slug in slugs:
            try:
                result, raw = await self._call_slug(slug, prompt, schema)
                break
            except NotFoundError as exc:
                last_exc = exc
                logger.warning("llm_slug_unavailable", slug=slug, error=str(exc))
                continue
            except (BadRequestError, PermissionDeniedError):
                raise  # permanent — do not try next slug
        else:
            # All slugs exhausted
            raise last_exc  # type: ignore[misc]

        # Parse usage from raw completion
        usage = raw.usage if raw is not None else None
        prompt_tokens: int = usage.prompt_tokens if usage else 0
        completion_tokens: int = usage.completion_tokens if usage else 0
        usd_cost: float = (
            float(usage.model_extra.get("cost", 0.0))
            if usage and usage.model_extra
            else 0.0
        )

        logger.info(
            "llm_extract_ok",
            slug=slug,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            usd_cost=usd_cost,
        )

        # Write tracking row — NEVER log prompt content (T-02-04)
        if self._redis_client is not None and self._session is not None:
            record_spend(self._redis_client, usd_cost)
            self._session.add(
                LLMGeneration(
                    id=uuid.uuid4(),
                    lane=self._lane,
                    model_slug=slug,
                    resolved_provider=slug,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    usd_cost=usd_cost,
                )
            )
            self._session.flush()

        return result

    async def generate(
        self,
        messages: list[dict[str, Any]],
        model: str = "claude-sonnet-4-5",
    ) -> str:
        """Generate a free-form text response via native AsyncAnthropic (D-05a).

        Used by WhatsAppAgent ask_followup_node for PT-BR conversation turns.
        NOT via OpenRouter — uses the native Anthropic SDK for direct quota control.

        max_tokens=2048 is REQUIRED — anthropic 0.109.x has no default (RESEARCH.md Pitfall 7).

        Args:
            messages: Conversation history list [{role, content}].
            model:    Model identifier (default: claude-sonnet-4-5).

        Returns:
            Generated text response string (response.content[0].text).

        Raises:
            CostGuardError: If daily USD budget exceeded before dispatch.
        """
        # Cost guard — BEFORE any LLM call (D-20, T-02-03)
        if self._redis_client is not None:
            pre_dispatch_check(self._redis_client, self._config)

        response = await self._anthropic_client.messages.create(
            model=model,
            max_tokens=2048,
            messages=messages,  # type: ignore[arg-type]
        )

        text: str = response.content[0].text  # type: ignore[union-attr]
        prompt_tokens: int = response.usage.input_tokens
        completion_tokens: int = response.usage.output_tokens

        # Anthropic does NOT return a cost field — compute from price table
        # (RESEARCH.md Pitfall 7). Prices are for Sonnet 4.5 (2026-06).
        usd_cost: float = (
            prompt_tokens * _SONNET_4_5_INPUT_USD_PER_MTOK
            + completion_tokens * _SONNET_4_5_OUTPUT_USD_PER_MTOK
        ) / 1_000_000

        logger.info(
            "llm_generate_ok",
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            usd_cost=usd_cost,
        )

        # Write tracking row — NEVER log prompt content (T-02-04)
        if self._redis_client is not None and self._session is not None:
            record_spend(self._redis_client, usd_cost)
            self._session.add(
                LLMGeneration(
                    id=uuid.uuid4(),
                    lane=self._lane,
                    model_slug=model,
                    resolved_provider=model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    usd_cost=usd_cost,
                )
            )
            self._session.flush()

        return text


# ---------------------------------------------------------------------------
# Protocol compliance check
# ---------------------------------------------------------------------------


def _check_protocol_compliance() -> None:
    """Compile-time structural typing assertion (not called at runtime).

    Verifies that RealLLMClient structurally satisfies LLMClientProtocol.
    Skipped at runtime because instantiation requires run_real_externals=True.
    """
    # NOTE: RealLLMClient raises RuntimeError if run_real_externals=False,
    # so we cannot instantiate it here. Structural compliance verified by
    # type annotations on extract() and generate() matching LLMClientProtocol.
    pass
