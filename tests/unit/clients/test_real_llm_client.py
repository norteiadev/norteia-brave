"""Offline unit tests for RealLLMClient (D-07).

100% offline — no real LLM calls, no network (D-07, TEST-01).

Tests:
  T1 — guard: RuntimeError when run_real_externals=False
  T2 — deny enforcement: extra_body["provider"]["data_collection"] == "deny" on every call
  T3 — slug fallback: primary NotFoundError → retries with deepseek_fallback_slugs[0]
  T4 — cost-guard wiring: pre_dispatch_check invoked + LLMGeneration row written
  T5 — pipeline wiring assertion: outreach_task call site passes redis_client= and session=
"""

from __future__ import annotations

import os
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from brave.core.models import Base, LLMGeneration


# ---------------------------------------------------------------------------
# Local SQLite in-memory session fixture (no BRAVE_DB_URL required, T4)
# ---------------------------------------------------------------------------


@pytest.fixture
def sqlite_session() -> Session:
    """In-memory SQLite session for LLMGeneration rows (fully offline, no Docker).

    Creates the llm_generations table using the real model definition and rolls
    back after each test so rows don't leak between tests.
    """
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    # Only create the tables we need — pgvector extension is not available in SQLite
    # so we create only the LLMGeneration table explicitly.
    LLMGeneration.__table__.create(bind=engine)
    SessionFactory = sessionmaker(bind=engine)
    session = SessionFactory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        engine.dispose()


# ---------------------------------------------------------------------------
# T1 — guard raises when run_real_externals=False
# ---------------------------------------------------------------------------


def test_guard_raises_when_run_real_externals_false(monkeypatch):
    """RealLLMClient raises RuntimeError containing 'run_real_externals=False'
    when RUN_REAL_EXTERNALS env var is absent/false.

    No network calls; imports happen inside test to pick up the env state.
    """
    monkeypatch.delenv("RUN_REAL_EXTERNALS", raising=False)
    # Clear pydantic-settings cache so AppConfig picks up the env change
    monkeypatch.delenv("BRAVE_LLM_OPENROUTER_API_KEY", raising=False)

    from brave.clients.llm import RealLLMClient
    from brave.config.settings import LLMConfig

    with pytest.raises(RuntimeError, match="run_real_externals=False"):
        RealLLMClient(config=LLMConfig())


# ---------------------------------------------------------------------------
# T2 — deny block present in every OpenRouter request
# ---------------------------------------------------------------------------


async def test_deny_block_present_in_openrouter_request(monkeypatch):
    """extract() passes extra_body={"provider": {"data_collection": "deny"}} on every call.

    D-04: data_collection must be "deny" — asserted via mock.call_args.kwargs.
    Uses AsyncMock; no real network.
    """
    monkeypatch.setenv("RUN_REAL_EXTERNALS", "true")
    monkeypatch.setenv("BRAVE_LLM_OPENROUTER_API_KEY", "test-key")

    from brave.clients.llm import RealLLMClient
    from brave.config.settings import LLMConfig

    config = LLMConfig(openrouter_api_key="test-key")
    client = RealLLMClient(config=config)

    # Patch create_with_completion on the live instructor client instance.
    # Return (result, raw) where raw.usage=None simulates no usage (cost=0.0 fallback path).
    fake_result = MagicMock()
    fake_raw = MagicMock(usage=None)
    mock_create = AsyncMock(return_value=(fake_result, fake_raw))
    client._instructor_client.create_with_completion = mock_create

    schema_mock = MagicMock()
    schema_mock.__name__ = "Schema"

    await client.extract(prompt="test", schema=schema_mock)

    assert mock_create.called, "create_with_completion was not called"
    call_kwargs = mock_create.call_args.kwargs
    assert "extra_body" in call_kwargs, "extra_body not in call kwargs"
    extra_body = call_kwargs["extra_body"]
    assert extra_body["provider"]["data_collection"] == "deny", (
        f"Expected 'deny', got {extra_body['provider']['data_collection']!r}"
    )


# ---------------------------------------------------------------------------
# T3 — primary slug NotFoundError falls back to next slug
# ---------------------------------------------------------------------------


async def test_primary_slug_notfound_falls_back_to_next_slug(monkeypatch):
    """When _call_slug raises NotFoundError on the primary slug, extract() retries
    with deepseek_fallback_slugs[0].

    Second call must use the fallback slug string.
    """
    monkeypatch.setenv("RUN_REAL_EXTERNALS", "true")
    monkeypatch.setenv("BRAVE_LLM_OPENROUTER_API_KEY", "test-key")

    from openai import NotFoundError

    from brave.clients.llm import RealLLMClient
    from brave.config.settings import LLMConfig

    config = LLMConfig(
        openrouter_api_key="test-key",
        deepseek_primary_slug="primary/slug",
        deepseek_fallback_slugs=["fallback/slug"],
    )
    client = RealLLMClient(config=config)

    # Capture slug arguments across calls
    call_slugs: list[str] = []

    fake_result = MagicMock()
    fake_raw = MagicMock(usage=None)

    not_found_response = MagicMock()
    not_found_response.status_code = 404

    original_call_slug = client._call_slug

    async def mock_call_slug(slug: str, prompt: str, schema: type) -> tuple[Any, Any]:
        call_slugs.append(slug)
        if slug == "primary/slug":
            raise NotFoundError(
                message="Model not found",
                response=not_found_response,
                body={"error": {"message": "model not found"}},
            )
        return fake_result, fake_raw

    client._call_slug = mock_call_slug

    schema_mock = MagicMock()
    schema_mock.__name__ = "Schema"

    result = await client.extract(prompt="test", schema=schema_mock)

    assert len(call_slugs) == 2, f"Expected 2 _call_slug invocations, got {len(call_slugs)}: {call_slugs}"
    assert call_slugs[0] == "primary/slug", f"First call slug mismatch: {call_slugs[0]!r}"
    assert call_slugs[1] == "fallback/slug", f"Second call slug mismatch: {call_slugs[1]!r}"
    assert result is fake_result


# ---------------------------------------------------------------------------
# T4 — cost guard invoked + LLMGeneration row written
# ---------------------------------------------------------------------------


async def test_cost_guard_invoked_and_llm_generation_written(
    monkeypatch, fake_redis, sqlite_session
):
    """extract() with redis_client + session:
      - pre_dispatch_check fires before dispatch (no CostGuardError = budget ok)
      - one LLMGeneration row written with usd_cost > 0 and lane == "test"
      - prompt content is NOT in the row (T-02-04)

    Uses fakeredis + in-memory SQLite session (fully offline, no containers).
    """
    monkeypatch.setenv("RUN_REAL_EXTERNALS", "true")
    monkeypatch.setenv("BRAVE_LLM_OPENROUTER_API_KEY", "test-key")

    from brave.clients.llm import RealLLMClient
    from brave.config.settings import LLMConfig

    config = LLMConfig(openrouter_api_key="test-key", usd_daily_budget=10.0)
    client = RealLLMClient(
        config=config,
        redis_client=fake_redis,
        session=sqlite_session,
        lane="test",
    )

    # Simulate a real-looking raw completion with usage (usd_cost via model_extra)
    fake_usage = MagicMock()
    fake_usage.prompt_tokens = 100
    fake_usage.completion_tokens = 50
    fake_usage.model_extra = {"cost": 0.002}
    fake_raw = MagicMock(usage=fake_usage)
    fake_result = MagicMock()
    mock_create = AsyncMock(return_value=(fake_result, fake_raw))
    client._instructor_client.create_with_completion = mock_create

    schema_mock = MagicMock()
    schema_mock.__name__ = "Schema"

    await client.extract(prompt="this is a test prompt", schema=schema_mock)

    # Verify one LLMGeneration row was written
    rows = sqlite_session.query(LLMGeneration).all()
    assert len(rows) == 1, f"Expected 1 LLMGeneration row, got {len(rows)}"

    row = rows[0]
    assert float(row.usd_cost) > 0, f"Expected usd_cost > 0, got {row.usd_cost}"
    assert row.lane == "test", f"Expected lane='test', got {row.lane!r}"

    # T-02-04: no prompt content stored
    row_repr = repr(row)
    assert "this is a test prompt" not in row_repr, (
        "Prompt content leaked into LLMGeneration repr"
    )
    # Verify the row fields — none should contain the prompt text
    assert row.model_slug is not None
    assert row.prompt_tokens == 100
    assert row.completion_tokens == 50


# ---------------------------------------------------------------------------
# T5 — pipeline.py outreach_task wiring assertion (structural grep)
# ---------------------------------------------------------------------------


def test_pipeline_outreach_task_passes_redis_and_session_to_real_llm_client():
    """Structural assertion: pipeline.py contains the wired RealLLMClient call site.

    Reads brave/tasks/pipeline.py as text and asserts the expected constructor
    call signature is present — confirming that Task 2 has wired redis_client,
    session, and lane into the outreach_task real path.

    This is a 100% offline structural test (D-07, TEST-01): no env vars needed,
    no imports of real clients. If this fails, Task 2 edits are incomplete.
    """
    pipeline_path = os.path.join(
        os.path.dirname(__file__),
        "..", "..", "..", "brave", "tasks", "pipeline.py",
    )
    pipeline_path = os.path.normpath(pipeline_path)

    with open(pipeline_path, "r", encoding="utf-8") as f:
        source = f.read()

    expected_signature = (
        "RealLLMClient(config=app_config.llm, redis_client=redis_client, session=session"
    )
    count = source.count(expected_signature)

    assert count >= 1, (
        f"Expected at least 1 occurrence of wired RealLLMClient call site in pipeline.py, "
        f"found {count}. Task 2 (pipeline wiring) must be completed first."
    )
