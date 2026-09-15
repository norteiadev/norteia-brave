"""Offline unit tests for RealLLMClient (D-07).

100% offline — no real LLM calls, no network (D-07, TEST-01).

Tests:
  T1 — guard: RuntimeError when run_real_externals=False
  T2 — deny enforcement: extra_body["provider"]["data_collection"] == "deny" on every call
  T3 — slug fallback: primary NotFoundError → retries with deepseek_fallback_slugs[0]
  T4 — cost-guard wiring: pre_dispatch_check invoked + LLMGeneration row written
  T5 — pipeline wiring assertion: outreach_task call site passes redis_client= and session=
  T6 — generate() prices the web_search server-tool fee on top of tokens
  T7 — generate() with no server_tool_use prices tokens only (no regression)
  T8 — generate() accumulates the search fee across pause_turn resumes
  Gemini direct — body, Flex→standard fallback, billed-tier pricing, spend before rejection
"""

from __future__ import annotations

import os
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx
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


# ---------------------------------------------------------------------------
# T6/T7/T8 — generate() cost accounting (web_search fee is billed on top of tokens)
# ---------------------------------------------------------------------------


def _fake_response(
    *,
    input_tokens: int,
    output_tokens: int,
    web_search_requests: int | None = None,
    stop_reason: str = "end_turn",
) -> SimpleNamespace:
    """Anthropic Message double. server_tool_use is absent unless a count is given —
    that is how the API shapes a response where no server tool ran.
    """
    usage = SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens)
    if web_search_requests is not None:
        usage.server_tool_use = SimpleNamespace(web_search_requests=web_search_requests)
    return SimpleNamespace(
        usage=usage,
        stop_reason=stop_reason,
        content=[SimpleNamespace(type="text", text="descrição")],
    )


def _generate_client(monkeypatch, **kwargs: Any):
    monkeypatch.setenv("RUN_REAL_EXTERNALS", "true")
    monkeypatch.setenv("BRAVE_LLM_OPENROUTER_API_KEY", "test-key")

    from brave.clients.llm import RealLLMClient
    from brave.config.settings import LLMConfig

    return RealLLMClient(
        config=LLMConfig(openrouter_api_key="test-key", usd_daily_budget=10.0), **kwargs
    )


async def test_generate_prices_web_search_fee_on_top_of_tokens(
    monkeypatch, fake_redis, sqlite_session
):
    """3 web searches must add 3 x $0.01 to the token cost, in usd_cost and in the row.

    The $10/1,000 search fee is ~30% of an atrativo description; before this it was
    invisible to record_spend and to the daily budget guard.
    """
    client = _generate_client(
        monkeypatch, redis_client=fake_redis, session=sqlite_session, lane="test"
    )
    client._anthropic_client.messages.create = AsyncMock(
        return_value=_fake_response(input_tokens=20_000, output_tokens=1_000, web_search_requests=3)
    )

    await client.generate(messages=[{"role": "user", "content": "x"}])

    expected = (20_000 * 3.0 + 1_000 * 15.0) / 1_000_000 + 3 * 0.01
    rows = sqlite_session.query(LLMGeneration).all()
    assert len(rows) == 1
    assert float(rows[0].usd_cost) == pytest.approx(expected)


async def test_generate_without_server_tool_use_prices_tokens_only(
    monkeypatch, fake_redis, sqlite_session
):
    """No server tool ran → usage has no server_tool_use attribute → tokens-only price."""
    client = _generate_client(
        monkeypatch, redis_client=fake_redis, session=sqlite_session, lane="test"
    )
    client._anthropic_client.messages.create = AsyncMock(
        return_value=_fake_response(input_tokens=1_000, output_tokens=500)
    )

    await client.generate(messages=[{"role": "user", "content": "x"}])

    expected = (1_000 * 3.0 + 500 * 15.0) / 1_000_000
    rows = sqlite_session.query(LLMGeneration).all()
    assert float(rows[0].usd_cost) == pytest.approx(expected)


async def test_generate_prices_haiku_at_haiku_rates(monkeypatch, fake_redis, sqlite_session):
    """Haiku is $1/$5 per MTok, not Sonnet's $3/$15 — a 3x over-count in record_spend would
    trip the daily budget guard 3x early on the cascade copywriter."""
    client = _generate_client(
        monkeypatch, redis_client=fake_redis, session=sqlite_session, lane="test"
    )
    client._anthropic_client.messages.create = AsyncMock(
        return_value=_fake_response(input_tokens=1_000, output_tokens=500)
    )

    await client.generate(messages=[{"role": "user", "content": "x"}], model="claude-haiku-4-5")

    rows = sqlite_session.query(LLMGeneration).all()
    assert float(rows[0].usd_cost) == pytest.approx((1_000 * 1.0 + 500 * 5.0) / 1_000_000)


async def test_generate_accumulates_web_search_fee_across_pause_turns(
    monkeypatch, fake_redis, sqlite_session
):
    """Searches run on every resumed turn, so the fee must sum like the tokens do."""
    client = _generate_client(
        monkeypatch, redis_client=fake_redis, session=sqlite_session, lane="test"
    )
    client._anthropic_client.messages.create = AsyncMock(
        side_effect=[
            _fake_response(
                input_tokens=5_000,
                output_tokens=100,
                web_search_requests=2,
                stop_reason="pause_turn",
            ),
            _fake_response(input_tokens=9_000, output_tokens=400, web_search_requests=1),
        ]
    )

    await client.generate(messages=[{"role": "user", "content": "x"}])

    expected = (14_000 * 3.0 + 500 * 15.0) / 1_000_000 + 3 * 0.01
    rows = sqlite_session.query(LLMGeneration).all()
    assert float(rows[0].usd_cost) == pytest.approx(expected)


# ---------------------------------------------------------------------------
# generate() via OpenRouter — "vendor/model" slugs (the cascade's Gemini, §26/§29)
# ---------------------------------------------------------------------------


def _openrouter_completion(*, finish: str = "stop", cost: float = 0.0021, text: str = "descrição"):
    usage = SimpleNamespace(prompt_tokens=4_000, completion_tokens=400, model_extra={"cost": cost})
    choice = SimpleNamespace(finish_reason=finish, message=SimpleNamespace(content=text))
    return SimpleNamespace(choices=[choice], usage=usage)


async def test_generate_openrouter_slug_body_cost_and_row(monkeypatch, fake_redis, sqlite_session):
    client = _generate_client(
        monkeypatch, redis_client=fake_redis, session=sqlite_session, lane="test"
    )
    create = AsyncMock(return_value=_openrouter_completion())
    client._openrouter.chat.completions.create = create
    client._anthropic_client.messages.create = AsyncMock()

    out = await client.generate(
        [{"role": "user", "content": "x"}], model="google/gemini-2.5-flash", system="sys"
    )

    assert out == "descrição"
    assert not client._anthropic_client.messages.create.called
    kw = create.call_args.kwargs
    assert kw["model"] == "google/gemini-2.5-flash" and kw["max_tokens"] == 2048
    assert kw["messages"] == [{"role": "system", "content": "sys"}, {"role": "user", "content": "x"}]
    assert kw["extra_body"]["provider"] == {"data_collection": "deny"}
    assert "reasoning" not in kw["extra_body"]
    (row,) = sqlite_session.query(LLMGeneration).all()
    assert row.model_slug == "google/gemini-2.5-flash"
    assert float(row.usd_cost) == pytest.approx(0.0021), "OpenRouter's billed cost, not a table"


async def test_generate_openrouter_rejects_cut_reply_but_records_spend(
    monkeypatch, fake_redis, sqlite_session
):
    from brave.shared.exceptions import PermanentError

    client = _generate_client(
        monkeypatch, redis_client=fake_redis, session=sqlite_session, lane="test"
    )
    client._openrouter.chat.completions.create = AsyncMock(
        return_value=_openrouter_completion(finish="length", text="Em Brumadinho,")
    )

    with pytest.raises(PermanentError, match="finish_reason='length'"):
        await client.generate([{"role": "user", "content": "x"}], model="google/gemini-2.5-flash")
    assert len(sqlite_session.query(LLMGeneration).all()) == 1


async def test_generate_openrouter_refuses_server_tools(monkeypatch):
    client = _generate_client(monkeypatch)
    with pytest.raises(ValueError, match="Anthropic-only"):
        await client.generate(
            [{"role": "user", "content": "x"}], model="google/gemini-2.5-flash", tools=[{"x": 1}]
        )


# ---------------------------------------------------------------------------
# generate() via Gemini direct — bare "gemini-*" slugs, Google AI Studio (§30)
# ---------------------------------------------------------------------------

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"


def _gemini_reply(
    text: str = "descrição",
    *,
    finish: str = "STOP",
    tier: str | None = "flex",
    prompt: int = 4_000,
    cached: int = 0,
    out: int = 400,
) -> dict[str, Any]:
    usage: dict[str, Any] = {"promptTokenCount": prompt, "candidatesTokenCount": out}
    if cached:
        usage["cachedContentTokenCount"] = cached
    if tier is not None:
        usage["serviceTier"] = tier
    return {
        "candidates": [{"content": {"role": "model", "parts": [{"text": text}]}, "finishReason": finish}],
        "usageMetadata": usage,
    }


def _gemini_client(monkeypatch, fake_redis, sqlite_session, **cfg: Any):
    monkeypatch.setenv("RUN_REAL_EXTERNALS", "true")

    from brave.clients.llm import RealLLMClient
    from brave.config.settings import LLMConfig

    config = LLMConfig(
        openrouter_api_key="test-key", gemini_api_key="g-key", usd_daily_budget=10.0, **cfg
    )
    return RealLLMClient(config=config, redis_client=fake_redis, session=sqlite_session, lane="t")


def _flex_usd(prompt: int = 4_000, out: int = 400) -> float:
    return (prompt * 0.15 + out * 1.25) / 1_000_000


def _standard_usd(prompt: int = 4_000, out: int = 400) -> float:
    return (prompt * 0.30 + out * 2.50) / 1_000_000


@respx.mock
async def test_generate_gemini_body_routing_and_row(monkeypatch, fake_redis, sqlite_session):
    import json

    client = _gemini_client(monkeypatch, fake_redis, sqlite_session)
    route = respx.post(GEMINI_URL).mock(return_value=httpx.Response(200, json=_gemini_reply()))
    client._openrouter.chat.completions.create = AsyncMock()
    client._anthropic_client.messages.create = AsyncMock()

    out = await client.generate(
        [{"role": "user", "content": "x"}, {"role": "assistant", "content": "y"}],
        model="gemini-2.5-flash",
        system="sys",
    )

    assert out == "descrição"
    assert not client._openrouter.chat.completions.create.called
    assert not client._anthropic_client.messages.create.called
    req = route.calls.last.request
    assert req.headers["x-goog-api-key"] == "g-key"
    assert "key=" not in str(req.url), "?key= answers a misleading 429 (§9.2)"
    body = json.loads(req.content)
    assert body["systemInstruction"] == {"parts": [{"text": "sys"}]}
    assert body["contents"] == [
        {"role": "user", "parts": [{"text": "x"}]},
        {"role": "model", "parts": [{"text": "y"}]},
    ]
    assert body["generationConfig"] == {
        "maxOutputTokens": 2048,
        "thinkingConfig": {"thinkingBudget": 0},
    }, "Google's default is dynamic thinking — it must be switched off explicitly"
    assert body["serviceTier"] == "flex"
    (row,) = sqlite_session.query(LLMGeneration).all()
    assert (row.model_slug, row.resolved_provider) == ("gemini-2.5-flash", "google-ai-studio:flex")
    assert float(row.usd_cost) == pytest.approx(_flex_usd())


@respx.mock
async def test_generate_gemini_flex_503_falls_back_to_standard(
    monkeypatch, fake_redis, sqlite_session
):
    import json

    client = _gemini_client(monkeypatch, fake_redis, sqlite_session)
    route = respx.post(GEMINI_URL).mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json=_gemini_reply(tier="standard"))]
    )

    assert await client.generate([{"role": "user", "content": "x"}], model="gemini-2.5-flash")

    assert route.call_count == 2
    assert json.loads(route.calls[0].request.content)["serviceTier"] == "flex"
    assert "serviceTier" not in json.loads(route.calls[1].request.content)
    (row,) = sqlite_session.query(LLMGeneration).all()
    assert row.resolved_provider == "google-ai-studio:standard"
    assert float(row.usd_cost) == pytest.approx(_standard_usd())


@respx.mock
async def test_generate_gemini_flex_timeout_falls_back_to_standard(
    monkeypatch, fake_redis, sqlite_session
):
    import json

    client = _gemini_client(monkeypatch, fake_redis, sqlite_session)
    # No serviceTier in the standard reply: the tier of the attempt that answered prices it.
    route = respx.post(GEMINI_URL).mock(
        side_effect=[httpx.ReadTimeout("flex queue"), httpx.Response(200, json=_gemini_reply(tier=None))]
    )

    await client.generate([{"role": "user", "content": "x"}], model="gemini-2.5-flash")

    assert route.call_count == 2
    assert "serviceTier" not in json.loads(route.calls[1].request.content)
    (row,) = sqlite_session.query(LLMGeneration).all()
    assert row.resolved_provider == "google-ai-studio:standard"
    assert float(row.usd_cost) == pytest.approx(_standard_usd())


@respx.mock
async def test_generate_gemini_billed_tier_is_the_source_of_truth(
    monkeypatch, fake_redis, sqlite_session
):
    """Flex requested but Google says standard was billed → standard price."""
    client = _gemini_client(monkeypatch, fake_redis, sqlite_session)
    respx.post(GEMINI_URL).mock(return_value=httpx.Response(200, json=_gemini_reply(tier="standard")))

    await client.generate([{"role": "user", "content": "x"}], model="gemini-2.5-flash")

    (row,) = sqlite_session.query(LLMGeneration).all()
    assert row.resolved_provider == "google-ai-studio:standard"
    assert float(row.usd_cost) == pytest.approx(_standard_usd())


@respx.mock
async def test_generate_gemini_prices_cached_tokens_and_records_spend(
    monkeypatch, fake_redis, sqlite_session
):
    from brave.observability.cost_guard import _daily_key

    client = _gemini_client(monkeypatch, fake_redis, sqlite_session)
    respx.post(GEMINI_URL).mock(
        return_value=httpx.Response(200, json=_gemini_reply(prompt=4_000, cached=1_000, out=400))
    )

    await client.generate([{"role": "user", "content": "x"}], model="gemini-2.5-flash")

    expected = (3_000 * 0.15 + 1_000 * 0.03 + 400 * 1.25) / 1_000_000
    (row,) = sqlite_session.query(LLMGeneration).all()
    assert float(row.usd_cost) == pytest.approx(expected)
    assert float(fake_redis.get(_daily_key())) == pytest.approx(expected)


@respx.mock
async def test_generate_gemini_rejects_cut_reply_but_records_spend(
    monkeypatch, fake_redis, sqlite_session
):
    from brave.shared.exceptions import PermanentError

    client = _gemini_client(monkeypatch, fake_redis, sqlite_session)
    respx.post(GEMINI_URL).mock(
        return_value=httpx.Response(200, json=_gemini_reply("Em Brumadinho,", finish="MAX_TOKENS"))
    )

    with pytest.raises(PermanentError, match="MAX_TOKENS"):
        await client.generate([{"role": "user", "content": "x"}], model="gemini-2.5-flash")
    (row,) = sqlite_session.query(LLMGeneration).all()
    assert float(row.usd_cost) == pytest.approx(_flex_usd())


@respx.mock
async def test_generate_gemini_blocked_prompt_raises(monkeypatch, fake_redis, sqlite_session):
    from brave.shared.exceptions import PermanentError

    client = _gemini_client(monkeypatch, fake_redis, sqlite_session)
    respx.post(GEMINI_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "promptFeedback": {"blockReason": "SAFETY"},
                "usageMetadata": {"promptTokenCount": 4_000, "serviceTier": "flex"},
            },
        )
    )

    with pytest.raises(PermanentError, match="SAFETY"):
        await client.generate([{"role": "user", "content": "x"}], model="gemini-2.5-flash")


@respx.mock
async def test_generate_gemini_unpriced_model_and_empty_key_fail_before_dispatch(
    monkeypatch, fake_redis, sqlite_session
):
    from brave.shared.exceptions import PermanentError

    route = respx.post(url__regex=r".*generativelanguage.*")
    client = _gemini_client(monkeypatch, fake_redis, sqlite_session)
    with pytest.raises(ValueError, match="no Gemini price"):
        await client.generate([{"role": "user", "content": "x"}], model="gemini-9-ultra")

    client._config = client._config.model_copy(update={"gemini_api_key": ""})
    with pytest.raises(PermanentError, match="BRAVE_LLM_GEMINI_API_KEY"):
        await client.generate([{"role": "user", "content": "x"}], model="gemini-2.5-flash")
    assert not route.called


@respx.mock
async def test_generate_gemini_standard_tier_sends_one_request_without_service_tier(
    monkeypatch, fake_redis, sqlite_session
):
    import json

    client = _gemini_client(monkeypatch, fake_redis, sqlite_session, gemini_service_tier="standard")
    route = respx.post(GEMINI_URL).mock(
        return_value=httpx.Response(200, json=_gemini_reply(tier="standard"))
    )

    await client.generate([{"role": "user", "content": "x"}], model="gemini-2.5-flash")

    assert route.call_count == 1
    assert "serviceTier" not in json.loads(route.calls.last.request.content)


async def test_generate_gemini_refuses_server_tools(monkeypatch, fake_redis, sqlite_session):
    client = _gemini_client(monkeypatch, fake_redis, sqlite_session)
    with pytest.raises(ValueError, match="Anthropic-only"):
        await client.generate(
            [{"role": "user", "content": "x"}], model="gemini-2.5-flash", tools=[{"x": 1}]
        )
