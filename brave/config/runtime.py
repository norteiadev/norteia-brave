"""Runtime config overlay — env AppConfig + config_settings DB rows (Phase D).

The pydantic-settings hierarchy in ``brave.config.settings`` is env-driven and
immutable at process start. Phase D adds an operator-tunable overlay: the sparse
``config_settings`` table (brave.core.models.ConfigSetting) holds dotted keys
whose values are layered on top of the env-bootstrapped :class:`AppConfig`.

Public surface
--------------
- :func:`load_effective_config` — ``AppConfig()`` bootstrapped from env, then
  overlaid with every ``config_settings`` row. Absent rows → the effective config
  equals the env defaults (behavior-neutral). Optionally memoizes the resulting
  snapshot in Redis (``brave:config:snapshot``); readers that hold no Redis client
  simply recompute from the DB each call (a cheap ~handful-of-rows SELECT).
- :func:`bust_config_snapshot` — delete the Redis snapshot; MUST be called by any
  writer that mutates ``config_settings`` so the next read recomputes.
- :func:`enabled_sources` — the registered-AND-enabled source lanes.
- :func:`seed_default_config` — idempotent seed of default rows (see its docstring
  for the reset-brave-db interaction).

Overlaid keys (everything else is ignored, forward-compat)
----------------------------------------------------------
- ``score.threshold_mar``            → AppConfig.score.threshold_mar
- ``score.weight_origem``            → AppConfig.score.weight_origem
- ``score.weight_completude``        → AppConfig.score.weight_completude
- ``score.weight_corroboracao``      → AppConfig.score.weight_corroboracao
- ``score.weight_atualidade``        → AppConfig.score.weight_atualidade
- ``score.weight_validacao_humana``  → AppConfig.score.weight_validacao_humana
- ``source.<name>.enabled``          → AppConfig.sources[name]
- ``engine.mode``                    → AppConfig.engine.mode

Import posture (D-18): this module lives in ``brave.config`` and imports only
``brave.config.settings`` and ``brave.core.models`` — never ``brave.domains`` or
``brave.tasks``. The score-path call-sites call ``load_effective_config(session)``
with NO Redis client, so wiring it in introduces ZERO Redis dependency on the
scoring path (offline posture preserved).
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from brave.config.settings import AppConfig
from brave.core.models import ConfigSetting

if TYPE_CHECKING:  # pragma: no cover - typing only
    from redis import Redis

# Redis key holding the memoized effective-config snapshot (JSON of AppConfig).
SNAPSHOT_KEY = "brave:config:snapshot"

# Dotted config_settings key → ScoreConfig attribute name.
_SCORE_OVERLAY_KEYS: dict[str, str] = {
    "score.threshold_mar": "threshold_mar",
    "score.weight_origem": "weight_origem",
    "score.weight_completude": "weight_completude",
    "score.weight_corroboracao": "weight_corroboracao",
    "score.weight_atualidade": "weight_atualidade",
    "score.weight_validacao_humana": "weight_validacao_humana",
}

_SOURCE_PREFIX = "source."
_SOURCE_SUFFIX = ".enabled"
_ENGINE_MODE_KEY = "engine.mode"
_DESC_ENRICH_KEY = "description_enrichment_enabled"
_PLACES_ENRICH_KEY = "places_enrichment_enabled"


# ---------------------------------------------------------------------------
# Overlay read + apply
# ---------------------------------------------------------------------------


def _read_overlay_rows(session: Session) -> dict[str, Any]:
    """Return every config_settings row as ``{dotted_key: unwrapped_value}``.

    Each row's ``value`` column is the ``{"v": <any>}`` wrapper; rows missing the
    wrapper are skipped defensively (never crash the read path on a malformed row).
    """
    rows = session.execute(select(ConfigSetting.key, ConfigSetting.value)).all()
    overlays: dict[str, Any] = {}
    for key, value in rows:
        if isinstance(value, dict) and "v" in value:
            overlays[key] = value["v"]
    return overlays


def _apply_overlay(base: AppConfig, overlays: dict[str, Any]) -> AppConfig:
    """Layer the dotted overlay rows onto ``base`` via ``model_copy(update=...)``.

    Returns ``base`` unchanged when there is nothing to overlay — so a config with
    zero rows (or only unknown keys) is returned untouched.
    """
    if not overlays:
        return base

    score_update: dict[str, Any] = {}
    sources_update: dict[str, bool] = dict(base.sources)
    engine_update: dict[str, Any] = {}
    desc_enrich: bool | None = None
    places_enrich: bool | None = None

    for dotted, value in overlays.items():
        attr = _SCORE_OVERLAY_KEYS.get(dotted)
        if attr is not None:
            score_update[attr] = value
        elif dotted.startswith(_SOURCE_PREFIX) and dotted.endswith(_SOURCE_SUFFIX):
            name = dotted[len(_SOURCE_PREFIX) : -len(_SOURCE_SUFFIX)]
            if name:
                sources_update[name] = bool(value)
        elif dotted == _ENGINE_MODE_KEY:
            engine_update["mode"] = value
        elif dotted == _DESC_ENRICH_KEY:
            desc_enrich = bool(value)
        elif dotted == _PLACES_ENRICH_KEY:
            places_enrich = bool(value)
        # Unknown keys are ignored (forward-compat with future config surfaces).

    updates: dict[str, Any] = {}
    if score_update:
        updates["score"] = base.score.model_copy(update=score_update)
    if sources_update != base.sources:
        updates["sources"] = sources_update
    if engine_update:
        updates["engine"] = base.engine.model_copy(update=engine_update)
    if desc_enrich is not None and desc_enrich != base.description_enrichment_enabled:
        updates["description_enrichment_enabled"] = desc_enrich
    if places_enrich is not None and places_enrich != base.places_enrichment_enabled:
        updates["places_enrichment_enabled"] = places_enrich

    if not updates:
        return base
    return base.model_copy(update=updates)


# ---------------------------------------------------------------------------
# Snapshot cache (optional; only used when a Redis client is supplied)
# ---------------------------------------------------------------------------


def _read_snapshot(redis: Redis) -> AppConfig | None:
    """Return the cached effective config, or None on miss/decode error.

    Any decode failure (schema drift, corrupt value) is treated as a cache miss so
    the caller recomputes from the DB — the cache can never surface a wrong config,
    only a stale-but-valid one (which the bust-on-write contract prevents).
    """
    try:
        raw = redis.get(SNAPSHOT_KEY)
    except Exception:
        return None
    if not raw:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    try:
        return AppConfig.model_validate_json(raw)
    except Exception:
        return None


def _write_snapshot(redis: Redis, config: AppConfig) -> None:
    """Store the effective config JSON under SNAPSHOT_KEY (best-effort)."""
    # Caching is an optimization — never let a Redis blip fail a config read.
    with contextlib.suppress(Exception):
        redis.set(SNAPSHOT_KEY, config.model_dump_json())


def bust_config_snapshot(redis: Redis) -> None:
    """Delete the cached effective-config snapshot.

    MUST be called by any writer that mutates ``config_settings`` (after the DB
    commit) so the next :func:`load_effective_config` recomputes the overlay.
    """
    redis.delete(SNAPSHOT_KEY)


def upsert_config(
    session: Session, updates: dict[str, Any], *, updated_by: str = "steward"
) -> None:
    """Insert-or-update dotted ``config_settings`` keys from ``updates``.

    Each value is stored under the canonical ``{"v": <value>}`` wrapper so any JSON
    scalar (including ``False``/``0``/``None``) round-trips unambiguously. Existing
    rows are updated in place (which trips the ORM ``onupdate`` bump on flush); absent
    keys are inserted.

    Flushes but does NOT commit and does NOT bust the snapshot cache — the caller
    owns the transaction boundary and MUST call :func:`bust_config_snapshot` AFTER a
    successful commit so a rolled-back write never leaves a busted-then-stale cache.
    Shared by the config PATCH endpoint and ``engine.set_mode``'s durable persist.
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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_effective_config(session: Session, redis: Redis | None = None) -> AppConfig:
    """Return the effective AppConfig: env defaults overlaid with config_settings.

    Args:
        session: a sync SQLAlchemy Session (FastAPI ``get_db`` / Celery
            ``_get_session`` both yield one) used to read the overlay rows.
        redis: optional Redis client. When supplied, the resulting snapshot is
            memoized under ``brave:config:snapshot`` and served from cache on the
            next call (until :func:`bust_config_snapshot`). When ``None`` (the
            score-path call-sites), the config is always recomputed from the DB and
            NO Redis dependency is introduced.

    Behavior-neutral guarantee: with no config_settings rows — or with rows seeded
    to the current env-effective values (:func:`seed_default_config`) — the return
    value is byte-for-byte the env-bootstrapped ``AppConfig()``.
    """
    if redis is not None:
        cached = _read_snapshot(redis)
        if cached is not None:
            return cached

    effective = _apply_overlay(AppConfig(), _read_overlay_rows(session))

    if redis is not None:
        _write_snapshot(redis, effective)
    return effective


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
    return {
        "score.threshold_mar": config.score.threshold_mar,
        "score.weight_origem": config.score.weight_origem,
        "score.weight_completude": config.score.weight_completude,
        "score.weight_corroboracao": config.score.weight_corroboracao,
        "score.weight_atualidade": config.score.weight_atualidade,
        "score.weight_validacao_humana": config.score.weight_validacao_humana,
        "source.default.enabled": config.sources.get("default", False),
        "source.tripadvisor.enabled": config.sources.get("tripadvisor", True),
        "engine.mode": config.engine.mode,
        "description_enrichment_enabled": config.description_enrichment_enabled,
        "places_enrichment_enabled": config.places_enrichment_enabled,
    }


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
    return inserted
