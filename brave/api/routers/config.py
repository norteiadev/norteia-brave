"""Operator-tunable runtime config surface (Phase D).

Two endpoints over the ``config_settings`` overlay (brave.config.runtime):

  GET   /api/v1/config   — the effective config snapshot (Bearer)
  PATCH /api/v1/config   — upsert dotted-key config overrides (steward or Bearer)

The effective config is the env-bootstrapped :class:`AppConfig` overlaid with every
``config_settings`` row (brave.config.runtime.load_effective_config). GET returns that
snapshot with secrets redacted. PATCH validates the requested changes (reliability weight-sum
== 100 whenever any weight is touched; thresholds ∈ [0, 100]; known keys only), upserts
the rows, writes an audit trail row, commits, then busts the Redis snapshot cache so the
next read recomputes the overlay.

Import posture (D-18): a router in ``brave.api`` — imports config/runtime/observability,
never ``brave.domains``/``brave.tasks``. All mutation side effects order as the other
mutation routes do: DB commit BEFORE the Redis cache-bust, so a rolled-back write never
leaves a busted-then-stale cache.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Body, Depends, HTTPException
from redis import Redis
from sqlalchemy.orm import Session

from brave.api.deps import get_db, get_redis, require_bearer, require_steward_or_bearer
from brave.config.runtime import (
    bust_config_snapshot,
    load_effective_config,
    upsert_config,
)
from brave.config.settings import AppConfig
from brave.core import engine as collection_engine
from brave.observability.audit import write_audit

logger = structlog.get_logger(__name__)
router = APIRouter()

# ---------------------------------------------------------------------------
# Settable-key contract (mirrors brave.config.runtime overlay keys)
# ---------------------------------------------------------------------------

# The five reliability weight keys (dotted → ScoreConfig attribute). These must sum to 100.
_WEIGHT_KEYS: dict[str, str] = {
    "score.weight_origem": "weight_origem",
    "score.weight_completude": "weight_completude",
    "score.weight_corroboracao": "weight_corroboracao",
    "score.weight_atualidade": "weight_atualidade",
    "score.weight_validacao_humana": "weight_validacao_humana",
}
# Threshold keys (dotted → ScoreConfig attribute). Each must be within [0, 100].
_THRESHOLD_KEYS: dict[str, str] = {"score.threshold_mar": "threshold_mar"}

_ENGINE_MODE_KEY = "engine.mode"
_SOURCE_PREFIX = "source."
_SOURCE_SUFFIX = ".enabled"

# Secret paths in the AppConfig snapshot to redact on GET (never echo secrets).
_SECRET_PATHS: tuple[tuple[str, str], ...] = (
    ("llm", "openrouter_api_key"),
    ("llm", "anthropic_api_key"),
    ("whatsapp", "twilio_auth_token"),
    ("whatsapp", "twilio_account_sid"),
    ("whatsapp", "messaging_service_sid"),
    ("tripadvisor", "proxy_url"),
)


def _is_source_key(key: str) -> bool:
    return (
        key.startswith(_SOURCE_PREFIX)
        and key.endswith(_SOURCE_SUFFIX)
        and len(key) > len(_SOURCE_PREFIX) + len(_SOURCE_SUFFIX)
    )


def _redact(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Replace any populated secret field with '***' (empty stays '' → 'unset')."""
    for section, field in _SECRET_PATHS:
        block = snapshot.get(section)
        if isinstance(block, dict) and block.get(field):
            block[field] = "***"
    return snapshot


def _current_value(cfg: AppConfig, dotted: str) -> Any:
    """The current effective value for a dotted key (for the audit before_state)."""
    if dotted in _WEIGHT_KEYS:
        return getattr(cfg.score, _WEIGHT_KEYS[dotted])
    if dotted in _THRESHOLD_KEYS:
        return getattr(cfg.score, _THRESHOLD_KEYS[dotted])
    if dotted == _ENGINE_MODE_KEY:
        return cfg.engine.mode
    if _is_source_key(dotted):
        name = dotted[len(_SOURCE_PREFIX) : -len(_SOURCE_SUFFIX)]
        return cfg.sources.get(name)
    return None


def _is_number(value: Any) -> bool:
    """True for a JSON number (int/float) but NOT bool (bool is an int subclass)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _validate_updates(db: Session, updates: dict[str, Any]) -> None:
    """Reject an invalid PATCH body with 422 BEFORE any write.

    Rules:
      - body must be a non-empty object of dotted-key → value;
      - only settable keys (the six score knobs, ``engine.mode``,
        ``source.<name>.enabled``) are accepted — unknown keys are rejected so a
        typo never silently persists a dead row;
      - each weight/threshold must be a number in [0, 100];
      - ``engine.mode`` must be a valid operator mode;
      - ``source.<name>.enabled`` must be a bool;
      - whenever ANY weight is touched, the RESULTING weight set (the update merged
        over the current effective config) must sum to 100 — a single-weight edit that
        breaks the reliability invariant is rejected.
    """
    if not isinstance(updates, dict) or not updates:
        raise HTTPException(
            status_code=422,
            detail="body must be a non-empty object of dotted config keys to values",
        )

    for key, value in updates.items():
        if key in _WEIGHT_KEYS or key in _THRESHOLD_KEYS:
            if not _is_number(value):
                raise HTTPException(
                    status_code=422, detail=f"{key} must be a number"
                )
            if not (0 <= float(value) <= 100):
                raise HTTPException(
                    status_code=422, detail=f"{key} must be within [0, 100]"
                )
        elif key == _ENGINE_MODE_KEY:
            if value not in collection_engine._VALID_MODES:
                raise HTTPException(
                    status_code=422,
                    detail="engine.mode must be 'LIGADO', 'PAUSADO', or 'DESLIGADO'",
                )
        elif _is_source_key(key):
            if not isinstance(value, bool):
                raise HTTPException(
                    status_code=422, detail=f"{key} must be a boolean"
                )
            source_name = key[len(_SOURCE_PREFIX) : -len(_SOURCE_SUFFIX)]
            if source_name not in collection_engine._VALID_SOURCES:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"unknown source {source_name!r}: must be one of "
                        f"{sorted(collection_engine._VALID_SOURCES)}"
                    ),
                )
        else:
            raise HTTPException(
                status_code=422, detail=f"unknown or non-settable config key: {key!r}"
            )

    # reliability weight-sum invariant: only enforced when a weight is actually touched.
    touched_weights = [k for k in updates if k in _WEIGHT_KEYS]
    if touched_weights:
        current = load_effective_config(db)  # no redis → always the live DB overlay
        merged = {
            attr: float(updates[dotted]) if dotted in updates else getattr(current.score, attr)
            for dotted, attr in _WEIGHT_KEYS.items()
        }
        total = sum(merged.values())
        if abs(total - 100.0) > 0.01:
            raise HTTPException(
                status_code=422,
                detail=(
                    "score weights (origem + completude + corroboracao + atualidade + "
                    f"validacao_humana) must sum to 100 — got {total:g}"
                ),
            )


@router.get("/api/v1/config", dependencies=[Depends(require_bearer)])
def get_config_snapshot(
    db: Session = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> dict[str, Any]:
    """Return the effective config snapshot (env defaults + config_settings overlay).

    Secrets are redacted (never echoed). ``redis`` warms/serves the snapshot cache.
    """
    effective = load_effective_config(db, redis)
    return _redact(effective.model_dump())


@router.patch(
    "/api/v1/config",
    status_code=200,
    dependencies=[Depends(require_steward_or_bearer)],
)
def update_config(
    body: dict[str, Any] = Body(default={}),
    db: Session = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> dict[str, Any]:
    """Upsert dotted-key config overrides, audit the change, and bust the cache.

    Validate → capture before-state → upsert rows → write audit → COMMIT → bust the
    Redis snapshot (after commit, so a rolled-back write never busts a valid cache) →
    return the fresh redacted effective snapshot.
    """
    _validate_updates(db, body)

    # Capture the prior effective values of the touched keys for the audit trail.
    before_cfg = load_effective_config(db)
    before_state = {key: _current_value(before_cfg, key) for key in body}

    upsert_config(db, body, updated_by="steward")
    write_audit(
        session=db,
        action="config_updated",
        before_state=before_state,
        after_state=dict(body),
        actor="steward",
    )
    db.commit()

    # Side effect AFTER commit (mirrors the other mutation routes): bust the memoized
    # snapshot so the next read recomputes, then recompute+re-warm the cache for GET.
    bust_config_snapshot(redis)
    effective = load_effective_config(db, redis)

    logger.info("config_updated", keys=sorted(body.keys()))
    return {"updated": sorted(body.keys()), "config": _redact(effective.model_dump())}
