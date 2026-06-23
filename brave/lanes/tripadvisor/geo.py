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
    from brave.lanes.tripadvisor.geo import resolve_geo_id
    from brave.config.settings import AppConfig
    import fakeredis

    config = AppConfig().tripadvisor
    geo_id = resolve_geo_id("BA", redis, config)  # → 303513
"""

from __future__ import annotations

import json
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
      2. Seed JSON fallback (data/tripadvisor/uf_geoids.json)
      3. ValueError — fail-closed; unknown UF is never silently swallowed

    The resolved value is cached in Redis on seed fallback with REDIS_GEO_TTL TTL.

    Args:
        uf:         Two-letter Brazilian state code (e.g. "BA").
        redis:      Redis client (sync — compatible with Celery worker context).
        config:     TripAdvisorConfig instance (currently unused; reserved for
                    future override map, e.g. manual geoId corrections).
        seed_path:  Override path to uf_geoids.json. Defaults to GEO_SEED_PATH.
                    Inject in tests to avoid depending on the production file path.

    Returns:
        TripAdvisor integer geoId.

    Raises:
        ValueError: When UF is not found in Redis or seed JSON.
    """
    key = f"{REDIS_GEO_KEY_PREFIX}{uf}"

    # 1. Redis cache hit
    raw = _decode(redis.get(key))
    if raw:
        return int(raw)

    # 2. Seed JSON fallback
    path = seed_path if seed_path is not None else GEO_SEED_PATH
    seed = load_uf_geoids(path)
    if uf in seed:
        geo_id = seed[uf]
        # Cache for next call — 24h TTL (geoIds are stable)
        redis.setex(key, REDIS_GEO_TTL, str(geo_id))
        return geo_id

    # 3. Fail-closed — unknown UF raises
    raise ValueError(
        f"Unknown UF {uf!r}: not in Redis cache or seed JSON {path}. "
        "Validate uf_geoids.json with a real_browser test run."
    )
