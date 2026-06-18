# Phase 6: Real-Externals Enablement (RealLLMClient + live 24/7 collection) - Pattern Map

**Mapped:** 2026-06-17
**Files analyzed:** 6 (1 new, 3 docstring-only fixes, 1 new test, 1 docstring-only test fix)
**Analogs found:** 6 / 6

---

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `brave/clients/llm.py` (new) | service / client | request-response (async LLM) | `brave/clients/places.py` + `brave/clients/apify.py` | exact role-match (same guard+retry+structlog pattern) |
| `tests/unit/test_real_llm_client.py` (new) | test | — | `tests/integration/test_cost_guard.py` + `tests/unit/test_scaffold_smoke.py` | exact (guard+behavior unit test pattern) |
| `brave/clients/places.py` (docstring fix) | service / client | request-response | self | N/A (string-only edit) |
| `brave/clients/apify.py` (docstring fix) | service / client | request-response | self | N/A (string-only edit) |
| `brave/clients/whatsapp.py` (docstring fix) | service / client | request-response | self | N/A (string-only edit) |
| `tests/integration/test_atrativos_lane_e2e.py` (docstring fix) | test | — | self | N/A (string-only edit) |

---

## Pattern Assignments

### `brave/clients/llm.py` — new `RealLLMClient` (service, request-response)

**Primary analog:** `brave/clients/places.py` (RealPlacesClient)
**Secondary analog:** `brave/clients/apify.py` (RealApifyClient)

---

#### Module docstring shape

Copy from `brave/clients/places.py` lines 1–20:

```python
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
```

---

#### Imports pattern

Copy from `brave/clients/apify.py` lines 22–30 (exact shape — both clients use the same four imports):

```python
from __future__ import annotations

from typing import Any

import structlog
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

logger = structlog.get_logger(__name__)
```

Add LLM-specific imports after:

```python
import uuid
from datetime import datetime, timezone

import instructor
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

from brave.config.settings import LLMConfig
from brave.core.models import LLMGeneration
from brave.observability.cost_guard import pre_dispatch_check, record_spend
```

---

#### `_is_retryable` predicate — copy + adapt from `brave/clients/apify.py` lines 48–62

The apify predicate checks `status_code` attribute on a generic exception object.
For `RealLLMClient`, use the typed openai exception hierarchy instead:

```python
# brave/clients/apify.py lines 48–62 (base pattern):
def _is_retryable(exc: BaseException) -> bool:
    """Return True for transient Apify errors only (rate-limit / 5xx / transport)."""
    exc_name = type(exc).__name__
    if "Timeout" in exc_name or "ConnectionError" in exc_name:
        return True
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if isinstance(status, int):
        return status == 429 or status >= 500
    return False
```

For `RealLLMClient`, adapt to the typed openai exception hierarchy (Research Pattern 4):

```python
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
```

---

#### `@retry` decorator — copy verbatim from `brave/clients/places.py` lines 124–129

```python
@retry(
    retry=retry_if_exception(_is_openai_retryable),  # WR-01: transient only
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
```

---

#### `__init__` guard pattern — copy from `brave/clients/places.py` lines 88–105, adapt for LLMConfig

```python
# brave/clients/places.py lines 88–105 (exact pattern to copy):
def __init__(self, api_key: str) -> None:
    from brave.config.settings import AppConfig

    if not AppConfig().run_real_externals:
        raise RuntimeError(
            "RealPlacesClient: run_real_externals=False — "
            "use FakePlacesClient in default test suite. "
            "Set BRAVE_RUN_REAL_EXTERNALS=true to enable real API calls."
        )

    if not api_key:
        raise RuntimeError(
            "RealPlacesClient: api_key is empty — "
            "set BRAVE_PLACES_API_KEY environment variable."
        )

    self._api_key = api_key
    self._client = None  # Lazy init — avoid import-time SDK setup
```

For `RealLLMClient`, the adapted version with optional tracking deps (D-01, D-05):

```python
def __init__(
    self,
    config: LLMConfig,
    *,
    redis_client=None,
    session=None,
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
```

Note: `instructor.from_openai` must be called at `__init__` time (not lazy) so the
`MD_JSON` mode assertion fires early and the client is reusable across calls.

---

#### `extract()` core pattern

Source: Research CONTEXT.md Pattern 2 (verified API) + `LLMTracker.track_and_call` in
`brave/observability/llm_tracker.py` lines 81–107 (cost guard + LLMGeneration pattern to inline):

```python
# From brave/observability/llm_tracker.py lines 81–107 (pattern to inline in extract()):
pre_dispatch_check(redis_client, config)           # BEFORE dispatch (D-20, T-02-03)

result = await call_fn()                            # LLM call

record_spend(redis_client, usd_cost)               # atomic INCRBYFLOAT

generation = LLMGeneration(
    id=uuid.uuid4(),
    lane=lane,
    model_slug=model_slug,
    resolved_provider=model_slug,
    prompt_tokens=prompt_tokens,
    completion_tokens=completion_tokens,
    usd_cost=usd_cost,
)
session.add(generation)
session.flush()
```

The `extract()` method wraps this around the instructor call with slug fallback:

```python
# From Research Pattern 2 (verified: instructor 1.15.1 + openai 2.41.1 .venv):
result, raw = await self._instructor_client.create_with_completion(
    messages=[{"role": "user", "content": prompt}],
    response_model=schema,
    model=slug,
    extra_body={"provider": {"data_collection": self._config.provider_data_collection}},
)
usage = raw.usage
prompt_tokens = usage.prompt_tokens if usage else 0
completion_tokens = usage.completion_tokens if usage else 0
usd_cost = float(usage.model_extra.get("cost", 0.0)) if usage and usage.model_extra else 0.0
```

Slug fallback wraps the `create_with_completion` call (Research Pattern 4):

```python
slugs = [self._config.deepseek_primary_slug] + list(self._config.deepseek_fallback_slugs)
last_exc: Exception | None = None
for slug in slugs:
    try:
        result, raw = await self._call_slug(slug, prompt, schema)
        break
    except (NotFoundError,) as exc:
        last_exc = exc
        logger.warning("llm_slug_unavailable", slug=slug, error=str(exc))
        continue
    except (BadRequestError, PermissionDeniedError) as exc:
        raise  # permanent — don't try next slug
else:
    raise last_exc  # all slugs exhausted
```

---

#### `generate()` core pattern

Source: Research Pattern 5 (verified: anthropic 0.109.1 .venv):

```python
# From Research CONTEXT.md Code Examples (anthropic 0.109.1 — local .venv):
response = await self._anthropic_client.messages.create(
    model=model,       # e.g. "claude-sonnet-4-5" — REQUIRED max_tokens too
    max_tokens=2048,   # REQUIRED — no default in anthropic 0.109.1
    messages=messages,
)
text: str = response.content[0].text   # TextBlock.text
prompt_tokens = response.usage.input_tokens
completion_tokens = response.usage.output_tokens
# Anthropic does NOT return cost field — compute from price table:
usd_cost = (prompt_tokens * 3.0 + completion_tokens * 15.0) / 1_000_000
```

Prices as module-level constants (easy to update):

```python
# Module-level constants for Sonnet 4.5 pricing (Anthropic docs, 2026-06)
_SONNET_4_5_INPUT_USD_PER_MTOK: float = 3.0
_SONNET_4_5_OUTPUT_USD_PER_MTOK: float = 15.0
```

---

#### Shared cost-guard + LLMGeneration wiring (both `extract()` and `generate()`)

Both methods follow the same sequence when optional tracking deps are present.
Source: `brave/observability/llm_tracker.py` lines 81–107 (exact helper calls):

```python
# Inline in extract() / generate() when self._redis_client and self._session are set:
if self._redis_client is not None:
    pre_dispatch_check(self._redis_client, self._config)    # raises CostGuardError if over budget

# ... LLM call here ...

if self._redis_client is not None and self._session is not None:
    record_spend(self._redis_client, usd_cost)
    self._session.add(LLMGeneration(
        id=uuid.uuid4(),
        lane=self._lane,
        model_slug=slug,
        resolved_provider=slug,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        usd_cost=usd_cost,
    ))
    self._session.flush()
```

Key: **never log prompt content** (T-02-04). `LLMGeneration` stores only slug + tokens + usd_cost.
Source: `brave/observability/llm_tracker.py` lines 94–95 comment.

---

#### `_check_protocol_compliance()` pattern — copy from `tests/fakes/fake_llm.py` lines 98–100

```python
# tests/fakes/fake_llm.py lines 98–100 (exact pattern):
def _check_protocol_compliance() -> None:
    """Compile-time structural typing assertion (not called at runtime)."""
    _client: LLMClientProtocol = FakeLLMClient()  # noqa: F841
```

For `RealLLMClient` — cannot instantiate (guard raises), so use comment-only variant
as in `brave/clients/places.py` lines 255–265 and `brave/clients/apify.py` lines 189–196:

```python
# brave/clients/places.py lines 255–265 (comment-only compliance check):
def _check_protocol_compliance() -> None:
    """Compile-time structural typing assertion (not called at runtime).

    Verifies that RealPlacesClient structurally satisfies PlacesClientProtocol.
    Skipped at runtime because instantiation requires run_real_externals=True.
    """
    # NOTE: RealLLMClient raises RuntimeError if run_real_externals=False,
    # so we cannot instantiate it here. Structural compliance verified by
    # type annotations on extract() and generate() matching LLMClientProtocol.
    pass
```

---

### `tests/unit/test_real_llm_client.py` — new unit test file

**Primary analog:** `tests/integration/test_cost_guard.py` (fixture usage + pytest.raises pattern)
**Secondary analog:** `tests/unit/test_scaffold_smoke.py` (monkeypatch + import-guard test style)

---

#### File structure + import pattern

Copy from `tests/integration/test_cost_guard.py` lines 1–9:

```python
"""Unit tests for RealLLMClient (brave/clients/llm.py).

Tests:
  1. Guard: RealLLMClient.__init__ raises RuntimeError when run_real_externals=False.
  2. Deny enforcement: every extract() call passes provider.data_collection="deny" in extra_body.
  3. Slug fallback: primary slug NotFoundError → next slug tried.
  4. Cost-guard wiring: extract() invokes pre_dispatch_check and writes one LLMGeneration row.

100% offline — no real LLM calls, no network (D-07, TEST-01).
Real smoke test: tests/integration/test_real_llm_smoke.py (skipif key absent).
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
```

---

#### Guard test pattern

Source: pattern inferred from `brave/clients/places.py` lines 88–96 and
`tests/integration/test_cost_guard.py` pytest.raises usage:

```python
def test_guard_raises_when_run_real_externals_false(monkeypatch):
    """RealLLMClient.__init__ raises RuntimeError when run_real_externals=False."""
    monkeypatch.delenv("RUN_REAL_EXTERNALS", raising=False)
    from brave.config.settings import LLMConfig
    from brave.clients.llm import RealLLMClient

    with pytest.raises(RuntimeError, match="run_real_externals=False"):
        RealLLMClient(config=LLMConfig())
```

---

#### Deny enforcement test pattern

Source: Research CONTEXT.md Code Examples "Deny-enforcement unit test pattern":

```python
async def test_deny_block_present_in_openrouter_request(monkeypatch):
    """provider.data_collection='deny' must appear in every extract() extra_body."""
    monkeypatch.setenv("RUN_REAL_EXTERNALS", "true")
    monkeypatch.setenv("BRAVE_LLM_OPENROUTER_API_KEY", "test-key")

    from brave.config.settings import LLMConfig
    from brave.clients.llm import RealLLMClient

    config = LLMConfig(openrouter_api_key="test-key")
    client = RealLLMClient(config=config)

    mock_create = AsyncMock(return_value=(MagicMock(), MagicMock(usage=None)))
    with patch.object(client._instructor_client, "create_with_completion", mock_create):
        await client.extract(prompt="test", schema=MagicMock(__name__="Schema"))

    call_kwargs = mock_create.call_args.kwargs
    assert call_kwargs["extra_body"]["provider"]["data_collection"] == "deny"
```

---

#### Cost-guard wiring test pattern

Source: `tests/integration/test_cost_guard.py` (fake_redis fixture) +
`brave/observability/llm_tracker.py` lines 81–107 (expected helpers called):

```python
async def test_cost_guard_invoked_when_redis_provided(monkeypatch, fake_redis, db_session):
    """extract() calls pre_dispatch_check + writes LLMGeneration row when deps provided."""
    monkeypatch.setenv("RUN_REAL_EXTERNALS", "true")
    monkeypatch.setenv("BRAVE_LLM_OPENROUTER_API_KEY", "test-key")

    from brave.config.settings import LLMConfig
    from brave.clients.llm import RealLLMClient
    from brave.core.models import LLMGeneration
    from brave.observability.cost_guard import pre_dispatch_check  # noqa: F401

    config = LLMConfig(openrouter_api_key="test-key", usd_daily_budget=10.0)
    fake_result = MagicMock()
    fake_usage = MagicMock(prompt_tokens=100, completion_tokens=50, model_extra={"cost": 0.002})
    fake_raw = MagicMock(usage=fake_usage)

    client = RealLLMClient(config=config, redis_client=fake_redis, session=db_session, lane="test")

    with patch.object(client._instructor_client, "create_with_completion",
                      AsyncMock(return_value=(fake_result, fake_raw))):
        await client.extract(prompt="test", schema=MagicMock(__name__="Schema"))

    # Verify LLMGeneration row was added
    rows = db_session.query(LLMGeneration).all()
    assert len(rows) == 1
    assert rows[0].usd_cost > 0
    assert rows[0].lane == "test"
```

---

#### Opt-in smoke test pattern (skipif)

```python
import os
import pytest

_HAS_KEY = bool(os.environ.get("BRAVE_LLM_OPENROUTER_API_KEY"))

@pytest.mark.skipif(not _HAS_KEY, reason="BRAVE_LLM_OPENROUTER_API_KEY not set — real smoke opt-in")
async def test_smoke_real_extract():
    """Real extract() call against OpenRouter/DeepSeek (opt-in only)."""
    ...
```

---

## Shared Patterns

### Guard pattern (`run_real_externals` check in `__init__`)

**Source:** `brave/clients/places.py` lines 88–96 + `brave/clients/apify.py` lines 82–90
**Apply to:** `brave/clients/llm.py` `RealLLMClient.__init__`

```python
# brave/clients/places.py lines 89–96:
from brave.config.settings import AppConfig

if not AppConfig().run_real_externals:
    raise RuntimeError(
        "RealPlacesClient: run_real_externals=False — "
        "use FakePlacesClient in default test suite. "
        "Set BRAVE_RUN_REAL_EXTERNALS=true to enable real API calls."
    )
```

Note: the error message in the new `RealLLMClient` uses `RUN_REAL_EXTERNALS` (no `BRAVE_` prefix)
because `AppConfig.run_real_externals` has `env_prefix=""` (settings.py line 238).
The three existing clients (`places.py`, `apify.py`, `whatsapp.py`) have the wrong prefix in their
error strings — that is exactly the D-06 footgun being fixed.

---

### Tenacity retry decorator

**Source:** `brave/clients/places.py` lines 124–129 (verbatim)
**Apply to:** the internal `_call_slug()` helper method of `RealLLMClient` (wraps per-slug create_with_completion)

```python
@retry(
    retry=retry_if_exception(_is_openai_retryable),  # WR-01: transient only
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
```

---

### structlog logger initialization

**Source:** `brave/clients/apify.py` line 29 (identical in every client module)
**Apply to:** `brave/clients/llm.py` module level

```python
logger = structlog.get_logger(__name__)
```

Log level conventions observed in analogs:
- `logger.info(...)` on success (e.g., `apify.py` line 178: `apify_ig_scrape_ok`)
- `logger.warning(...)` on slug fallback (e.g., Research Pattern 4 slug loop: `llm_slug_unavailable`)
- `logger.error(...)` on caught exception before re-raise (e.g., `places.py` lines 157, 165)

---

### LLMGeneration row fields (T-02-04 — no prompt content)

**Source:** `brave/observability/llm_tracker.py` lines 95–104 + `brave/core/models.py` lines 245–256

```python
# brave/observability/llm_tracker.py lines 95–104 (exact field set to copy):
generation = LLMGeneration(
    id=uuid.uuid4(),
    lane=lane,
    model_slug=model_slug,
    resolved_provider=model_slug,  # Phase 6: same as slug; real provider from headers later
    prompt_tokens=prompt_tokens,
    completion_tokens=completion_tokens,
    usd_cost=usd_cost,
)
session.add(generation)
session.flush()
```

`LLMGeneration.__tablename__ = "llm_generations"` (models.py line 243).
Fields: id (uuid), lane (str64), model_slug (str128), resolved_provider (str128 nullable),
prompt_tokens (int), completion_tokens (int), usd_cost (Numeric 10,6), created_at (server_default).

---

### Cost guard helpers

**Source:** `brave/observability/cost_guard.py` lines 47–92
**Apply to:** `brave/clients/llm.py` `extract()` and `generate()` (when `redis_client` is present)

```python
# brave/observability/cost_guard.py lines 47–67:
def pre_dispatch_check(redis_client: Redis, config: LLMConfig) -> None:
    """Raises CostGuardError if daily counter >= usd_daily_budget."""
    key = _daily_key()
    raw = redis_client.get(key)
    current = float(raw) if raw is not None else 0.0
    if current >= config.usd_daily_budget:
        raise CostGuardError(...)

# brave/observability/cost_guard.py lines 70–92:
def record_spend(redis_client: Redis, usd_amount: float) -> float:
    """Atomic INCRBYFLOAT + TTL set to end-of-day."""
    key = _daily_key()
    new_total = float(redis_client.incrbyfloat(key, usd_amount))
    ttl = redis_client.ttl(key)
    if ttl < 0:
        redis_client.expire(key, _seconds_until_midnight())
    return new_total
```

---

### `fake_redis` fixture (for new unit test)

**Source:** `tests/conftest.py` lines 106–112

```python
@pytest.fixture
def fake_redis() -> fakeredis.FakeRedis:
    """In-process FakeRedis instance for unit tests. State is reset per test."""
    return fakeredis.FakeRedis()
```

The `fake_redis` fixture is already in the shared conftest — no duplication needed in the new test file.

---

## Docstring Footgun Fixes (D-06)

These are string-only edits. No pattern extraction needed — the target strings
and replacement strings are fully specified in RESEARCH.md Pattern 8.

| File | Line | Replace | With |
|------|------|---------|------|
| `brave/clients/places.py` | 95 | `"Set BRAVE_RUN_REAL_EXTERNALS=true to enable real API calls."` | `"Set RUN_REAL_EXTERNALS=true to enable real API calls."` |
| `brave/clients/apify.py` | 89 | `"Set BRAVE_RUN_REAL_EXTERNALS=true to enable real API calls."` | `"Set RUN_REAL_EXTERNALS=true to enable real API calls."` |
| `brave/clients/whatsapp.py` | 13 | `requires BRAVE_RUN_REAL_EXTERNALS=true` | `requires RUN_REAL_EXTERNALS=true` |
| `brave/clients/whatsapp.py` | 66 | `Requires BRAVE_RUN_REAL_EXTERNALS=true` | `Requires RUN_REAL_EXTERNALS=true` |
| `brave/clients/whatsapp.py` | 121 | `RuntimeError: If BRAVE_RUN_REAL_EXTERNALS is not True.` | `RuntimeError: If RUN_REAL_EXTERNALS is not True.` |
| `brave/clients/whatsapp.py` | 129 | `"TwilioWhatsAppClient.send_template requires BRAVE_RUN_REAL_EXTERNALS=true."` | `"TwilioWhatsAppClient.send_template requires RUN_REAL_EXTERNALS=true."` |
| `tests/integration/test_atrativos_lane_e2e.py` | 7 | `BRAVE_RUN_REAL_EXTERNALS must be absent / False.` | `RUN_REAL_EXTERNALS must be absent / False.` |

**Rationale:** `AppConfig.model_config = SettingsConfigDict(env_prefix="")` (settings.py line 238),
so `run_real_externals` resolves from bare `RUN_REAL_EXTERNALS`, not `BRAVE_RUN_REAL_EXTERNALS`.
Following the wrong var in docs silently keeps fake clients active — operator footgun.

---

## No Analog Found

None. All files have clear analogs in the codebase.

---

## Pipeline Call Sites (context for planner)

The four existing call sites in `brave/tasks/pipeline.py` that currently import the phantom module
are at lines 667–668, 800–801, 1237–1238, and 1430–1431. All use the same construction:

```python
# brave/tasks/pipeline.py lines 665–671 (representative — same at all four sites):
if app_config.run_real_externals:
    from brave.clients.llm import RealLLMClient  # type: ignore[import]
    llm_client = RealLLMClient(config=app_config.llm)
else:
    from tests.fakes.fake_llm import FakeLLMClient
    llm_client = FakeLLMClient()
```

All four call sites stay **unchanged** once `brave/clients/llm.py` exists. The `# type: ignore[import]`
comment must be removed from each site after the module is created.

---

## Metadata

**Analog search scope:** `brave/clients/`, `brave/observability/`, `brave/core/models.py`,
`brave/config/settings.py`, `tests/fakes/`, `tests/unit/`, `tests/integration/`
**Files read:** 12 source files
**Pattern extraction date:** 2026-06-17
