"""Runtime config overlay — env AppConfig + config_settings DB rows (Phase D).

The pydantic-settings hierarchy in ``brave.config.settings`` is env-driven and
immutable at process start. Phase D adds an operator-tunable overlay: the sparse
``config_settings`` table (brave.core.models.ConfigSetting) holds dotted keys
whose values are layered on top of the env-bootstrapped :class:`AppConfig`.

Public surface
--------------
- :data:`CONFIG_KEYS` — the ONE registry of settable keys (dotted key → the AppConfig
  field it overrides + its validation kind). The overlay, the seed, the PATCH
  validation and the engine's durable mode all read it; nothing else lists keys.
- :func:`load_effective_config` — ``AppConfig()`` bootstrapped from env, then
  overlaid with the ``config_settings`` rows. Absent rows → the effective config
  equals the env defaults (behavior-neutral). Optionally memoizes the overlay ROWS
  (never the AppConfig — so no secret ever reaches Redis) under
  ``brave:config:overlay``; readers that hold no Redis client simply recompute from
  the DB each call (a cheap ~handful-of-rows SELECT).
- :func:`upsert_config` — the writer. It marks the Session; the ``after_commit``
  listener below deletes the cached overlay once the write is durable, so no writer
  busts the cache by hand.
- :func:`enabled_sources` — the registered-AND-enabled source lanes.
- :func:`seed_default_config` — idempotent seed of default rows (see its docstring
  for the reset-brave-db interaction).

Import posture (D-18): this module lives in ``brave.config`` and imports only
``brave.config.settings`` and ``brave.core.models`` — never ``brave.domains`` or
``brave.tasks``. The score-path call-sites call ``load_effective_config(session)``
with NO Redis client, so wiring it in introduces ZERO Redis dependency on the
scoring path (offline posture preserved).
"""

from __future__ import annotations

import contextlib
import functools
import json
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from brave.config.settings import AppConfig
from brave.core.models import ConfigSetting

if TYPE_CHECKING:  # pragma: no cover - typing only
    from redis import Redis

logger = structlog.get_logger(__name__)

# Redis key holding the memoized overlay rows (JSON {dotted_key: value}). The old
# ``brave:config:snapshot`` (a serialized AppConfig) is read by no one — DEL it on deploy.
OVERLAY_KEY = "brave:config:overlay"
_OVERLAY_MAX_AGE_SECONDS = 60


# ---------------------------------------------------------------------------
# Key registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConfigKey:
    """One settable ``config_settings`` key.

    ``field`` is the AppConfig path the row overrides (``("score", "threshold_mar")``,
    ``("sources", "tripadvisor")``, ``("places_enrichment_enabled",)``). ``kind`` drives
    the PATCH validation: ``weight`` (number in [0, 100], the five sum to 100),
    ``threshold`` (number in [0, 100]), ``bool``, ``mode`` (an engine operator mode).
    """

    key: str
    field: tuple[str, ...]
    kind: str

    def read(self, config: AppConfig) -> Any:
        """This key's value on ``config`` — the seed default when ``config`` is env-only."""
        value: Any = config
        for part in self.field:
            value = value.get(part, False) if isinstance(value, dict) else getattr(value, part)
        return value


ENGINE_MODE_KEY = "engine.mode"

# Declaration order is the seed order.
CONFIG_KEYS: dict[str, ConfigKey] = {
    k.key: k
    for k in (
        ConfigKey("score.threshold_mar", ("score", "threshold_mar"), "threshold"),
        ConfigKey("score.weight_origem", ("score", "weight_origem"), "weight"),
        ConfigKey("score.weight_completude", ("score", "weight_completude"), "weight"),
        ConfigKey("score.weight_corroboracao", ("score", "weight_corroboracao"), "weight"),
        ConfigKey("score.weight_atualidade", ("score", "weight_atualidade"), "weight"),
        ConfigKey(
            "score.weight_validacao_humana", ("score", "weight_validacao_humana"), "weight"
        ),
        ConfigKey("source.default.enabled", ("sources", "default"), "bool"),
        ConfigKey("source.tripadvisor.enabled", ("sources", "tripadvisor"), "bool"),
        ConfigKey(ENGINE_MODE_KEY, ("engine", "mode"), "mode"),
        ConfigKey("description_enrichment_enabled", ("description_enrichment_enabled",), "bool"),
        ConfigKey("places_enrichment_enabled", ("places_enrichment_enabled",), "bool"),
        ConfigKey(
            "atrativo_description_batch_enabled", ("atrativo_description_batch_enabled",), "bool"
        ),
        ConfigKey(
            "atrativo_description_cascade_enabled",
            ("atrativo_description_cascade_enabled",),
            "bool",
        ),
    )
}


# ---------------------------------------------------------------------------
# Overlay read + apply
# ---------------------------------------------------------------------------


def _read_overlay_rows(session: Session) -> dict[str, Any]:
    """Return the registered config_settings rows as ``{dotted_key: unwrapped_value}``.

    Each row's ``value`` column is the ``{"v": <any>}`` wrapper; rows missing the
    wrapper are skipped defensively (never crash the read path on a malformed row), and
    so are keys outside :data:`CONFIG_KEYS` (forward-compat — and it keeps the cached
    overlay to registered keys only).
    """
    rows = session.execute(select(ConfigSetting.key, ConfigSetting.value)).all()
    overlays: dict[str, Any] = {}
    for key, value in rows:
        if key in CONFIG_KEYS and isinstance(value, dict) and "v" in value:
            overlays[key] = value["v"]
    return overlays


def _apply_overlay(base: AppConfig, overlays: dict[str, Any]) -> AppConfig:
    """Layer the dotted overlay rows onto ``base`` via ``model_copy(update=...)``.

    Every key resolves through :data:`CONFIG_KEYS`; unknown keys are ignored. Returns
    ``base`` unchanged when nothing applies.
    """
    top: dict[str, Any] = {}
    nested: dict[str, dict[str, Any]] = {}
    for dotted, value in overlays.items():
        entry = CONFIG_KEYS.get(dotted)
        if entry is None:
            continue
        if entry.kind == "bool":
            value = bool(value)
        head, *rest = entry.field
        if rest:
            nested.setdefault(head, {})[rest[0]] = value
        else:
            top[head] = value

    updates: dict[str, Any] = dict(top)
    for head, sub in nested.items():
        block = getattr(base, head)
        updates[head] = {**block, **sub} if isinstance(block, dict) else block.model_copy(update=sub)

    if not updates:
        return base
    return base.model_copy(update=updates)


# ---------------------------------------------------------------------------
# Overlay cache (optional; only used when a Redis client is supplied)
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def overlay_redis() -> Redis:
    """The Redis that holds the overlay cache: ``BRAVE_DB_REDIS_URL``, shared by every process.

    Used by the Celery tasks' config read and by the after-commit bust. One client per
    process (its pool is reused); both timeouts bound a hung Redis, since the bust runs
    right after a request's commit. Tests swap it for a fakeredis (tests/conftest.py).
    """
    import redis as _redis_lib  # noqa: PLC0415

    return _redis_lib.from_url(
        os.environ.get("BRAVE_DB_REDIS_URL", "redis://localhost:6379/0"),
        socket_connect_timeout=1,
        socket_timeout=1,
    )


def _read_cached_overlay(redis: Redis) -> dict[str, Any] | None:
    """Return the cached overlay rows, or None on miss/decode error (→ recompute from DB)."""
    try:
        raw = redis.get(OVERLAY_KEY)
    except Exception:
        return None
    if not raw:
        return None
    try:
        overlays = json.loads(raw)
    except Exception:
        return None
    return overlays if isinstance(overlays, dict) else None


def _write_cached_overlay(redis: Redis, overlays: dict[str, Any]) -> None:
    """Store the overlay rows under OVERLAY_KEY (best-effort)."""
    # Caching is an optimization — never let a Redis blip fail a config read.
    # Expiry bounds staleness: a reader that loaded the overlay just before a writer's
    # commit can re-write the OLD rows right after the bust. Without an expiry that stale
    # overlay (e.g. a cost flag the operator just turned off) would be served forever.
    with contextlib.suppress(Exception):
        redis.set(OVERLAY_KEY, json.dumps(overlays), ex=_OVERLAY_MAX_AGE_SECONDS)


# ---------------------------------------------------------------------------
# Write + after-commit invalidation
# ---------------------------------------------------------------------------

# session.info flag: this transaction wrote config_settings rows.
_OVERLAY_DIRTY = "brave.config.overlay_dirty"


def _mark_overlay_dirty(session: Session) -> None:
    # Offline stub sessions have no ``info`` dict (and never commit through SQLAlchemy).
    info = getattr(session, "info", None)
    if isinstance(info, dict):
        info[_OVERLAY_DIRTY] = True


@event.listens_for(Session, "after_commit")
def _bust_overlay_after_commit(session: Session) -> None:
    """Drop the cached overlay once a config write is durable — for every writer.

    After the commit, never before: a rolled-back write must not bust a valid cache.
    Best-effort — a Redis error only logs (the 60 s expiry still bounds staleness).
    """
    if not session.info.pop(_OVERLAY_DIRTY, False):
        return
    try:
        overlay_redis().delete(OVERLAY_KEY)
    except Exception as exc:
        logger.error("config_overlay_bust_failed", error=str(exc))


@event.listens_for(Session, "after_soft_rollback")
def _forget_overlay_dirty(session: Session, previous_transaction: Any) -> None:
    # A SAVEPOINT rollback keeps the outer transaction's config write pending.
    if not previous_transaction.nested:
        session.info.pop(_OVERLAY_DIRTY, None)


def upsert_config(
    session: Session, updates: dict[str, Any], *, updated_by: str = "steward"
) -> None:
    """Insert-or-update dotted ``config_settings`` keys from ``updates``.

    Each value is stored under the canonical ``{"v": <value>}`` wrapper so any JSON
    scalar (including ``False``/``0``/``None``) round-trips unambiguously. Existing
    rows are updated in place (which trips the ORM ``onupdate`` bump on flush); absent
    keys are inserted.

    Flushes but does NOT commit — the caller owns the transaction boundary. The Session
    is marked so the ``after_commit`` listener drops the cached overlay once the caller
    commits (a rollback clears the mark). Shared by the config PATCH endpoint and
    ``engine.set_mode``'s durable persist.
    """
    for key, value in updates.items():
        row = session.get(ConfigSetting, key)
        if row is None:
            session.add(
                ConfigSetting(key=key, value={"v": value}, updated_by=updated_by)
            )
        else:
            row.value = {"v": value}
            row.updated_by = updated_by
    session.flush()
    _mark_overlay_dirty(session)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_effective_config(session: Session, redis: Redis | None = None) -> AppConfig:
    """Return the effective AppConfig: env defaults overlaid with config_settings.

    Args:
        session: a sync SQLAlchemy Session (FastAPI ``get_db`` / Celery
            ``_get_session`` both yield one) used to read the overlay rows.
        redis: optional Redis client. When supplied, the overlay ROWS are memoized
            under ``brave:config:overlay`` and served from cache on the next call
            (until a config write commits, or 60 s). The env half is always rebuilt
            from ``AppConfig()``, so the cache never holds a secret. When ``None``
            (the score-path call-sites), the rows are always read from the DB and NO
            Redis dependency is introduced.

    Behavior-neutral guarantee: with no config_settings rows — or with rows seeded
    to the current env-effective values (:func:`seed_default_config`) — the return
    value equals the env-bootstrapped ``AppConfig()``.
    """
    overlays = _read_cached_overlay(redis) if redis is not None else None
    if overlays is None:
        overlays = _read_overlay_rows(session)
        if redis is not None:
            _write_cached_overlay(redis, overlays)
    return _apply_overlay(AppConfig(), overlays)


def enabled_sources(config: AppConfig) -> list[str]:
    """Return the enabled collection-source lanes, in declaration order.

    Defaults to ``["default", "tripadvisor"]`` (both enabled) unless a
    ``source.<name>.enabled`` overlay row disables one. Consumed by the engine
    source-validation / beat-gating in a later phase; provided here so those
    call-sites have a single source of truth.
    """
    return [name for name, is_enabled in config.sources.items() if is_enabled]


# ---------------------------------------------------------------------------
# Idempotent seed
# ---------------------------------------------------------------------------


def _seed_values(config: AppConfig) -> dict[str, Any]:
    """The default config_settings values, taken from the CURRENT env-effective config.

    Seeding from ``AppConfig()`` (not hardcoded literals) guarantees each seeded row
    equals what the env would produce, so the overlay is a strict no-op even when an
    env override (e.g. BRAVE_SCORE_THRESHOLD_MAR) is set — seeding never changes
    behavior.
    """
    return {key: entry.read(config) for key, entry in CONFIG_KEYS.items()}


def seed_default_config(session: Session, *, updated_by: str = "seed") -> int:
    """Insert the default config_settings rows IF ABSENT — idempotent, safe to re-run.

    For each known key, a row is inserted only when it does not already exist
    (existence check; dialect-agnostic). Existing rows are left untouched, so
    re-running never clobbers an operator's tuned value. Flushes (does NOT commit) —
    the caller/script owns the transaction boundary.

    Values equal the current env-effective config (:func:`_seed_values`), so a
    freshly seeded base is byte-for-byte identical to one with no rows at all
    (behavior-neutral).

    reset-brave-db interaction: the reset script
    (``.claude/skills/reset-brave-db/scripts/reset_db.py``) TRUNCATEs every data
    table including ``config_settings``, emptying the overlay. This seed MUST be run
    AFTER a reset to repopulate the defaults (an empty table is still behavior-neutral
    — the overlay just falls back to env — but a seeded table is the intended
    "carga inicial" baseline and what the config-management surface expects to edit).

    Args:
        session: sync SQLAlchemy Session (caller commits).
        updated_by: audit attribution written to each new row's ``updated_by``.

    Returns:
        The number of rows inserted (0 when all keys already present).
    """
    defaults = _seed_values(AppConfig())
    existing = set(session.execute(select(ConfigSetting.key)).scalars())

    inserted = 0
    for key, raw in defaults.items():
        if key in existing:
            continue
        session.add(ConfigSetting(key=key, value={"v": raw}, updated_by=updated_by))
        inserted += 1

    session.flush()
    if inserted:
        _mark_overlay_dirty(session)
    return inserted
