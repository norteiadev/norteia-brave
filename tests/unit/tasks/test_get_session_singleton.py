"""_get_session: one engine per process per db_url, built lazily (prefork-safe)."""

from brave.tasks import pipeline


def test_engine_is_lazy_singleton_keyed_by_db_url(monkeypatch):
    monkeypatch.setattr(pipeline, "_ENGINES", {})
    monkeypatch.setenv("BRAVE_DB_URL", "sqlite://")

    s1, e1 = pipeline._get_session()
    s2, e2 = pipeline._get_session()
    assert e1 is e2  # reused, not rebuilt per task
    assert s1 is not s2  # sessions stay per-call
    assert e1.pool._pre_ping is True

    monkeypatch.setenv("BRAVE_DB_URL", "sqlite:///:memory:")
    _, e3 = pipeline._get_session()
    assert e3 is not e1  # a changed URL never serves the old engine



def test_config_overlay_cache_expires():
    """An overlay re-written around a concurrent config write must not live forever."""
    import fakeredis

    from brave.config.runtime import OVERLAY_KEY, _write_cached_overlay

    rc = fakeredis.FakeRedis()
    _write_cached_overlay(rc, {})
    assert 0 < rc.ttl(OVERLAY_KEY) <= 60
