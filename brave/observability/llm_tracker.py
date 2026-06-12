"""LLM call tracker — records every LLM call in llm_generations table (D-20, OBS-01).

LLMTracker wraps an LLMClientProtocol and:
1. Calls pre_dispatch_check (enforcing cost guard) BEFORE dispatch
2. Invokes the actual LLM call function
3. Records the call in the llm_generations table

Resolved provider defaults to model_slug in Phase 1.
Real provider from OpenRouter response headers arrives in Phase 2+.

See D-10: Log model_slug + tokens + usd_cost ONLY.
Do NOT log prompt content (potential PII). T-02-04.
"""

import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from redis import Redis
from sqlalchemy.orm import Session

from brave.config.settings import LLMConfig
from brave.core.models import LLMGeneration
from brave.observability.cost_guard import pre_dispatch_check, record_spend


class LLMTracker:
    """Wraps an LLM client call with pre-dispatch cost guard and post-call logging.

    Usage:
        tracker = LLMTracker(llm_client)
        result = await tracker.track_and_call(
            lane="core",
            model_slug="deepseek/deepseek-chat",
            session=session,
            redis_client=redis,
            config=llm_config,
            call_fn=lambda: llm_client.extract(prompt, MySchema),
        )
    """

    def __init__(self, client: Any) -> None:  # LLMClientProtocol
        """Initialize with an LLM client instance.

        Args:
            client: An LLMClientProtocol implementation (real or fake).
        """
        self._client = client

    async def track_and_call(
        self,
        lane: str,
        model_slug: str,
        session: Session,
        redis_client: Redis,
        config: LLMConfig,
        call_fn: Callable,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        usd_cost: float = 0.0,
    ) -> Any:
        """Enforce cost guard, invoke call_fn, and log the call.

        Args:
            lane:              Pipeline lane identifier (e.g., "core", "destinos").
            model_slug:        LLM model slug (e.g., "deepseek/deepseek-chat").
            session:           SQLAlchemy Session for llm_generations row.
            redis_client:      Redis client for cost guard counter.
            config:            LLMConfig with usd_daily_budget.
            call_fn:           Async callable that performs the LLM call.
            prompt_tokens:     Estimated prompt token count (0 in Phase 1; real from response Phase 2+).
            completion_tokens: Estimated completion token count (0 in Phase 1).
            usd_cost:          Estimated USD cost (0.0 in Phase 1; real from response Phase 2+).

        Returns:
            The result of call_fn().

        Raises:
            CostGuardError: If daily budget exceeded BEFORE dispatch.
        """
        # Enforce cost guard BEFORE any LLM call (D-20, T-02-03)
        pre_dispatch_check(redis_client, config)

        # Invoke the LLM call
        result = await call_fn()

        # Record spend (D-20): always advance the daily counter so the cost guard
        # reflects real usage (CR-04). usd_cost is a 0.0 stub in Phase 1 and the
        # real per-call cost from Phase 2+; recording 0.0 is a harmless no-op that
        # also keeps the daily key/TTL alive.
        record_spend(redis_client, usd_cost)

        # Log to llm_generations table (OBS-01)
        # NOTE: Do NOT include prompt content — potential PII (T-02-04)
        generation = LLMGeneration(
            id=uuid.uuid4(),
            lane=lane,
            model_slug=model_slug,
            resolved_provider=model_slug,  # Phase 1: same as slug; Phase 2+: from response headers
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            usd_cost=usd_cost,
        )
        session.add(generation)
        session.flush()

        return result
