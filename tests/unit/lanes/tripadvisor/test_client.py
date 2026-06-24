"""Offline unit tests for TripAdvisorClient and FakeTripAdvisorClient.

All tests run without Playwright or network I/O:
- TestFakeTripAdvisorClient: FakeTripAdvisorClient records calls and returns fixture data
- TestTripAdvisorClientOffline: offline tests that don't require Playwright
- TestTripAdvisorClientSessionInjection: SessionMissingError + _get_session Redis-only
  behaviour introduced by Phase 12 session-injection model (TA-12)
- TestTripAdvisorClientPayloadShape: fetch_* send the correct extensions.preRegisteredQueryId
  batch-array payload shape (TA-12 core fix)

Notes (Phase 12 changes):
- _bootstrap_session is removed — operator injects session via POST /api/v1/tripadvisor/session
- _get_session() reads Redis only; raises SessionMissingError on miss
- Correct payload shape: [{"variables": {...}, "extensions": {"preRegisteredQueryId": qid}}]
- TestTripAdvisorClientRealBrowser (and real_browser marker) removed — no bootstrap path exists
"""

import json

import httpx
import pytest
import respx


# ---------------------------------------------------------------------------
# FakeTripAdvisorClient tests
# ---------------------------------------------------------------------------


class TestFakeTripAdvisorClient:
    @pytest.mark.asyncio
    async def test_fake_records_destinations_calls(self):
        from tests.fakes.fake_tripadvisor import FakeTripAdvisorClient

        fake = FakeTripAdvisorClient()
        await fake.fetch_destinations(uf="BA")
        await fake.fetch_destinations(uf="RJ")
        assert fake.destinations_calls == [{"uf": "BA"}, {"uf": "RJ"}]

    @pytest.mark.asyncio
    async def test_fake_records_attractions_calls(self):
        from tests.fakes.fake_tripadvisor import FakeTripAdvisorClient

        fake = FakeTripAdvisorClient()
        await fake.fetch_attractions(geo_id=303513, offset=0)
        await fake.fetch_attractions(geo_id=303513, offset=20)
        assert fake.attractions_calls == [
            {"geo_id": 303513, "offset": 0},
            {"geo_id": 303513, "offset": 20},
        ]

    @pytest.mark.asyncio
    async def test_fake_returns_destination_fixture(self):
        from tests.fakes.fake_tripadvisor import FakeTripAdvisorClient

        fixture = [{"locationId": 12345, "name": "Salvador"}]
        fake = FakeTripAdvisorClient(fixture_destinations={"BA": fixture})
        result = await fake.fetch_destinations(uf="BA")
        assert result == fixture

    @pytest.mark.asyncio
    async def test_fake_returns_attraction_fixture(self):
        from tests.fakes.fake_tripadvisor import FakeTripAdvisorClient

        fixture = [{"locationId": 99999, "name": "Elevador Lacerda"}]
        fake = FakeTripAdvisorClient(fixture_attractions={303513: fixture})
        result = await fake.fetch_attractions(geo_id=303513, offset=0)
        assert result == fixture

    @pytest.mark.asyncio
    async def test_fake_returns_empty_on_missing_uf(self):
        from tests.fakes.fake_tripadvisor import FakeTripAdvisorClient

        fake = FakeTripAdvisorClient()
        result = await fake.fetch_destinations(uf="ZZ")
        assert result == []

    @pytest.mark.asyncio
    async def test_fake_resolve_geo_id_default_zero(self):
        from tests.fakes.fake_tripadvisor import FakeTripAdvisorClient

        fake = FakeTripAdvisorClient()
        result = await fake.resolve_geo_id(uf="BA")
        assert result == 0

    @pytest.mark.asyncio
    async def test_fake_resolve_geo_id_from_config(self):
        from tests.fakes.fake_tripadvisor import FakeTripAdvisorClient

        fake = FakeTripAdvisorClient(geo_ids={"BA": 303513})
        result = await fake.resolve_geo_id(uf="BA")
        assert result == 303513

    def test_fake_protocol_compliance(self):
        from tests.fakes.fake_tripadvisor import _check_protocol_compliance

        _check_protocol_compliance()


# ---------------------------------------------------------------------------
# TripAdvisorClient tests (offline only)
# ---------------------------------------------------------------------------


class TestTripAdvisorClientOffline:
    """Tests that do NOT require Playwright or live TripAdvisor access."""

    def test_playwright_not_at_module_top_level(self):
        """Playwright must not appear anywhere in client.py (top-level or function-level)."""
        import ast
        from pathlib import Path

        client_path = (
            Path(__file__).parent.parent.parent.parent.parent
            / "brave"
            / "lanes"
            / "tripadvisor"
            / "client.py"
        )
        tree = ast.parse(client_path.read_text())
        # Check ALL import nodes (top-level AND function-level) for playwright references
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in getattr(node, "names", []):
                    assert "playwright" not in alias.name.lower(), (
                        f"playwright found in import: {alias.name}"
                    )
                module = getattr(node, "module", "") or ""
                assert "playwright" not in module.lower(), (
                    f"playwright found in from import: {module}"
                )

    def test_session_key_constant(self):
        from brave.lanes.tripadvisor.client import BRAVE_TA_SESSION_KEY

        assert BRAVE_TA_SESSION_KEY == "brave:ta:session"

    def test_session_expired_error_is_exception(self):
        from brave.lanes.tripadvisor.client import SessionExpiredError

        err = SessionExpiredError("test")
        assert isinstance(err, Exception)

    @pytest.mark.asyncio
    async def test_session_expired_on_403(self, monkeypatch):
        """httpx returning 403 from a GraphQL call must raise SessionExpiredError."""
        import fakeredis

        from brave.config.settings import AppConfig
        from brave.lanes.tripadvisor.client import SessionExpiredError, TripAdvisorClient

        config = AppConfig().tripadvisor
        redis = fakeredis.FakeRedis()

        client = TripAdvisorClient(config=config, redis=redis)

        # Stub _get_session to return a flat-dict session (Phase 12 shape)
        stub_session = {
            "cookies": {"__ddg1_": "stub"},
            "query_ids": {"destinations": "stub_qid_dest", "attractions": "stub_qid_attr"},
            "user_agent": "Mozilla/5.0 test",
            "acquired_at": "2026-06-24T12:00:00Z",
        }
        monkeypatch.setattr(client, "_get_session", lambda: stub_session)

        with respx.mock:
            respx.post("https://www.tripadvisor.com/data/graphql/ids").mock(
                return_value=httpx.Response(403, json={"error": "forbidden"})
            )
            with pytest.raises(SessionExpiredError):
                await client.fetch_destinations(uf="BA")

    @pytest.mark.asyncio
    async def test_session_expired_on_429(self, monkeypatch):
        """httpx returning 429 from a GraphQL call must raise SessionExpiredError."""
        import fakeredis

        from brave.config.settings import AppConfig
        from brave.lanes.tripadvisor.client import SessionExpiredError, TripAdvisorClient

        config = AppConfig().tripadvisor
        redis = fakeredis.FakeRedis()

        client = TripAdvisorClient(config=config, redis=redis)

        stub_session = {
            "cookies": {"__ddg1_": "stub"},
            "query_ids": {"destinations": "stub_qid_dest", "attractions": "stub_qid_attr"},
            "user_agent": "Mozilla/5.0 test",
            "acquired_at": "2026-06-24T12:00:00Z",
        }
        monkeypatch.setattr(client, "_get_session", lambda: stub_session)

        with respx.mock:
            respx.post("https://www.tripadvisor.com/data/graphql/ids").mock(
                return_value=httpx.Response(429, json={"error": "rate_limited"})
            )
            with pytest.raises(SessionExpiredError):
                await client.fetch_destinations(uf="BA")

    def test_client_protocol_compliance(self):
        """TripAdvisorClient must satisfy TripAdvisorClientProtocol structurally."""
        from brave.lanes.tripadvisor.client import _check_protocol_compliance

        _check_protocol_compliance()


# ---------------------------------------------------------------------------
# Session-injection model tests (TA-12)
# ---------------------------------------------------------------------------


class TestTripAdvisorClientSessionInjection:
    """Tests for the Phase 12 session-injection model.

    _get_session() reads Redis only. No Playwright, no _bootstrap_session.
    """

    def test_get_session_raises_on_redis_miss(self):
        """_get_session() with empty FakeRedis must raise SessionMissingError."""
        import fakeredis

        from brave.config.settings import AppConfig
        from brave.lanes.tripadvisor.client import SessionMissingError, TripAdvisorClient

        config = AppConfig().tripadvisor
        redis = fakeredis.FakeRedis()
        client = TripAdvisorClient(config=config, redis=redis)

        with pytest.raises(SessionMissingError):
            client._get_session()

    def test_get_session_returns_injected_session(self):
        """FakeRedis with a valid JSON session → _get_session() returns that dict."""
        import fakeredis

        from brave.config.settings import AppConfig
        from brave.lanes.tripadvisor.client import BRAVE_TA_SESSION_KEY, TripAdvisorClient

        config = AppConfig().tripadvisor
        redis = fakeredis.FakeRedis()

        session_data = {
            "cookies": {"datadome": "abc123", "TASession": "xyz456"},
            "query_ids": {"destinations": "a1b2c3d4e5f6a7b8", "attractions": "b2c3d4e5f6a7b8c9"},
            "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) ...",
            "acquired_at": "2026-06-24T12:00:00Z",
        }
        redis.set(BRAVE_TA_SESSION_KEY, json.dumps(session_data))

        client = TripAdvisorClient(config=config, redis=redis)
        result = client._get_session()

        assert result["query_ids"]["destinations"] == "a1b2c3d4e5f6a7b8"
        assert result["cookies"]["datadome"] == "abc123"
        assert result["acquired_at"] == "2026-06-24T12:00:00Z"

    def test_get_session_handles_list_cookies(self):
        """Phase 11 legacy cookies-as-list shape is converted to flat dict."""
        import fakeredis

        from brave.config.settings import AppConfig
        from brave.lanes.tripadvisor.client import BRAVE_TA_SESSION_KEY, TripAdvisorClient

        config = AppConfig().tripadvisor
        redis = fakeredis.FakeRedis()

        # Phase 11 shape: cookies as list of {name, value, domain}
        session_data = {
            "cookies": [
                {"name": "datadome", "value": "abc123", "domain": ".tripadvisor.com"},
                {"name": "TASession", "value": "xyz456", "domain": ".tripadvisor.com"},
            ],
            "query_ids": {"destinations": "a1b2c3d4e5f6a7b8"},
            "acquired_at": "2026-06-24T12:00:00Z",
        }
        redis.set(BRAVE_TA_SESSION_KEY, json.dumps(session_data))

        client = TripAdvisorClient(config=config, redis=redis)
        result = client._get_session()

        # Must be normalised to flat dict
        assert isinstance(result["cookies"], dict)
        assert result["cookies"]["datadome"] == "abc123"
        assert result["cookies"]["TASession"] == "xyz456"

    def test_no_bootstrap_session_method(self):
        """TripAdvisorClient instance must NOT have a _bootstrap_session attribute."""
        import fakeredis

        from brave.config.settings import AppConfig
        from brave.lanes.tripadvisor.client import TripAdvisorClient

        config = AppConfig().tripadvisor
        redis = fakeredis.FakeRedis()
        client = TripAdvisorClient(config=config, redis=redis)

        assert not hasattr(client, "_bootstrap_session"), (
            "_bootstrap_session must be removed in Phase 12 refactor"
        )

    def test_no_playwright_at_module_level(self):
        """AST parse of client.py: no playwright import AND no _bootstrap_session function."""
        import ast
        from pathlib import Path

        client_path = (
            Path(__file__).parent.parent.parent.parent.parent
            / "brave"
            / "lanes"
            / "tripadvisor"
            / "client.py"
        )
        source = client_path.read_text()
        tree = ast.parse(source)

        # Check ALL import nodes for playwright
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in getattr(node, "names", []):
                    assert "playwright" not in alias.name.lower(), (
                        f"playwright found in import: {alias.name}"
                    )
                module = getattr(node, "module", "") or ""
                assert "playwright" not in module.lower(), (
                    f"playwright found in from-import: {module}"
                )

        # Check no _bootstrap_session function definition
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                assert node.name != "_bootstrap_session", (
                    "_bootstrap_session function must be removed in Phase 12 refactor"
                )

    def test_session_missing_error_is_exception(self):
        """SessionMissingError must be an Exception subclass."""
        from brave.lanes.tripadvisor.client import SessionMissingError

        err = SessionMissingError("test")
        assert isinstance(err, Exception)


# ---------------------------------------------------------------------------
# Payload shape tests (TA-12 core fix)
# ---------------------------------------------------------------------------


class TestTripAdvisorClientPayloadShape:
    """Assert that fetch_* use the correct extensions.preRegisteredQueryId shape."""

    @pytest.mark.asyncio
    async def test_fetch_destinations_payload_shape(self):
        """fetch_destinations POSTs payload[0]["extensions"]["preRegisteredQueryId"]."""
        import fakeredis

        from brave.config.settings import AppConfig
        from brave.lanes.tripadvisor.client import BRAVE_TA_SESSION_KEY, TripAdvisorClient

        config = AppConfig().tripadvisor
        redis = fakeredis.FakeRedis()

        session_data = {
            "cookies": {"datadome": "abc", "TASession": "xyz"},
            "query_ids": {"destinations": "stub_qid_dest", "attractions": "stub_qid_attr"},
            "user_agent": "Mozilla/5.0 test",
            "acquired_at": "2026-06-24T12:00:00Z",
        }
        redis.set(BRAVE_TA_SESSION_KEY, json.dumps(session_data))

        client = TripAdvisorClient(config=config, redis=redis)

        captured_body = None

        with respx.mock:
            def capture_request(request):
                nonlocal captured_body
                captured_body = json.loads(request.content)
                return httpx.Response(200, json=[{"data": {"locations": []}}])

            respx.post("https://www.tripadvisor.com/data/graphql/ids").mock(
                side_effect=capture_request
            )
            await client.fetch_destinations(uf="BA")

        assert captured_body is not None, "No request was captured"
        assert isinstance(captured_body, list), "Payload must be a list (batch array)"
        item = captured_body[0]
        assert "extensions" in item, f"Missing 'extensions' key in payload item: {item}"
        assert item["extensions"]["preRegisteredQueryId"] == "stub_qid_dest"
        assert "query" not in item, f"Old 'query' key must NOT be in payload item: {item}"

    @pytest.mark.asyncio
    async def test_fetch_attractions_payload_shape(self):
        """fetch_attractions POSTs payload[0]["extensions"]["preRegisteredQueryId"]."""
        import fakeredis

        from brave.config.settings import AppConfig
        from brave.lanes.tripadvisor.client import BRAVE_TA_SESSION_KEY, TripAdvisorClient

        config = AppConfig().tripadvisor
        redis = fakeredis.FakeRedis()

        session_data = {
            "cookies": {"datadome": "abc", "TASession": "xyz"},
            "query_ids": {"destinations": "stub_qid_dest", "attractions": "stub_qid_attr"},
            "user_agent": "Mozilla/5.0 test",
            "acquired_at": "2026-06-24T12:00:00Z",
        }
        redis.set(BRAVE_TA_SESSION_KEY, json.dumps(session_data))

        client = TripAdvisorClient(config=config, redis=redis)

        captured_body = None

        with respx.mock:
            def capture_request(request):
                nonlocal captured_body
                captured_body = json.loads(request.content)
                return httpx.Response(200, json=[{"data": {"attractions": []}}])

            respx.post("https://www.tripadvisor.com/data/graphql/ids").mock(
                side_effect=capture_request
            )
            await client.fetch_attractions(geo_id=303513, offset=0)

        assert captured_body is not None, "No request was captured"
        assert isinstance(captured_body, list), "Payload must be a list (batch array)"
        item = captured_body[0]
        assert "extensions" in item, f"Missing 'extensions' key in payload item: {item}"
        assert item["extensions"]["preRegisteredQueryId"] == "stub_qid_attr"
        assert "query" not in item, f"Old 'query' key must NOT be in payload item: {item}"

    @pytest.mark.asyncio
    async def test_fetch_destinations_uses_flat_cookie_dict(self):
        """fetch_destinations passes cookies as a flat dict (not list-of-dicts)."""
        import fakeredis

        from brave.config.settings import AppConfig
        from brave.lanes.tripadvisor.client import BRAVE_TA_SESSION_KEY, TripAdvisorClient

        config = AppConfig().tripadvisor
        redis = fakeredis.FakeRedis()

        session_data = {
            "cookies": {"datadome": "abc", "TASession": "xyz"},
            "query_ids": {"destinations": "stub_qid_dest", "attractions": "stub_qid_attr"},
            "user_agent": "Mozilla/5.0 test",
            "acquired_at": "2026-06-24T12:00:00Z",
        }
        redis.set(BRAVE_TA_SESSION_KEY, json.dumps(session_data))

        client = TripAdvisorClient(config=config, redis=redis)

        captured_headers = None

        with respx.mock:
            def capture_request(request):
                nonlocal captured_headers
                captured_headers = dict(request.headers)
                return httpx.Response(200, json=[{"data": {"locations": []}}])

            respx.post("https://www.tripadvisor.com/data/graphql/ids").mock(
                side_effect=capture_request
            )
            await client.fetch_destinations(uf="BA")

        assert captured_headers is not None
        # httpx sets the Cookie header from the cookies dict
        cookie_header = captured_headers.get("cookie", "")
        assert "datadome=abc" in cookie_header, (
            f"Expected 'datadome=abc' in Cookie header, got: {cookie_header}"
        )

    def test_no_scraper_dep_in_pyproject(self):
        """pyproject.toml must not contain a 'scraper' optional dep group or playwright."""
        from pathlib import Path

        pyproject_path = (
            Path(__file__).parent.parent.parent.parent.parent / "pyproject.toml"
        )
        content = pyproject_path.read_text()

        assert "scraper" not in content, (
            "scraper optional dep group must be removed from pyproject.toml"
        )
        assert "playwright" not in content, (
            "playwright must be removed from pyproject.toml"
        )


# ---------------------------------------------------------------------------
# Phase 12 review fixes: proxy threading (CR-02) + canary single-page (WR-06)
# ---------------------------------------------------------------------------


class TestTripAdvisorClientProxyAndPaging:
    """CR-02: BRAVE_TA_PROXY_URL is threaded into httpx; WR-06: max_pages bounds paging."""

    @staticmethod
    def _seed(redis):
        from brave.lanes.tripadvisor.client import BRAVE_TA_SESSION_KEY

        redis.set(
            BRAVE_TA_SESSION_KEY,
            json.dumps(
                {
                    "cookies": {"datadome": "abc"},
                    "query_ids": {"destinations": "qid_d", "attractions": "qid_a"},
                    "user_agent": "UA",
                    "acquired_at": "2026-06-24T12:00:00Z",
                }
            ),
        )

    @pytest.mark.asyncio
    async def test_fetch_destinations_threads_configured_proxy(self, monkeypatch):
        """CR-02: the configured proxy_url is passed to httpx.AsyncClient(proxy=...)."""
        import fakeredis

        from brave.config.settings import AppConfig
        from brave.lanes.tripadvisor import client as client_mod
        from brave.lanes.tripadvisor.client import TripAdvisorClient

        config = AppConfig().tripadvisor.model_copy(
            update={"proxy_url": "socks5://user:pass@proxy:1080"}
        )
        redis = fakeredis.FakeRedis()
        self._seed(redis)

        captured: dict = {}

        class _FakeClient:
            def __init__(self, *args, **kwargs):
                captured["proxy"] = kwargs.get("proxy")

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def post(self, *args, **kwargs):
                return httpx.Response(
                    200,
                    json=[{"data": {"locations": []}}],
                    request=httpx.Request("POST", "https://www.tripadvisor.com/data/graphql/ids"),
                )

        monkeypatch.setattr(client_mod.httpx, "AsyncClient", _FakeClient)

        ta_client = TripAdvisorClient(config=config, redis=redis)
        await ta_client.fetch_destinations(uf="BA")

        assert captured["proxy"] == "socks5://user:pass@proxy:1080"

    @pytest.mark.asyncio
    async def test_fetch_destinations_no_proxy_passes_none(self, monkeypatch):
        """CR-02: empty proxy_url resolves to proxy=None (no accidental empty-string proxy)."""
        import fakeredis

        from brave.config.settings import AppConfig
        from brave.lanes.tripadvisor import client as client_mod
        from brave.lanes.tripadvisor.client import TripAdvisorClient

        config = AppConfig().tripadvisor.model_copy(update={"proxy_url": ""})
        redis = fakeredis.FakeRedis()
        self._seed(redis)

        captured: dict = {}

        class _FakeClient:
            def __init__(self, *args, **kwargs):
                captured["proxy"] = kwargs.get("proxy", "MISSING")

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def post(self, *args, **kwargs):
                return httpx.Response(
                    200,
                    json=[{"data": {"locations": []}}],
                    request=httpx.Request("POST", "https://www.tripadvisor.com/data/graphql/ids"),
                )

        monkeypatch.setattr(client_mod.httpx, "AsyncClient", _FakeClient)

        ta_client = TripAdvisorClient(config=config, redis=redis)
        await ta_client.fetch_destinations(uf="BA")

        assert captured["proxy"] is None

    @pytest.mark.asyncio
    async def test_fetch_destinations_max_pages_one_stops_after_first(self):
        """WR-06: max_pages=1 issues exactly one request even on a full first page."""
        import fakeredis

        from brave.config.settings import AppConfig
        from brave.lanes.tripadvisor.client import TripAdvisorClient

        config = AppConfig().tripadvisor
        redis = fakeredis.FakeRedis()
        self._seed(redis)

        call_count = 0
        full_page = [{"locationId": i} for i in range(20)]  # 20 items → would normally page again

        with respx.mock:
            def handler(request):
                nonlocal call_count
                call_count += 1
                return httpx.Response(200, json=[{"data": {"locations": full_page}}])

            respx.post("https://www.tripadvisor.com/data/graphql/ids").mock(
                side_effect=handler
            )
            results = await TripAdvisorClient(config=config, redis=redis).fetch_destinations(
                uf="BA", max_pages=1
            )

        assert call_count == 1, f"max_pages=1 should issue exactly one request, got {call_count}"
        assert len(results) == 20
