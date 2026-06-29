"""Offline unit tests for persist_rotated_cookies (session write-back) and
client transport write-back wiring (260629-p2v).

All tests run 100% offline: fakeredis for Redis, respx for HTTP mocks.
No RUN_REAL_EXTERNALS required.

Test groups:
  TestPersistRotatedCookies — unit tests for the helper itself (no HTTP)
  TestClientWriteBack       — integration tests: client methods call the helper
"""

from __future__ import annotations

import json

import pytest


# ---------------------------------------------------------------------------
# Helper fixtures
# ---------------------------------------------------------------------------

def _make_session(
    cookies: dict | None = None,
    session_id: str = "oldsid",
) -> dict:
    """Return a valid flat-dict session suitable for Redis storage."""
    return {
        "cookies": cookies if cookies is not None else {"datadome": "old", "TAAUTHEAT": "auth"},
        "query_ids": {"destinations": "abc123def456789a", "attractions": "a5cb7fa004b5e4b5"},
        "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
        "acquired_at": "2026-06-24T12:00:00Z",
        "session_id": session_id,
    }


# ---------------------------------------------------------------------------
# TestPersistRotatedCookies — unit tests for the helper (fakeredis, no HTTP)
# ---------------------------------------------------------------------------


class TestPersistRotatedCookies:
    """Unit tests for brave.lanes.tripadvisor.session.persist_rotated_cookies."""

    def test_merges_response_cookies_new_wins(self):
        """Response cookies overwrite matching stored cookies; extras are kept."""
        import fakeredis

        from brave.config.settings import TripAdvisorConfig
        from brave.lanes.tripadvisor.client import BRAVE_TA_SESSION_KEY
        from brave.lanes.tripadvisor.session import persist_rotated_cookies

        redis = fakeredis.FakeRedis()
        ta_config = TripAdvisorConfig()
        session = _make_session(cookies={"datadome": "old", "TAAUTHEAT": "auth"})
        redis.setex(BRAVE_TA_SESSION_KEY, 1800, json.dumps(session))

        persist_rotated_cookies(redis, {"datadome": "new", "__vt": "v"}, ta_config)

        stored = json.loads(redis.get(BRAVE_TA_SESSION_KEY))
        assert stored["cookies"]["datadome"] == "new"    # response wins
        assert stored["cookies"]["TAAUTHEAT"] == "auth"  # unchanged long-lived cookie
        assert stored["cookies"]["__vt"] == "v"          # new cookie added

    def test_slides_ttl(self):
        """After a successful write-back, TTL is reset to session_ttl (sliding window)."""
        import fakeredis

        from brave.config.settings import TripAdvisorConfig
        from brave.lanes.tripadvisor.client import BRAVE_TA_SESSION_KEY
        from brave.lanes.tripadvisor.session import persist_rotated_cookies

        redis = fakeredis.FakeRedis()
        ta_config = TripAdvisorConfig()
        session = _make_session()
        # Seed with a short TTL to confirm it gets reset
        redis.setex(BRAVE_TA_SESSION_KEY, 100, json.dumps(session))

        persist_rotated_cookies(redis, {"datadome": "new"}, ta_config)

        ttl = redis.ttl(BRAVE_TA_SESSION_KEY)
        assert abs(ttl - ta_config.session_ttl) <= 2, (
            f"TTL {ttl} expected to be within ±2 of session_ttl={ta_config.session_ttl}"
        )

    def test_rederives_session_id_from_TASID(self):
        """When TASID appears in response cookies, session_id is updated to its value."""
        import fakeredis

        from brave.config.settings import TripAdvisorConfig
        from brave.lanes.tripadvisor.client import BRAVE_TA_SESSION_KEY
        from brave.lanes.tripadvisor.session import persist_rotated_cookies

        redis = fakeredis.FakeRedis()
        ta_config = TripAdvisorConfig()
        session = _make_session(session_id="oldsid")
        redis.setex(BRAVE_TA_SESSION_KEY, 1800, json.dumps(session))

        persist_rotated_cookies(redis, {"TASID": "newsid"}, ta_config)

        stored = json.loads(redis.get(BRAVE_TA_SESSION_KEY))
        assert stored["session_id"] == "newsid"

    def test_noop_on_empty_response_cookies(self):
        """Empty response_cookies dict → Redis session is NOT written (no-op)."""
        import fakeredis

        from brave.config.settings import TripAdvisorConfig
        from brave.lanes.tripadvisor.client import BRAVE_TA_SESSION_KEY
        from brave.lanes.tripadvisor.session import persist_rotated_cookies

        redis = fakeredis.FakeRedis()
        ta_config = TripAdvisorConfig()
        session = _make_session(cookies={"datadome": "original"})
        redis.setex(BRAVE_TA_SESSION_KEY, 1800, json.dumps(session))

        persist_rotated_cookies(redis, {}, ta_config)

        stored = json.loads(redis.get(BRAVE_TA_SESSION_KEY))
        assert stored["cookies"]["datadome"] == "original"  # unchanged

    def test_noop_on_missing_session(self):
        """No brave:ta:session key in Redis → returns without error (key-not-found)."""
        import fakeredis

        from brave.config.settings import TripAdvisorConfig
        from brave.lanes.tripadvisor.session import persist_rotated_cookies

        redis = fakeredis.FakeRedis()  # empty — no session key
        ta_config = TripAdvisorConfig()

        # Must not raise
        persist_rotated_cookies(redis, {"datadome": "new"}, ta_config)

    def test_best_effort_swallows_redis_error(self):
        """Redis.get raising Exception → persist_rotated_cookies returns without propagating."""
        import fakeredis

        from brave.config.settings import TripAdvisorConfig
        from brave.lanes.tripadvisor.session import persist_rotated_cookies

        redis = fakeredis.FakeRedis()
        ta_config = TripAdvisorConfig()

        # Inject a Redis failure
        def _raise(*args, **kwargs):
            raise Exception("Redis connection lost")

        redis.get = _raise

        # Must not raise — best-effort contract
        persist_rotated_cookies(redis, {"datadome": "new"}, ta_config)

    def test_normalises_list_form_cookies(self):
        """Phase-11 list-form cookies in Redis are normalised before merge."""
        import fakeredis

        from brave.config.settings import TripAdvisorConfig
        from brave.lanes.tripadvisor.client import BRAVE_TA_SESSION_KEY
        from brave.lanes.tripadvisor.session import persist_rotated_cookies

        redis = fakeredis.FakeRedis()
        ta_config = TripAdvisorConfig()
        session = {
            "cookies": [
                {"name": "datadome", "value": "old", "domain": ".tripadvisor.com"},
                {"name": "TAAUTHEAT", "value": "auth", "domain": ".tripadvisor.com"},
            ],
            "query_ids": {},
            "user_agent": "",
            "acquired_at": "",
            "session_id": "",
        }
        redis.setex(BRAVE_TA_SESSION_KEY, 1800, json.dumps(session))

        persist_rotated_cookies(redis, {"datadome": "new"}, ta_config)

        stored = json.loads(redis.get(BRAVE_TA_SESSION_KEY))
        # Must merge correctly from normalised form
        assert stored["cookies"]["datadome"] == "new"    # response wins
        assert stored["cookies"]["TAAUTHEAT"] == "auth"  # preserved from list form


# ---------------------------------------------------------------------------
# TestClientWriteBack — client integration tests (respx + fakeredis)
# ---------------------------------------------------------------------------


class TestClientWriteBack:
    """Client transport methods must call persist_rotated_cookies after each successful response."""

    @pytest.mark.asyncio
    async def test_fetch_destinations_writes_back_set_cookie(self, monkeypatch):
        """fetch_destinations: Set-Cookie from response lands in Redis session."""
        import fakeredis
        import httpx
        import respx

        from brave.config.settings import AppConfig
        from brave.lanes.tripadvisor.client import BRAVE_TA_SESSION_KEY, TripAdvisorClient

        redis = fakeredis.FakeRedis()
        config = AppConfig().tripadvisor
        session_data = _make_session(cookies={"datadome": "old"})
        # Seed Redis so persist_rotated_cookies can read it
        redis.setex(BRAVE_TA_SESSION_KEY, 1800, json.dumps(session_data))

        client = TripAdvisorClient(config=config, redis=redis)
        # Bypass real _get_session (reads from fakeredis, but skip geo cache issues)
        monkeypatch.setattr(client, "_get_session", lambda: session_data)
        # Bypass resolve_geo_id (Redis geo-cache lookup)
        monkeypatch.setattr("brave.lanes.tripadvisor.geo.resolve_geo_id", lambda uf, r, c: 303380)

        with respx.mock:
            respx.post("https://www.tripadvisor.com/data/graphql/ids").mock(
                return_value=httpx.Response(
                    200,
                    json=[{"data": {"locations": []}}],
                    headers={"Set-Cookie": "__vt=newvt; Path=/; Domain=.tripadvisor.com"},
                )
            )
            await client.fetch_destinations(uf="BA")

        stored = json.loads(redis.get(BRAVE_TA_SESSION_KEY))
        assert stored["cookies"]["__vt"] == "newvt", (
            "fetch_destinations must write Set-Cookie back into the Redis session"
        )

    @pytest.mark.asyncio
    async def test_fetch_attractions_writes_back_set_cookie(self, monkeypatch):
        """fetch_attractions: Set-Cookie from response lands in Redis session."""
        import fakeredis
        import httpx
        import respx

        from brave.config.settings import AppConfig
        from brave.lanes.tripadvisor.client import BRAVE_TA_SESSION_KEY, TripAdvisorClient

        redis = fakeredis.FakeRedis()
        config = AppConfig().tripadvisor
        session_data = _make_session(cookies={"datadome": "old"}, session_id="mysid")
        redis.setex(BRAVE_TA_SESSION_KEY, 1800, json.dumps(session_data))

        client = TripAdvisorClient(config=config, redis=redis)
        monkeypatch.setattr(client, "_get_session", lambda: session_data)

        with respx.mock:
            respx.post("https://www.tripadvisor.com/data/graphql/ids").mock(
                return_value=httpx.Response(
                    200,
                    json=[{"data": {"Result": [{"sections": []}]}}],
                    headers={"Set-Cookie": "__vt=newvt; Path=/; Domain=.tripadvisor.com"},
                )
            )
            await client.fetch_attractions(geo_id=303380)

        stored = json.loads(redis.get(BRAVE_TA_SESSION_KEY))
        assert stored["cookies"]["__vt"] == "newvt", (
            "fetch_attractions must write Set-Cookie back into the Redis session"
        )

    @pytest.mark.asyncio
    async def test_fetch_attractions_paginated_writes_back_per_page(self, monkeypatch):
        """fetch_attractions_paginated: Set-Cookie per HTML GET lands in Redis session."""
        import fakeredis
        import httpx
        import respx

        from brave.config.settings import AppConfig
        from brave.lanes.tripadvisor.client import BRAVE_TA_SESSION_KEY, TripAdvisorClient

        redis = fakeredis.FakeRedis()
        config = AppConfig().tripadvisor
        session_data = _make_session(cookies={"datadome": "old"})
        redis.setex(BRAVE_TA_SESSION_KEY, 1800, json.dumps(session_data))

        client = TripAdvisorClient(config=config, redis=redis)
        monkeypatch.setattr(client, "_get_session", lambda: session_data)

        # Page 1 of all-Brazil (geo_id=294280, offset=0)
        html_url = (
            "https://www.tripadvisor.com/Attractions-g294280-Activities-"
            "a_allAttractions.true-oa0-Brazil.html"
        )

        with respx.mock:
            respx.get(html_url).mock(
                return_value=httpx.Response(
                    200,
                    text="<html>no flex cards here</html>",
                    headers={"Set-Cookie": "datadome=fresh; Path=/; Domain=.tripadvisor.com"},
                )
            )
            async for _offset, _cards in client.fetch_attractions_paginated(
                geo_id=294280, start_page=1, max_pages=1
            ):
                pass

        stored = json.loads(redis.get(BRAVE_TA_SESSION_KEY))
        assert stored["cookies"]["datadome"] == "fresh", (
            "fetch_attractions_paginated must write Set-Cookie back into the Redis session"
        )

    @pytest.mark.asyncio
    async def test_fetch_destinations_writeback_error_does_not_abort_fetch(self, monkeypatch):
        """Write-back raising must NOT abort the data fetch — best-effort contract."""
        import fakeredis
        import httpx
        import respx

        import brave.lanes.tripadvisor.session as session_module
        from brave.config.settings import AppConfig
        from brave.lanes.tripadvisor.client import BRAVE_TA_SESSION_KEY, TripAdvisorClient

        redis = fakeredis.FakeRedis()
        config = AppConfig().tripadvisor
        session_data = _make_session(cookies={"datadome": "old"})
        redis.setex(BRAVE_TA_SESSION_KEY, 1800, json.dumps(session_data))

        client = TripAdvisorClient(config=config, redis=redis)
        monkeypatch.setattr(client, "_get_session", lambda: session_data)
        monkeypatch.setattr("brave.lanes.tripadvisor.geo.resolve_geo_id", lambda uf, r, c: 303380)

        # Make persist_rotated_cookies raise — simulates a Redis write failure at the helper level
        def _raise_on_persist(*args, **kwargs):
            raise Exception("inject write-back error")

        monkeypatch.setattr(session_module, "persist_rotated_cookies", _raise_on_persist)

        # Return a response with Set-Cookie to trigger the write-back code path
        with respx.mock:
            respx.post("https://www.tripadvisor.com/data/graphql/ids").mock(
                return_value=httpx.Response(
                    200,
                    json=[{"data": {"locations": [{"name": "Salvador", "locationId": 12345}]}}],
                    headers={"Set-Cookie": "__vt=newvt; Path=/"},
                )
            )
            # Must not raise — fetch must complete normally despite write-back error
            result = await client.fetch_destinations(uf="BA")

        assert result, "fetch_destinations must return data even when write-back raises"
