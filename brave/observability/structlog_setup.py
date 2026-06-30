"""Structlog configuration for the Brave pipeline.

`configure_structlog(redis)` wires a Redis log-buffer processor (when Redis is
available) and a ConsoleRenderer into the global structlog chain.

## Source caching (W1 — hot-path guard)
The Redis-buffer processor must NOT call `redis.get("brave:engine:source")` on
every log event — that would add a synchronous Redis round-trip to every
structlog call. Instead, the processor caches the resolved source in a
mutable closure dict and refreshes it at most once every `_SOURCE_CACHE_TTL`
seconds. On RedisError the cached value is preserved and the TTL is reset so
the next log call does not immediately hammer Redis again.

## Idempotency (W2 — double-config guard)
`configure_structlog` is guarded by a module-level `_configured` flag. Calling
it a second time is a no-op — idempotent for both the FastAPI lifespan and the
Celery `worker_process_init` signal (each process calls configure once). We
also set `cache_logger_on_first_use=False` to avoid the cache-trap: with
`True`, loggers cached before the first `configure()` call would keep the
pre-configure chain, making pre-configure emissions silently bypass the buffer.
`False` means every `get_logger()` call resolves the current chain — minimal
overhead given the small number of logger instances in this codebase.

## Offline / test behaviour
When `redis=None` (BRAVE_USE_FAKEREDIS=1 or Redis unavailable), only the
ConsoleRenderer is installed — no buffer processor. This keeps the offline test
suite fully functional without any Redis dependency.
"""

from __future__ import annotations

import time
from typing import Any

import structlog

from brave.observability.log_buffer import _decode, append_log

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SOURCE_CACHE_TTL: float = 30.0  # seconds between redis.get("brave:engine:source") calls
_SOURCE_KEY = "brave:engine:source"

# ---------------------------------------------------------------------------
# Module-level idempotency guard (W2)
# ---------------------------------------------------------------------------

_configured: bool = False


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _make_buffer_processor(redis: Any):
    """Return a structlog processor that appends every event to the Redis log ring buffer.

    Source is resolved from Redis (brave:engine:source) at most once per
    _SOURCE_CACHE_TTL seconds — never on every log call (W1). A mutable dict
    closure holds the cached state; no module-level mutation needed.

    The processor is fail-silent: any exception (RedisError, serialisation
    error, …) is swallowed and the original event_dict is returned unchanged
    so the rest of the processor chain (ConsoleRenderer) still runs.
    """
    _state: dict[str, Any] = {"source": "default", "ts": 0.0}

    def _processor(logger: Any, method_name: str, event_dict: dict) -> dict:
        now = time.monotonic()
        # Refresh source cache only when TTL has elapsed
        if now - _state["ts"] > _SOURCE_CACHE_TTL:
            try:
                raw = redis.get(_SOURCE_KEY)
                _state["source"] = _decode(raw) or "default"
            except Exception:
                pass  # Keep cached source on Redis error
            # Always reset the timestamp so we don't hammer Redis on every call
            # when Redis is temporarily unreachable
            _state["ts"] = now

        try:
            append_log(redis, _state["source"], dict(event_dict))
        except Exception:
            pass  # Buffer failures must never crash the caller (T-ks0-03)

        return event_dict  # pass event through — ConsoleRenderer still runs

    return _processor


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def configure_structlog(redis: Any = None) -> None:
    """Configure the global structlog chain — idempotent (W2).

    Wires:
      1. add_log_level — adds "level" key to event_dict
      2. add_logger_name — adds "logger" key
      3. TimeStamper(fmt="iso") — adds "timestamp" key
      4. _make_buffer_processor(redis) — Redis ring-buffer write (omitted when redis=None)
      5. ConsoleRenderer — human-readable stdout output

    cache_logger_on_first_use=False (W2): avoids stale-chain trap where loggers
    obtained before the first configure() call cache the pre-configure chain.

    Safe to call multiple times: only the first call applies the configuration.
    """
    global _configured
    if _configured:
        return

    processors: list[Any] = [
        structlog.stdlib.add_log_level,
        # NOTE: structlog.stdlib.add_logger_name is intentionally omitted.
        # It requires a stdlib logging.Logger (.name attribute) but we use
        # PrintLoggerFactory (no .name). Using it would raise AttributeError
        # when a structlog.get_logger(__name__) logger emits events in test
        # contexts where configure_structlog runs via lifespan before tests
        # that don't use the lifespan context manager.
        structlog.processors.TimeStamper(fmt="iso"),
    ]

    if redis is not None:
        processors.append(_make_buffer_processor(redis))

    processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.BoundLogger,
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,  # W2: avoids cache-trap on pre-configure loggers
    )

    _configured = True
