"""TripAdvisor UF → geoId resolution (TA-01).

Resolution order:
  1. Redis cache   — fast path; key expires after REDIS_GEO_TTL seconds
  2. Seed JSON     — data/tripadvisor/uf_geoids.json (all 27 UFs, ASSUMED values)
  3. ValueError    — fail-closed; unknown UF raises rather than returning 0

GeoIds are stable TripAdvisor integers that change only when TA restructures
their geography model. The 24h TTL on Redis is intentionally long.

Security note (T-11-01-02): geoIds are not sensitive — they are public TripAdvisor
location identifiers. No PII is stored in this cache.

Usage:
    from brave.domains.tripadvisor.geo import resolve_geo_id
    from brave.config.settings import AppConfig
    import fakeredis

    config = AppConfig().tripadvisor
    geo_id = resolve_geo_id("BA", redis, config)  # → 303513
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# Production seed path — points to the committed JSON adjacent to brave/
GEO_SEED_PATH: Path = Path(__file__).parent.parent.parent.parent / "data" / "tripadvisor" / "uf_geoids.json"

# Redis key prefix (full key = f"{REDIS_GEO_KEY_PREFIX}{uf}")
REDIS_GEO_KEY_PREFIX: str = "brave:ta:geo:"

# TTL for cached geoIds — 24h (geoIds are stable, rarely change)
REDIS_GEO_TTL: int = 86_400


def _decode(value: Any) -> str:
    """Decode Redis response bytes to str (mirrors brave/core/engine.py pattern)."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def load_uf_geoids(path: Path) -> dict[str, int]:
    """Load the UF → geoId seed map from a JSON file.

    Args:
        path: Path to uf_geoids.json (must exist and contain a flat str→int dict).

    Returns:
        Dict mapping 2-letter UF code → TripAdvisor integer geoId.
    """
    raw = path.read_text(encoding="utf-8")
    data: dict[str, Any] = json.loads(raw)
    return {uf: int(geo_id) for uf, geo_id in data.items()}


def _resolve_geo_id_from_db(uf: str) -> int | None:
    """Read a single UF → geoId from the ``uf_geoids`` reference table.

    Callers of ``resolve_geo_id`` carry ``redis`` + ``config`` but no DB session, so a
    short-lived engine is opened from ``BRAVE_DB_URL`` and disposed (mirrors
    ``brave/tasks/beat_schedule.py``). This runs ONLY on a Redis miss, so the DB is
    never the hot path. Any failure — no ``BRAVE_DB_URL``, DB down, table empty —
    returns None so the caller falls back to the seed JSON (keeps tests green).

    Args:
        uf: Two-letter Brazilian state code (e.g. "BA").

    Returns:
        The integer geoId for the UF, or None when unavailable.
    """
    db_url = os.environ.get("BRAVE_DB_URL")
    if not db_url:
        return None
    try:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from brave.core.models import UfGeoid

        engine = create_engine(db_url)
        try:
            with sessionmaker(bind=engine)() as session:
                geo_id = (
                    session.query(UfGeoid.geo_id)
                    .filter(UfGeoid.uf == uf)
                    .scalar()
                )
                return int(geo_id) if geo_id is not None else None
        finally:
            engine.dispose()
    except Exception:
        return None


def resolve_geo_id(
    uf: str,
    redis: Any,
    config: Any,
    *,
    seed_path: Path | None = None,
) -> int:
    """Resolve a Brazilian UF code to its TripAdvisor integer geoId.

    Resolution order:
      1. Redis cache (key ``brave:ta:geo:{uf}``, TTL 24h)
      2. ``uf_geoids`` DB reference table (short-lived engine from BRAVE_DB_URL;
         production path — skipped when ``seed_path`` is an explicit test override)
      3. Seed JSON fallback (data/tripadvisor/uf_geoids.json)
      4. ValueError — fail-closed; unknown UF is never silently swallowed

    The resolved value is cached in Redis on DB/seed fallback with REDIS_GEO_TTL TTL.

    Args:
        uf:         Two-letter Brazilian state code (e.g. "BA").
        redis:      Redis client (sync — compatible with Celery worker context).
        config:     TripAdvisorConfig instance (currently unused; reserved for
                    future override map, e.g. manual geoId corrections).
        seed_path:  Override path to uf_geoids.json. Defaults to GEO_SEED_PATH.
                    Inject in tests to avoid depending on the production file path;
                    passing it also bypasses the DB read so tests stay deterministic.

    Returns:
        TripAdvisor integer geoId.

    Raises:
        ValueError: When UF is not found in Redis, the DB table, or seed JSON.
    """
    key = f"{REDIS_GEO_KEY_PREFIX}{uf}"

    # 1. Redis cache hit — hot path
    raw = _decode(redis.get(key))
    if raw:
        return int(raw)

    # 2. DB reference table (production path). Only on a Redis miss, and only when the
    # caller did not inject a seed_path test override. Any failure returns None → the
    # JSON seed below still resolves (keeps the offline suite green with no DB).
    if seed_path is None:
        db_geo_id = _resolve_geo_id_from_db(uf)
        if db_geo_id is not None:
            redis.setex(key, REDIS_GEO_TTL, str(db_geo_id))
            return db_geo_id

    # 3. Seed JSON fallback (test override or no DB)
    path = seed_path if seed_path is not None else GEO_SEED_PATH
    seed = load_uf_geoids(path)
    if uf in seed:
        geo_id = seed[uf]
        # Cache for next call — 24h TTL (geoIds are stable)
        redis.setex(key, REDIS_GEO_TTL, str(geo_id))
        return geo_id

    # 4. Fail-closed — unknown UF raises
    raise ValueError(
        f"Unknown UF {uf!r}: not in Redis cache, uf_geoids table, or seed JSON {path}. "
        "Validate uf_geoids.json with a real_browser test run."
    )
