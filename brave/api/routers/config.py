"""Operator-tunable runtime config surface (Phase D).

Two endpoints over the ``config_settings`` overlay (brave.config.runtime):

  GET   /api/v1/config   — the effective config snapshot (Bearer)
  PATCH /api/v1/config   — upsert dotted-key config overrides (steward or Bearer)

The effective config is the env-bootstrapped :class:`AppConfig` overlaid with every
``config_settings`` row (brave.config.runtime.load_effective_config). GET returns that
snapshot with secrets redacted. PATCH validates the requested changes (reliability weight-sum
== 100 whenever any weight is touched; thresholds ∈ [0, 100]; known keys only), upserts
the rows, writes an audit trail row and commits; the commit drops the cached overlay
(brave.config.runtime's after_commit listener) so the next read recomputes it.

Import posture (D-18): a router in ``brave.api`` — imports config/runtime/observability,
never ``brave.domains``/``brave.tasks``. The settable keys, their validation kind and
the AppConfig field each overrides all come from ``brave.config.runtime.CONFIG_KEYS``.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Body, Depends, HTTPException
from redis import Redis
from sqlalchemy.orm import Session

from brave.api.deps import get_db, get_redis, require_bearer, require_steward_or_bearer
from brave.config.runtime import CONFIG_KEYS, load_effective_config, upsert_config
from brave.core import engine as collection_engine
from brave.observability.audit import write_audit

logger = structlog.get_logger(__name__)
router = APIRouter()

# The five reliability weight keys (kind "weight"). These must sum to 100.
_WEIGHT_KEYS = [entry for entry in CONFIG_KEYS.values() if entry.kind == "weight"]

# Secret paths in the AppConfig snapshot to redact on GET (never echo secrets).
_SECRET_PATHS: tuple[tuple[str, str], ...] = (
    ("llm", "openrouter_api_key"),
    ("llm", "anthropic_api_key"),
    ("llm", "gemini_api_key"),
    ("whatsapp", "twilio_auth_token"),
    ("whatsapp", "twilio_account_sid"),
    ("whatsapp", "messaging_service_sid"),
    ("tripadvisor", "proxy_url"),
)


def _redact(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Replace any populated secret field with '***' (empty stays '' → 'unset')."""
    for section, field in _SECRET_PATHS:
        block = snapshot.get(section)
        if isinstance(block, dict) and block.get(field):
            block[field] = "***"
    return snapshot


def _is_number(value: Any) -> bool:
    """True for a JSON number (int/float) but NOT bool (bool is an int subclass)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _validate_updates(db: Session, updates: dict[str, Any]) -> None:
    """Reject an invalid PATCH body with 422 BEFORE any write.

    Rules:
      - body must be a non-empty object of dotted-key → value;
      - only registered keys (``CONFIG_KEYS``) are accepted — unknown keys are
        rejected so a typo never silently persists a dead row;
      - each weight/threshold must be a number in [0, 100];
      - ``engine.mode`` must be a valid operator mode;
      - every flag (``source.<name>.enabled`` included) must be a bool;
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
        entry = CONFIG_KEYS.get(key)
        if entry is None:
            raise HTTPException(
                status_code=422, detail=f"unknown or non-settable config key: {key!r}"
            )
        if entry.kind in ("weight", "threshold"):
            if not _is_number(value):
                raise HTTPException(
                    status_code=422, detail=f"{key} must be a number"
                )
            if not (0 <= float(value) <= 100):
                raise HTTPException(
                    status_code=422, detail=f"{key} must be within [0, 100]"
                )
        elif entry.kind == "mode":
            if value not in collection_engine.VALID_MODES:
                raise HTTPException(
                    status_code=422,
                    detail="engine.mode must be 'LIGADO', 'PAUSADO', or 'DESLIGADO'",
                )
        elif not isinstance(value, bool):
            raise HTTPException(status_code=422, detail=f"{key} must be a boolean")

    # reliability weight-sum invariant: only enforced when a weight is actually touched.
    if any(entry.key in updates for entry in _WEIGHT_KEYS):
        current = load_effective_config(db)  # no redis → always the live DB overlay
        merged = {
            entry.key: float(updates[entry.key]) if entry.key in updates else entry.read(current)
            for entry in _WEIGHT_KEYS
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
    """Return the effective config (env defaults + config_settings overlay).

    Secrets are redacted (never echoed). ``redis`` warms/serves the overlay cache.
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
) -> dict[str, Any]:
    """Upsert dotted-key config overrides and audit the change.

    Validate → capture before-state → upsert rows → write audit → COMMIT (the commit
    drops the cached overlay) → return the fresh redacted effective config, read from
    the DB.
    """
    _validate_updates(db, body)

    # Capture the prior effective values of the touched keys for the audit trail.
    before_cfg = load_effective_config(db)
    before_state = {key: CONFIG_KEYS[key].read(before_cfg) for key in body}

    upsert_config(db, body, updated_by="steward")
    write_audit(
        session=db,
        action="config_updated",
        before_state=before_state,
        after_state=dict(body),
        actor="steward",
    )
    db.commit()

    effective = load_effective_config(db)

    logger.info("config_updated", keys=sorted(body.keys()))
    return {"updated": sorted(body.keys()), "config": _redact(effective.model_dump())}
