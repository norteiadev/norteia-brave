"""RealLLMClient — OpenRouter/DeepSeek extraction + Anthropic/OpenRouter/Gemini generation.

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

import httpx
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

from brave.clients.tavily import _is_retryable as _is_httpx_retryable
from brave.clients.tavily import _wait as _httpx_wait
from brave.config.settings import LLMConfig
from brave.core.models import LLMGeneration
from brave.observability.cost_guard import pre_dispatch_check, record_spend
from brave.shared.exceptions import PermanentError

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Anthropic Sonnet 4.5 pricing constants (USD per million tokens, 2026-06)
# Update these when Anthropic revises pricing.
# ---------------------------------------------------------------------------

_SONNET_4_5_INPUT_USD_PER_MTOK: float = 3.0
_SONNET_4_5_OUTPUT_USD_PER_MTOK: float = 15.0

# (input, output) USD per MTok for generate(). It used to price EVERY model at Sonnet rates —
# harmless while only Sonnet called it, a 3x over-count in record_spend (and a budget guard
# tripping 3x early) once the cascade copywriter runs Haiku. An unknown slug falls back to
# Sonnet: over-counting trips the guard early, under-counting would let it overspend.
_PRICES_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-sonnet-4-5": (_SONNET_4_5_INPUT_USD_PER_MTOK, _SONNET_4_5_OUTPUT_USD_PER_MTOK),
    "claude-haiku-4-5": (1.0, 5.0),
}

# Prompt-caching multipliers on the input rate. Nothing sends cache_control today, so both
# counters are always 0 — priced anyway because a cache hit MOVES tokens out of input_tokens,
# so without these the day someone enables caching we would under-count instead of measure it.
_CACHE_READ_MULTIPLIER: float = 0.1
_CACHE_WRITE_MULTIPLIER: float = 1.25

# The server-side web_search tool bills $10 per 1,000 searches ON TOP of tokens. On an atrativo
# description that fee is ~30% of the real bill, so omitting it made record_spend — and the daily
# budget guard that reads it — structurally low.
_WEB_SEARCH_USD_PER_REQUEST: float = 0.01

# Google AI Studio (Gemini direct) returns no cost, so it is priced here: (input, output,
# cache_read) USD per MTok by (model, BILLED tier), ai.google.dev/gemini-api/docs/pricing on
# 2026-09-15. A model missing from the table raises before dispatch — failing loud beats a
# cost guard counting wrong. Thinking tokens bill at the output rate.
_GEMINI_PRICES_USD_PER_MTOK: dict[tuple[str, str], tuple[float, float, float]] = {
    ("gemini-2.5-flash", "standard"): (0.30, 2.50, 0.03),
    ("gemini-2.5-flash", "flex"): (0.15, 1.25, 0.03),
    ("gemini-2.5-flash-lite", "standard"): (0.10, 0.40, 0.01),
    ("gemini-2.5-flash-lite", "flex"): (0.05, 0.20, 0.01),
}

# Flex sheds load with these; the same body is re-sent at the standard tier right away.
_GEMINI_FLEX_FALLBACK_STATUS: frozenset[int] = frozenset({429, 503})
_GEMINI_TIMEOUT_S: float = 60.0


def gemini_is_priced(model: str) -> bool:
    """True when generate() can price ``model`` (the pipeline refuses to build otherwise)."""
    return (model, "standard") in _GEMINI_PRICES_USD_PER_MTOK


def _gemini_cost(model: str, tier: str, usage: dict[str, Any]) -> float:
    price_in, price_out, price_cache = _GEMINI_PRICES_USD_PER_MTOK[(model, tier)]
    prompt = int(usage.get("promptTokenCount") or 0)
    cached = int(usage.get("cachedContentTokenCount") or 0)
    output = int(usage.get("candidatesTokenCount") or 0) + int(usage.get("thoughtsTokenCount") or 0)
    return ((prompt - cached) * price_in + cached * price_cache + output * price_out) / 1_000_000


# Bound on pause_turn resumes when a server-side tool (web_search) is enabled — a backstop
# so a runaway server-side loop can never spin generate() forever.
_MAX_TOOL_TURNS: int = 4


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


def _usage_int(obj: Any, field: str) -> int:
    """Read an optional integer usage counter, defaulting to 0.

    Anthropic omits these counters entirely when the feature never fired (no server tool ran,
    no cache_control sent), and test doubles rarely set them — anything non-int reads as 0.
    """
    value = getattr(obj, field, 0)
    return value if isinstance(value, int) else 0


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
        # Kept raw too: generate() sends OpenRouter slugs (e.g. "google/gemini-2.5-flash")
        # through plain chat completions — no response_model, so no instructor.
        self._openrouter = AsyncOpenAI(
            api_key=config.openrouter_api_key,
            base_url=config.openrouter_base_url,
        )
        self._instructor_client: instructor.AsyncInstructor = instructor.from_openai(
            self._openrouter,
            mode=instructor.Mode.TOOLS,
        )

        # Build native AsyncAnthropic for generate()
        self._anthropic_client = AsyncAnthropic(api_key=config.anthropic_api_key)

        # Gemini direct: built on the first gemini-* call, so lanes that never write with
        # Gemini construct this client exactly as before.
        self._gemini_http: httpx.AsyncClient | None = None

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
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> str:
        """Generate a free-form text response (D-05a).

        Used by WhatsAppAgent ask_followup_node for PT-BR conversation turns, and by
        TourismCopywriter (atrativo descriptions) with the server-side web_search tool.
        Claude slugs go through the native Anthropic SDK (direct quota control); a
        "vendor/model" slug (the cascade's Gemini) goes to OpenRouter, see _generate_openrouter.

        max_tokens=2048 is REQUIRED — anthropic 0.109.x has no default (RESEARCH.md Pitfall 7).

        Args:
            messages: Conversation history list [{role, content}].
            model:    Model identifier (default: claude-sonnet-4-5).
            system:   Optional system prompt (copywriter persona / guards).
            tools:    Optional tool defs, e.g. the server-side web_search tool. When passed,
                      the server-side sampling loop may stop_reason=="pause_turn"; we resume
                      up to _MAX_TOOL_TURNS times, then extract text from all text blocks.

        Returns:
            Generated text (concatenated text blocks; empty string if none).

        Raises:
            CostGuardError: If daily USD budget exceeded before dispatch.
            PermanentError: OpenRouter/Gemini reply cut short (finish reason not stop), a
                            prompt blocked by Gemini, or an empty Gemini key.
        """
        # Cost guard — BEFORE any LLM call (D-20, T-02-03)
        if self._redis_client is not None:
            pre_dispatch_check(self._redis_client, self._config)

        # An OpenRouter slug ("vendor/model") never reaches Anthropic; a bare gemini-* slug
        # goes to Google AI Studio direct.
        if "/" in model:
            return await self._generate_openrouter(messages, model, system=system, tools=tools)
        if model.startswith("gemini-"):
            return await self._generate_gemini(messages, model, system=system, tools=tools)

        create_kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": 2048,
            "messages": list(messages),
        }
        if system is not None:
            create_kwargs["system"] = system
        if tools:
            create_kwargs["tools"] = tools

        response = await self._anthropic_client.messages.create(**create_kwargs)  # type: ignore[arg-type]

        # Server-side tools (web_search) can pause the turn at the 10-iteration cap; resume
        # by re-sending the assistant content until it ends naturally (bounded — never loop).
        prompt_tokens = response.usage.input_tokens
        completion_tokens = response.usage.output_tokens
        cache_read_tokens = _usage_int(response.usage, "cache_read_input_tokens")
        cache_write_tokens = _usage_int(response.usage, "cache_creation_input_tokens")
        # Each resume re-runs searches, so the search fee accumulates per turn like the tokens.
        web_searches = _usage_int(
            getattr(response.usage, "server_tool_use", None), "web_search_requests"
        )
        _turns = 0
        while response.stop_reason == "pause_turn" and _turns < _MAX_TOOL_TURNS:
            _turns += 1
            messages = list(messages) + [{"role": "assistant", "content": response.content}]
            create_kwargs["messages"] = messages
            response = await self._anthropic_client.messages.create(**create_kwargs)  # type: ignore[arg-type]
            prompt_tokens += response.usage.input_tokens
            completion_tokens += response.usage.output_tokens
            cache_read_tokens += _usage_int(response.usage, "cache_read_input_tokens")
            cache_write_tokens += _usage_int(response.usage, "cache_creation_input_tokens")
            web_searches += _usage_int(
                getattr(response.usage, "server_tool_use", None), "web_search_requests"
            )

        # Extract text from all text-type blocks (skips server_tool_use / *_tool_result).
        text = "".join(
            getattr(block, "text", "") for block in response.content if block.type == "text"
        )

        # Anthropic does NOT return a cost field — compute from price table
        # (RESEARCH.md Pitfall 7).
        price_in, price_out = _PRICES_USD_PER_MTOK.get(
            model, _PRICES_USD_PER_MTOK["claude-sonnet-4-5"]
        )
        usd_cost: float = (
            prompt_tokens * price_in
            + cache_write_tokens * price_in * _CACHE_WRITE_MULTIPLIER
            + cache_read_tokens * price_in * _CACHE_READ_MULTIPLIER
            + completion_tokens * price_out
        ) / 1_000_000 + web_searches * _WEB_SEARCH_USD_PER_REQUEST

        # web_searches is folded into usd_cost only — llm_generations has no column for it,
        # so the log line is where the search count stays observable.
        logger.info(
            "llm_generate_ok",
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            web_searches=web_searches,
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

    @retry(
        retry=retry_if_exception(_is_openai_retryable),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def _openrouter_completion(self, **kwargs: Any) -> Any:
        return await self._openrouter.chat.completions.create(**kwargs)

    async def _generate_openrouter(
        self,
        messages: list[dict[str, Any]],
        model: str,
        *,
        system: str | None,
        tools: list[dict[str, Any]] | None,
    ) -> str:
        """generate() for OpenRouter slugs — the cascade copywriter's Gemini 2.5 Flash (§26).

        Same body the POC measured: max_tokens 2048, provider.data_collection deny (D-04), no
        ``reasoning`` field (thinking off is OpenRouter's default for 2.5 Flash; on, it doubled
        cost and latency and brought the only truncated replies). Cost is OpenRouter's billed
        ``usage.cost``, as in extract() — no local price table to drift.

        A reply with finish_reason != "stop" raises: a cut text has few concrete claims, so it
        passes the groundedness gate (Gemini "Inhotim", 28 characters, §26.4) — it must never
        reach the column.
        """
        if tools:
            raise ValueError(f"generate(): server-side tools are Anthropic-only; got {model!r}")
        full = ([{"role": "system", "content": system}] if system is not None else []) + list(
            messages
        )
        response = await self._openrouter_completion(
            model=model,
            max_tokens=2048,
            messages=full,
            extra_body={
                "provider": {"data_collection": self._config.provider_data_collection},
                "usage": {"include": True},
            },
        )
        choice = response.choices[0]
        usage = response.usage
        prompt_tokens: int = usage.prompt_tokens if usage else 0
        completion_tokens: int = usage.completion_tokens if usage else 0
        usd_cost: float = (
            float(usage.model_extra.get("cost", 0.0)) if usage and usage.model_extra else 0.0
        )
        logger.info(
            "llm_generate_ok",
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            finish_reason=choice.finish_reason,
            usd_cost=usd_cost,
        )

        # The call was billed whatever finish_reason says — record before rejecting it.
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

        if choice.finish_reason != "stop":
            raise PermanentError(f"generate(): {model} finish_reason={choice.finish_reason!r}")
        return choice.message.content or ""

    async def _gemini_post(
        self, url: str, body: dict[str, Any], *, timeout: float = _GEMINI_TIMEOUT_S
    ) -> dict[str, Any]:
        http = self._gemini_http
        if http is None:
            http = self._gemini_http = httpx.AsyncClient(timeout=_GEMINI_TIMEOUT_S)
        # Header auth: ?key= in the URL answers a misleading 429 (§9.2).
        r = await http.post(
            url, headers={"x-goog-api-key": self._config.gemini_api_key}, json=body, timeout=timeout
        )
        r.raise_for_status()
        result: dict[str, Any] = r.json()
        return result

    @retry(
        retry=retry_if_exception(_is_httpx_retryable),
        stop=stop_after_attempt(3),
        wait=_httpx_wait,
        reraise=True,
    )
    async def _gemini_post_standard(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        return await self._gemini_post(url, body)

    async def _generate_gemini(
        self,
        messages: list[dict[str, Any]],
        model: str,
        *,
        system: str | None,
        tools: list[dict[str, Any]] | None,
    ) -> str:
        """generate() for bare gemini-* slugs — Google AI Studio ``:generateContent`` (§30).

        Flex tier (-50%) when configured: ONE attempt capped at gemini_flex_timeout_s; on 503,
        429 or timeout the same body is re-sent right away at the standard tier (with the usual
        retries). 21/100 Flex calls took a 503 when measured, so without the fallback a fifth
        of the atrativos would pay a long wait or fail.

        Thinking is switched off explicitly: Google's default for 2.5 Flash is dynamic thinking,
        which doubled cost and latency and brought truncated replies (§26). The price comes from
        the tier Google says it BILLED (usageMetadata.serviceTier), not the one requested.

        No D-04 here: the paid AI Studio tier does not use prompts to improve Google products.
        """
        if tools:
            raise ValueError(f"generate(): server-side tools are Anthropic-only; got {model!r}")
        if not gemini_is_priced(model):
            raise ValueError(f"generate(): no Gemini price for {model!r}")
        if not self._config.gemini_api_key:
            raise PermanentError("generate(): BRAVE_LLM_GEMINI_API_KEY is empty")

        body: dict[str, Any] = {
            "contents": [
                {
                    "role": "model" if m["role"] == "assistant" else "user",
                    "parts": [{"text": m["content"]}],
                }
                for m in messages
            ],
            "generationConfig": {"maxOutputTokens": 2048, "thinkingConfig": {"thinkingBudget": 0}},
        }
        if system is not None:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        url = f"{self._config.gemini_base_url}/models/{model}:generateContent"

        tier = self._config.gemini_service_tier
        data: dict[str, Any] | None = None
        if tier == "flex":
            reason: str | None = None
            try:
                data = await self._gemini_post(
                    url, {**body, "serviceTier": "flex"}, timeout=self._config.gemini_flex_timeout_s
                )
            except httpx.TimeoutException:
                reason = "timeout"
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in _GEMINI_FLEX_FALLBACK_STATUS:
                    raise
                reason = str(exc.response.status_code)
            if data is None:
                logger.warning("gemini_flex_fallback", model=model, reason=reason)
                tier = "standard"
        if data is None:
            data = await self._gemini_post_standard(url, body)

        usage = data.get("usageMetadata") or {}
        billed = str(usage.get("serviceTier") or tier).lower()
        if (model, billed) not in _GEMINI_PRICES_USD_PER_MTOK:
            # Over-count rather than guess low: price an unexpected tier at standard.
            logger.warning("gemini_unknown_service_tier", model=model, service_tier=billed)
            usd_cost = _gemini_cost(model, "standard", usage)
        else:
            usd_cost = _gemini_cost(model, billed, usage)
        prompt_tokens = int(usage.get("promptTokenCount") or 0)
        completion_tokens = int(usage.get("candidatesTokenCount") or 0) + int(
            usage.get("thoughtsTokenCount") or 0
        )
        candidates = data.get("candidates") or []
        finish = candidates[0].get("finishReason") if candidates else None
        logger.info(
            "llm_generate_ok",
            model=model,
            service_tier=billed,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            finish_reason=finish,
            usd_cost=usd_cost,
        )

        # Billed whatever the reply says — record before rejecting it. resolved_provider
        # carries the billed tier: that is how the pilot measures the real Flex share.
        if self._redis_client is not None and self._session is not None:
            record_spend(self._redis_client, usd_cost)
            self._session.add(
                LLMGeneration(
                    id=uuid.uuid4(),
                    lane=self._lane,
                    model_slug=model,
                    resolved_provider=f"google-ai-studio:{billed}",
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    usd_cost=usd_cost,
                )
            )
            self._session.flush()

        if not candidates:
            block = (data.get("promptFeedback") or {}).get("blockReason")
            raise PermanentError(f"generate(): {model} no candidates, blockReason={block!r}")
        # A cut reply passes the groundedness gate (§26.4) — never let it reach the column.
        if finish != "STOP":
            raise PermanentError(f"generate(): {model} finishReason={finish!r}")
        parts = (candidates[0].get("content") or {}).get("parts") or []
        return "".join(p.get("text", "") for p in parts)


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
