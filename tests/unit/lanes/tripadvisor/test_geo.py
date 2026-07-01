"""Offline unit tests for brave/lanes/tripadvisor/geo.py (TA-01).

Tests use fakeredis.FakeRedis() for Redis-backed resolution:
- Cache hit: resolves from Redis without reading JSON
- Cache miss (seed fallback): resolves from uf_geoids.json
- Unknown UF: raises ValueError (fail-closed)
- 27-UF seed map: exactly 27 keys
"""

import json
import tempfile
from pathlib import Path

import fakeredis
import pytest


@pytest.fixture
def fake_redis():
    """In-process Redis for unit tests — no Docker needed."""
    return fakeredis.FakeRedis()


@pytest.fixture
def seed_path(tmp_path: Path) -> Path:
    """Write a minimal uf_geoids.json for tests."""
    data = {"BA": 303513, "RJ": 303506, "SP": 303533}
    seed = tmp_path / "uf_geoids.json"
    seed.write_text(json.dumps(data))
    return seed


class TestLoadUfGeoids:
    def test_load_returns_dict(self, seed_path: Path):
        from brave.lanes.tripadvisor.geo import load_uf_geoids

        result = load_uf_geoids(seed_path)
        assert isinstance(result, dict)
        assert result["BA"] == 303513
        assert result["RJ"] == 303506

    def test_values_are_ints(self, seed_path: Path):
        from brave.lanes.tripadvisor.geo import load_uf_geoids

        result = load_uf_geoids(seed_path)
        for v in result.values():
            assert isinstance(v, int)


class TestResolveGeoId:
    def test_cache_hit_returns_cached_value(self, fake_redis, seed_path: Path):
        """Redis has the value; should return it without touching seed JSON."""
        from brave.lanes.tripadvisor.geo import REDIS_GEO_KEY_PREFIX, resolve_geo_id

        # Pre-populate cache with a known value
        fake_redis.set(f"{REDIS_GEO_KEY_PREFIX}BA", "99999")

        from brave.config.settings import AppConfig

        config = AppConfig().tripadvisor
        result = resolve_geo_id("BA", fake_redis, config, seed_path=seed_path)
        assert result == 99999

    def test_seed_fallback_on_redis_miss(self, fake_redis, seed_path: Path):
        """Redis miss → load from seed JSON → cache and return."""
        from brave.lanes.tripadvisor.geo import REDIS_GEO_KEY_PREFIX, resolve_geo_id

        from brave.config.settings import AppConfig

        config = AppConfig().tripadvisor
        result = resolve_geo_id("SP", fake_redis, config, seed_path=seed_path)
        assert result == 303533

        # Must have been cached in Redis
        cached = fake_redis.get(f"{REDIS_GEO_KEY_PREFIX}SP")
        assert cached is not None
        assert int(cached) == 303533

    def test_unknown_uf_raises_value_error(self, fake_redis, seed_path: Path):
        """Unknown UF that is neither in Redis nor seed → ValueError (fail-closed)."""
        from brave.lanes.tripadvisor.geo import resolve_geo_id

        from brave.config.settings import AppConfig

        config = AppConfig().tripadvisor
        with pytest.raises(ValueError, match="Unknown UF"):
            resolve_geo_id("XX", fake_redis, config, seed_path=seed_path)

    def test_production_seed_has_27_keys(self):
        """The committed data/tripadvisor/uf_geoids.json must have exactly 27 UF keys."""
        from brave.lanes.tripadvisor.geo import GEO_SEED_PATH, load_uf_geoids

        data = load_uf_geoids(GEO_SEED_PATH)
        assert len(data) == 27, f"Expected 27 UF keys, found {len(data)}: {sorted(data.keys())}"


# ---------------------------------------------------------------------------
# TestUfGeoidsSeed — structural validation of the committed uf_geoids.json
# ---------------------------------------------------------------------------


class TestUfGeoidsSeed:
    """Validate the committed data/tripadvisor/uf_geoids.json seed file.

    These tests run offline (no Redis, no network). They verify that the
    committed seed has the exact set of 27 Brazilian state UF codes, all
    positive integer geoIds, and no value from the previously-wrong
    sequential 303509-303534 range (those were arbitrary city geoIds, not
    state-level — confirmed by spike finding that 303509 = Teresopolis/RJ),
    EXCEPT the two entries the 260701-has live POC re-validated as genuine
    STATE geoIds that happen to fall in that band (RN=303510, RS=303530).
    """

    _EXPECTED_UF_CODES = frozenset({
        "AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA",
        "MG", "MS", "MT", "PA", "PB", "PE", "PI", "PR", "RJ", "RN",
        "RO", "RR", "RS", "SC", "SE", "SP", "TO",
    })

    def _load(self) -> dict:
        from brave.lanes.tripadvisor.geo import GEO_SEED_PATH, load_uf_geoids

        return load_uf_geoids(GEO_SEED_PATH)

    def test_uf_geoids_has_27_keys(self):
        """Exactly 27 UF codes, matching the canonical Brazilian state set."""
        data = self._load()
        assert set(data.keys()) == self._EXPECTED_UF_CODES, (
            f"Key mismatch. Expected {sorted(self._EXPECTED_UF_CODES)}, "
            f"got {sorted(data.keys())}"
        )

    def test_uf_geoids_all_positive_ints(self):
        """All geoId values must be positive integers (> 0)."""
        data = self._load()
        for uf, geo_id in data.items():
            assert isinstance(geo_id, int), f"UF {uf}: value {geo_id!r} is not an int"
            assert geo_id > 0, f"UF {uf}: geoId {geo_id} must be > 0"

    def test_uf_geoids_no_legacy_sequential_range(self):
        """No value in 303509-303534 EXCEPT the live-validated RN/RS state geoIds.

        Root cause (SPIKE 260629-rmz): that sequential range holds arbitrary city
        geoIds (e.g. 303509 = Teresopolis/RJ) that were used as placeholder UF-geoId
        mappings. This test guards against regression to those placeholders.

        Exception (260701-has live POC): RN=303510 and RS=303530 are the genuine
        STATE geoIds for those UFs — validated live via the GraphQL fetch_attraction_geo
        path — and legitimately fall inside the band. They are whitelisted; the guard
        still catches any OTHER value drifting into the placeholder range.
        """
        _VALIDATED_IN_BAND = {"RN": 303510, "RS": 303530}
        data = self._load()
        sequential_uf = {
            uf: v
            for uf, v in data.items()
            if 303509 <= v <= 303534 and _VALIDATED_IN_BAND.get(uf) != v
        }
        assert not sequential_uf, (
            f"Found geoIds in the wrong sequential range 303509-303534: {sequential_uf}. "
            "Replace with correct state-level geoIds discovered via "
            "scripts/ta_discover_state_geoids.py."
        )
