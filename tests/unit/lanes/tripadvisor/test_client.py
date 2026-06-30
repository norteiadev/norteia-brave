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
        await fake.fetch_attractions(geo_id=303513)
        await fake.fetch_attractions(geo_id=303513, max_pages=2)
        assert fake.attractions_calls == [
            {"geo_id": 303513, "max_pages": None},
            {"geo_id": 303513, "max_pages": 2},
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
        result = await fake.fetch_attractions(geo_id=303513)
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
        """fetch_attractions POSTs the AttractionsFusion qid + variables shape."""
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
            "session_id": "TASID_VALUE",
        }
        redis.set(BRAVE_TA_SESSION_KEY, json.dumps(session_data))

        client = TripAdvisorClient(config=config, redis=redis)

        captured_body = None

        with respx.mock:
            def capture_request(request):
                nonlocal captured_body
                captured_body = json.loads(request.content)
                return httpx.Response(
                    200,
                    json=[{"data": {"Result": [{"sections": []}]}}],
                )

            respx.post("https://www.tripadvisor.com/data/graphql/ids").mock(
                side_effect=capture_request
            )
            await client.fetch_attractions(geo_id=303513)

        assert captured_body is not None, "No request was captured"
        assert isinstance(captured_body, list), "Payload must be a list (batch array)"
        item = captured_body[0]
        assert "extensions" in item, f"Missing 'extensions' key in payload item: {item}"
        # Phase 13: hardcoded AttractionsFusion qid — NOT the session attractions qid
        assert item["extensions"]["preRegisteredQueryId"] == "a5cb7fa004b5e4b5"
        assert "query" not in item, f"Old 'query' key must NOT be in payload item: {item}"
        # Real variables shape (NOT the old {locationId, offset, limit})
        assert "locationId" not in item["variables"], "Old locationId variable must be gone"
        assert item["variables"]["request"]["routeParameters"]["contentType"] == "attraction"
        assert item["variables"]["sessionId"] == "TASID_VALUE"

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


# ---------------------------------------------------------------------------
# AttractionsFusion contract tests (Phase 13, plan 13-01)
# ---------------------------------------------------------------------------

# Fixture helpers
_IGUAZU_FLEX_CARD = {
    "__typename": "WebPresentation_SingleFlexCardSection",
    "singleFlexCardContent": {
        "cardTitle": {"text": "Iguazu Falls"},
        "cardLink": {"webRoute": {"typedParams": {"detailId": 312332}}},
        "bubbleRating": {"rating": 4.9, "reviewCount": 45811},
        "primaryInfo": {"text": "Waterfalls"},
    },
}
_AD_PLACEHOLDER = {"__typename": "WebPresentation_AdPlaceholder"}
_PAGINATION_LINKS = {"__typename": "WebPresentation_PaginationLinksList"}


def _make_ta_response(sections: list) -> list:
    """Build a full TripAdvisor response envelope with the given sections list."""
    return [{"data": {"Result": [{"sections": sections}]}}]


def _make_session_redis(redis, session_id: str = "TASID_VALUE") -> None:
    """Seed fakeredis with a valid Phase 13 session."""
    from brave.lanes.tripadvisor.client import BRAVE_TA_SESSION_KEY

    redis.set(
        BRAVE_TA_SESSION_KEY,
        json.dumps(
            {
                "cookies": {"datadome": "abc", "TASID": session_id},
                "query_ids": {"destinations": "stub_d", "attractions": "stub_a"},
                "user_agent": "Mozilla/5.0",
                "acquired_at": "2026-06-24T12:00:00Z",
                "session_id": session_id,
            }
        ),
    )


class TestTripAdvisorAttractionsFusionContract:
    """Verify the rewired fetch_attractions uses the AttractionsFusion qid + variables."""

    @pytest.mark.asyncio
    async def test_fetch_attractions_uses_attractions_fusion_qid(self):
        """fetch_attractions POSTs preRegisteredQueryId==a5cb7fa004b5e4b5."""
        import fakeredis

        from brave.config.settings import AppConfig
        from brave.lanes.tripadvisor.client import TripAdvisorClient

        config = AppConfig().tripadvisor
        redis = fakeredis.FakeRedis()
        _make_session_redis(redis)

        captured_body = None

        with respx.mock:
            def capture(request):
                nonlocal captured_body
                captured_body = json.loads(request.content)
                return httpx.Response(200, json=_make_ta_response([]))

            respx.post("https://www.tripadvisor.com/data/graphql/ids").mock(
                side_effect=capture
            )
            await TripAdvisorClient(config=config, redis=redis).fetch_attractions(
                geo_id=294280
            )

        assert captured_body is not None, "No request captured"
        assert captured_body[0]["extensions"]["preRegisteredQueryId"] == "a5cb7fa004b5e4b5"
        assert "locationId" not in captured_body[0]["variables"], (
            "Old locationId variable must be gone"
        )
        assert (
            captured_body[0]["variables"]["request"]["routeParameters"]["contentType"]
            == "attraction"
        )
        assert captured_body[0]["variables"]["sessionId"] == "TASID_VALUE"

    @pytest.mark.asyncio
    async def test_fetch_attractions_parses_single_flex_card_sections(self):
        """Iguazu Falls fixture parses to expected normalized card dict."""
        import fakeredis

        from brave.config.settings import AppConfig
        from brave.lanes.tripadvisor.client import TripAdvisorClient

        config = AppConfig().tripadvisor
        redis = fakeredis.FakeRedis()
        _make_session_redis(redis)

        sections = [_IGUAZU_FLEX_CARD, _AD_PLACEHOLDER, _PAGINATION_LINKS]

        with respx.mock:
            respx.post("https://www.tripadvisor.com/data/graphql/ids").mock(
                return_value=httpx.Response(200, json=_make_ta_response(sections))
            )
            result = await TripAdvisorClient(config=config, redis=redis).fetch_attractions(
                geo_id=294280
            )

        assert len(result) == 1, f"Expected exactly 1 card, got {len(result)}: {result}"
        card = result[0]
        assert card["name"] == "Iguazu Falls"
        assert card["locationId"] == 312332
        assert card["rating"] == 4.9
        assert card["review_count"] == 45811
        assert card["category"] == "Waterfalls"

    @pytest.mark.asyncio
    async def test_fetch_attractions_empty_sections_stops_pagination(self):
        """Empty sections list on first page → result == [] and exactly 1 HTTP call."""
        import fakeredis

        from brave.config.settings import AppConfig
        from brave.lanes.tripadvisor.client import TripAdvisorClient

        config = AppConfig().tripadvisor
        redis = fakeredis.FakeRedis()
        _make_session_redis(redis)

        call_count = 0

        with respx.mock:
            def handler(request):
                nonlocal call_count
                call_count += 1
                return httpx.Response(200, json=_make_ta_response([]))

            respx.post("https://www.tripadvisor.com/data/graphql/ids").mock(
                side_effect=handler
            )
            result = await TripAdvisorClient(config=config, redis=redis).fetch_attractions(
                geo_id=294280
            )

        assert result == [], f"Expected empty result, got: {result}"
        assert call_count == 1, f"Expected exactly 1 HTTP call, got {call_count}"

    @pytest.mark.asyncio
    async def test_fetch_attractions_partial_page_stops_pagination(self):
        """15 FlexCard sections (< 30) → exactly 1 HTTP call and 15 cards returned."""
        import fakeredis

        from brave.config.settings import AppConfig
        from brave.lanes.tripadvisor.client import TripAdvisorClient

        config = AppConfig().tripadvisor
        redis = fakeredis.FakeRedis()
        _make_session_redis(redis)

        # Build 15 SingleFlexCardSection items
        sections = [
            {
                "__typename": "WebPresentation_SingleFlexCardSection",
                "singleFlexCardContent": {
                    "cardTitle": {"text": f"Attraction {i}"},
                    "cardLink": {"webRoute": {"typedParams": {"detailId": 100000 + i}}},
                    "bubbleRating": {"rating": 4.0, "reviewCount": 100 + i},
                    "primaryInfo": {"text": "Beach"},
                },
            }
            for i in range(15)
        ]

        call_count = 0

        with respx.mock:
            def handler(request):
                nonlocal call_count
                call_count += 1
                return httpx.Response(200, json=_make_ta_response(sections))

            respx.post("https://www.tripadvisor.com/data/graphql/ids").mock(
                side_effect=handler
            )
            result = await TripAdvisorClient(config=config, redis=redis).fetch_attractions(
                geo_id=294280
            )

        assert call_count == 1, f"Expected exactly 1 HTTP call, got {call_count}"
        assert len(result) == 15, f"Expected 15 cards, got {len(result)}"


# ---------------------------------------------------------------------------
# Bootstrap qid reject-list tests (Phase 13, plan 13-01)
# ---------------------------------------------------------------------------


class TestBootstrapQueryIdRejectList:
    """Verify ta_bootstrap rejects known non-listing qids and extracts TASID."""

    def test_parse_curl_rejects_known_non_listing_qids(self, capsys):
        """A cURL with the ad qid 46dcf3e69ea8ba5a → query_ids == {} + warning to stderr."""
        import sys

        # Import parse_curl from scripts (stdlib-only)
        import importlib.util
        from pathlib import Path

        spec = importlib.util.spec_from_file_location(
            "ta_bootstrap",
            Path(__file__).parent.parent.parent.parent.parent
            / "scripts"
            / "ta_bootstrap.py",
        )
        ta_bootstrap = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ta_bootstrap)

        curl_str = (
            "curl 'https://www.tripadvisor.com/data/graphql/ids' "
            "-H 'Cookie: datadome=abc; TASID=E75FBE95' "
            "-H 'User-Agent: Mozilla/5.0' "
            "--data-raw '[{\"variables\":{},\"extensions\":{\"preRegisteredQueryId\":\"46dcf3e69ea8ba5a\"}}]'"
        )

        result = ta_bootstrap.parse_curl(curl_str)
        captured = capsys.readouterr()

        assert result["query_ids"] == {}, (
            f"Rejected qid must not be in query_ids; got: {result['query_ids']}"
        )
        assert "46dcf3e69ea8ba5a" in captured.err, (
            f"Warning about rejected qid expected in stderr; got: {captured.err!r}"
        )

    def test_parse_curl_extracts_tasid_as_session_id(self):
        """cURL with Cookie: TASID=E75FBE95 → parse_curl returns session_id == 'E75FBE95'."""
        import importlib.util
        from pathlib import Path

        spec = importlib.util.spec_from_file_location(
            "ta_bootstrap",
            Path(__file__).parent.parent.parent.parent.parent
            / "scripts"
            / "ta_bootstrap.py",
        )
        ta_bootstrap = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ta_bootstrap)

        curl_str = (
            "curl 'https://www.tripadvisor.com/data/graphql/ids' "
            "-H 'Cookie: datadome=abc; TASID=E75FBE95; TASession=xyz' "
            "-H 'User-Agent: Mozilla/5.0' "
            "--data-raw '[{\"variables\":{},\"extensions\":{\"preRegisteredQueryId\":\"a5cb7fa004b5e4b5\"}}]'"
        )

        result = ta_bootstrap.parse_curl(curl_str)
        assert result["session_id"] == "E75FBE95", (
            f"Expected session_id='E75FBE95', got: {result['session_id']!r}"
        )

    def test_parse_curl_session_id_empty_when_no_tasid(self):
        """cURL with no TASID cookie → parse_curl returns session_id == ''."""
        import importlib.util
        from pathlib import Path

        spec = importlib.util.spec_from_file_location(
            "ta_bootstrap",
            Path(__file__).parent.parent.parent.parent.parent
            / "scripts"
            / "ta_bootstrap.py",
        )
        ta_bootstrap = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ta_bootstrap)

        curl_str = (
            "curl 'https://www.tripadvisor.com/data/graphql/ids' "
            "-H 'Cookie: datadome=abc' "
            "-H 'User-Agent: Mozilla/5.0' "
            "--data-raw '[{\"variables\":{},\"extensions\":{\"preRegisteredQueryId\":\"a5cb7fa004b5e4b5\"}}]'"
        )

        result = ta_bootstrap.parse_curl(curl_str)
        assert result["session_id"] == "", (
            f"Expected empty session_id when TASID absent, got: {result['session_id']!r}"
        )


# ---------------------------------------------------------------------------
# TestFetchDestinationsQid — fixed QID resolution chain
# ---------------------------------------------------------------------------


class TestFetchDestinationsQid:
    """Verify the fixed fetch_destinations QID resolution chain.

    Bug (SPIKE 260629-rmz Finding 2): the previous code used
      query_id = session.get("query_ids", {}).get("destinations", "")
    which always resolved to "" because the cURL parser stores query_ids
    positionally (query_0..query_N), never writing a "destinations" key.

    Fix: three-step priority chain:
      1. config.query_id_override["destinations"]   (operator override wins)
      2. session["query_ids"].get("destinations")   (legacy session key)
      3. _DESTINATIONS_QID module constant           (pinned when discovered)
      4. ValueError when all three are falsy
    """

    @staticmethod
    def _make_redis_with_positional_session():
        """Seed fakeredis with a session that has positional query_ids (no "destinations" key)."""
        import json

        import fakeredis

        from brave.lanes.tripadvisor.client import BRAVE_TA_SESSION_KEY

        redis = fakeredis.FakeRedis()
        session_data = {
            "cookies": {"datadome": "abc", "TASession": "xyz"},
            "query_ids": {"query_0": "old_positional_qid"},  # no "destinations" key
            "user_agent": "Mozilla/5.0 test",
            "acquired_at": "2026-06-24T12:00:00Z",
        }
        redis.set(BRAVE_TA_SESSION_KEY, json.dumps(session_data))
        return redis

    @pytest.mark.asyncio
    async def test_uses_config_override_qid(self):
        """config.query_id_override["destinations"] takes priority over session lookup.

        Session has only positional keys (no "destinations"); config has an override.
        The POST must use the override QID, not the positional one.
        """
        from brave.config.settings import AppConfig
        from brave.lanes.tripadvisor.client import TripAdvisorClient

        config = AppConfig().tripadvisor.model_copy(
            update={"query_id_override": {"destinations": "override_qid_xyz"}}
        )
        redis = self._make_redis_with_positional_session()

        captured_body = None

        with respx.mock:
            def capture(request):
                nonlocal captured_body
                captured_body = json.loads(request.content)
                return httpx.Response(200, json=[{"data": {"locations": []}}])

            respx.post("https://www.tripadvisor.com/data/graphql/ids").mock(
                side_effect=capture
            )
            await TripAdvisorClient(config=config, redis=redis).fetch_destinations(uf="SP")

        assert captured_body is not None, "No request captured"
        assert captured_body[0]["extensions"]["preRegisteredQueryId"] == "override_qid_xyz", (
            f"Expected 'override_qid_xyz' (config override), "
            f"got {captured_body[0]['extensions']['preRegisteredQueryId']!r}"
        )

    @pytest.mark.asyncio
    async def test_raises_when_no_qid_configured(self, monkeypatch):
        """ValueError raised (not empty-QID request) when no QID is available.

        Session has no "destinations" key, config override is empty, and
        _DESTINATIONS_QID is None. Must raise ValueError immediately.
        """
        import brave.lanes.tripadvisor.client as client_mod

        from brave.config.settings import AppConfig
        from brave.lanes.tripadvisor.client import TripAdvisorClient

        # Ensure _DESTINATIONS_QID is None (it is by default, but patch defensively)
        monkeypatch.setattr(client_mod, "_DESTINATIONS_QID", None)

        config = AppConfig().tripadvisor.model_copy(
            update={"query_id_override": {}}  # no override
        )
        redis = self._make_redis_with_positional_session()

        with pytest.raises(ValueError, match="No destinations queryId configured"):
            await TripAdvisorClient(config=config, redis=redis).fetch_destinations(uf="SP")


# ---------------------------------------------------------------------------
# TestParserNullSafety — _parse_attractions_page handles null fields
# ---------------------------------------------------------------------------


def _make_null_card_section(
    card_title=None,
    bubble_rating=None,
    primary_info=None,
    detail_id: str = "12345",
) -> dict:
    """Build a SingleFlexCardSection with selectively null nested fields."""
    return {
        "__typename": "WebPresentation_SingleFlexCardSection",
        "singleFlexCardContent": {
            "cardTitle": card_title,
            "bubbleRating": bubble_rating,
            "primaryInfo": primary_info,
            "cardLink": {"webRoute": {"typedParams": {"detailId": detail_id}}},
        },
    }


class TestParserNullSafety:
    """Regression tests for null bubbleRating/cardTitle/primaryInfo.

    Bug (SPIKE 260629-rmz Finding 4): .get(k, {}).get(...) raises AttributeError
    when the field is present-but-null (review-less or title-less attractions).
    Fix: (card.get(k) or {}).get(...) short-circuits on None.
    """

    def test_parse_null_bubble_rating_no_attribute_error(self):
        """Card with bubbleRating=None must not raise; rating=0.0, review_count=0."""
        from brave.lanes.tripadvisor.client import TripAdvisorClient

        section = _make_null_card_section(
            card_title={"text": "Some Attraction"},
            bubble_rating=None,  # present-but-null
            primary_info={"text": "Nature"},
        )
        # Must not raise AttributeError
        cards = TripAdvisorClient._parse_attractions_page([section])
        assert len(cards) == 1, f"Expected 1 card, got {len(cards)}"
        assert cards[0]["rating"] == 0.0, f"Expected rating=0.0, got {cards[0]['rating']}"
        assert cards[0]["review_count"] == 0, (
            f"Expected review_count=0, got {cards[0]['review_count']}"
        )

    def test_parse_null_card_title_no_attribute_error(self):
        """Card with cardTitle=None must not raise; name=''."""
        from brave.lanes.tripadvisor.client import TripAdvisorClient

        section = _make_null_card_section(
            card_title=None,  # present-but-null
            bubble_rating={"rating": 4.5, "reviewCount": 100},
            primary_info={"text": "Waterfall"},
        )
        cards = TripAdvisorClient._parse_attractions_page([section])
        assert len(cards) == 1, f"Expected 1 card, got {len(cards)}"
        assert cards[0]["name"] == "", f"Expected name='', got {cards[0]['name']!r}"

    def test_parse_null_primary_info_no_attribute_error(self):
        """Card with primaryInfo=None must not raise; category=''."""
        from brave.lanes.tripadvisor.client import TripAdvisorClient

        section = _make_null_card_section(
            card_title={"text": "Iguazu Falls"},
            bubble_rating={"rating": 4.9, "reviewCount": 50000},
            primary_info=None,  # present-but-null
        )
        cards = TripAdvisorClient._parse_attractions_page([section])
        assert len(cards) == 1, f"Expected 1 card, got {len(cards)}"
        assert cards[0]["category"] == "", (
            f"Expected category='', got {cards[0]['category']!r}"
        )


# ---------------------------------------------------------------------------
# TestFetchAttractionDetail — new detail client method
# ---------------------------------------------------------------------------


def _seed_ta_session(redis, session_id: str = "TASID_VALUE") -> None:
    """Seed fakeredis with a minimal valid TA session."""
    from brave.lanes.tripadvisor.client import BRAVE_TA_SESSION_KEY

    redis.set(
        BRAVE_TA_SESSION_KEY,
        json.dumps({
            "cookies": {"datadome": "abc", "TASID": session_id},
            "query_ids": {"query_0": "some_qid"},
            "user_agent": "Mozilla/5.0",
            "acquired_at": "2026-06-24T12:00:00Z",
            "session_id": session_id,
        }),
    )


class TestFetchAttractionDetail:
    """Verify fetch_attraction_detail method (SPIKE 260629-rmz Finding 3)."""

    @pytest.mark.asyncio
    async def test_sends_correct_payload(self):
        """fetch_attraction_detail POSTs {variables:{locationId:N}, extensions:{preRegisteredQueryId:'444040f131735091'}}."""
        import fakeredis

        from brave.config.settings import AppConfig
        from brave.lanes.tripadvisor.client import TripAdvisorClient

        config = AppConfig().tripadvisor
        redis = fakeredis.FakeRedis()
        _seed_ta_session(redis)

        captured_body = None

        with respx.mock:
            def capture(request):
                nonlocal captured_body
                captured_body = json.loads(request.content)
                return httpx.Response(
                    200,
                    json=[{"data": {"locations": [{"parents": [], "locationId": 312332}]}}],
                )

            respx.post("https://www.tripadvisor.com/data/graphql/ids").mock(
                side_effect=capture
            )
            result = await TripAdvisorClient(config=config, redis=redis).fetch_attraction_detail(312332)

        assert captured_body is not None, "No request captured"
        assert isinstance(captured_body, list), "Payload must be a list"
        item = captured_body[0]
        assert item["variables"] == {"locationId": 312332}
        assert item["extensions"]["preRegisteredQueryId"] == "444040f131735091", (
            f"Expected '444040f131735091', got {item['extensions']['preRegisteredQueryId']!r}"
        )
        assert result is not None, "Expected a location dict, got None"
        assert result["locationId"] == 312332

    @pytest.mark.asyncio
    async def test_returns_none_on_empty_locations(self):
        """When locations=[], fetch_attraction_detail returns None."""
        import fakeredis

        from brave.config.settings import AppConfig
        from brave.lanes.tripadvisor.client import TripAdvisorClient

        config = AppConfig().tripadvisor
        redis = fakeredis.FakeRedis()
        _seed_ta_session(redis)

        with respx.mock:
            respx.post("https://www.tripadvisor.com/data/graphql/ids").mock(
                return_value=httpx.Response(200, json=[{"data": {"locations": []}}])
            )
            result = await TripAdvisorClient(config=config, redis=redis).fetch_attraction_detail(99999)

        assert result is None, f"Expected None on empty locations, got {result!r}"


# ---------------------------------------------------------------------------
# TestFetchAttractionGeo — fetch_attraction_geo (qid d3d4987463b78a39)
# ---------------------------------------------------------------------------


class TestFetchAttractionGeo:
    """Verify fetch_attraction_geo uses qid d3d4987463b78a39 and parses correctly.

    SPIKE-2 validated response shape (scrubbed fixture, no cookies/PII):
      data[0].data.gtmData.locationData = {
        "cityName": "Foz do Iguacu",
        "stateName": "State of Parana",
        "stateId": 303435,
        "countryName": "Brazil",
        "countryId": 294280,
        "locationHierarchy": ":312332:1:13:294280:303435:303444:",
      }
    city_geo_id = last non-empty element of locationHierarchy.split(':') = 303444.
    """

    # SPIKE-2 scrubbed fixture (no cookies, no PII — aggregate geo only)
    _FOZ_RESPONSE = [{"data": {"gtmData": {"locationData": {
        "cityName": "Foz do Iguacu",
        "stateName": "State of Parana",
        "stateId": 303435,
        "countryName": "Brazil",
        "countryId": 294280,
        "locationHierarchy": ":312332:1:13:294280:303435:303444:",
    }}}}]

    @pytest.mark.asyncio
    async def test_happy_path_foz_do_iguacu(self, monkeypatch):
        """Happy path: Cataratas fixture returns correct normalized geo dict."""
        import fakeredis

        from brave.config.settings import AppConfig
        from brave.lanes.tripadvisor.client import TripAdvisorClient

        config = AppConfig().tripadvisor
        redis = fakeredis.FakeRedis()
        client = TripAdvisorClient(config=config, redis=redis)

        stub_session = {
            "cookies": {"datadome": "abc"},
            "user_agent": "Mozilla/5.0 test",
            "acquired_at": "2026-06-24T12:00:00Z",
        }
        monkeypatch.setattr(client, "_get_session", lambda: stub_session)

        with respx.mock:
            respx.post("https://www.tripadvisor.com/data/graphql/ids").mock(
                return_value=httpx.Response(200, json=self._FOZ_RESPONSE)
            )
            result = await client.fetch_attraction_geo(312332)

        assert result == {
            "location_id": 312332,
            "city_name": "Foz do Iguacu",
            "state_name": "State of Parana",
            "city_geo_id": 303444,
            "state_geo_id": 303435,
        }, f"Unexpected result: {result!r}"

    @pytest.mark.asyncio
    async def test_uses_correct_qid_d3d4987463b78a39(self, monkeypatch):
        """POST payload must use preRegisteredQueryId 'd3d4987463b78a39'."""
        import fakeredis

        from brave.config.settings import AppConfig
        from brave.lanes.tripadvisor.client import TripAdvisorClient

        config = AppConfig().tripadvisor
        redis = fakeredis.FakeRedis()
        client = TripAdvisorClient(config=config, redis=redis)

        stub_session = {
            "cookies": {"datadome": "abc"},
            "user_agent": "Mozilla/5.0 test",
            "acquired_at": "2026-06-24T12:00:00Z",
        }
        monkeypatch.setattr(client, "_get_session", lambda: stub_session)

        captured_body = None

        with respx.mock:
            def capture(request):
                nonlocal captured_body
                captured_body = json.loads(request.content)
                return httpx.Response(200, json=self._FOZ_RESPONSE)

            respx.post("https://www.tripadvisor.com/data/graphql/ids").mock(
                side_effect=capture
            )
            await client.fetch_attraction_geo(312332)

        assert captured_body is not None, "No request captured"
        assert isinstance(captured_body, list), "Payload must be a list (batch array)"
        item = captured_body[0]
        assert item["extensions"]["preRegisteredQueryId"] == "d3d4987463b78a39", (
            f"Expected 'd3d4987463b78a39', got {item['extensions'].get('preRegisteredQueryId')!r}"
        )
        assert item["variables"]["locationId"] == 312332
        assert item["variables"]["eventType"] == "PAGEVIEW"
        assert item["variables"]["isGeoPage"] is True

    @pytest.mark.asyncio
    async def test_malformed_response_returns_none(self, monkeypatch):
        """Response missing gtmData key → returns None, no exception."""
        import fakeredis

        from brave.config.settings import AppConfig
        from brave.lanes.tripadvisor.client import TripAdvisorClient

        config = AppConfig().tripadvisor
        redis = fakeredis.FakeRedis()
        client = TripAdvisorClient(config=config, redis=redis)

        stub_session = {"cookies": {"datadome": "abc"}, "user_agent": "", "acquired_at": "2026-06-24"}
        monkeypatch.setattr(client, "_get_session", lambda: stub_session)

        with respx.mock:
            respx.post("https://www.tripadvisor.com/data/graphql/ids").mock(
                return_value=httpx.Response(200, json=[{"data": {}}])
            )
            result = await client.fetch_attraction_geo(999)

        assert result is None, f"Expected None on malformed response, got {result!r}"

    @pytest.mark.asyncio
    async def test_non_brazil_guard_returns_none(self, monkeypatch):
        """countryId != 294280 → returns None (non-Brazil guard)."""
        import fakeredis

        from brave.config.settings import AppConfig
        from brave.lanes.tripadvisor.client import TripAdvisorClient

        config = AppConfig().tripadvisor
        redis = fakeredis.FakeRedis()
        client = TripAdvisorClient(config=config, redis=redis)

        stub_session = {"cookies": {"datadome": "abc"}, "user_agent": "", "acquired_at": "2026-06-24"}
        monkeypatch.setattr(client, "_get_session", lambda: stub_session)

        non_brazil = [{"data": {"gtmData": {"locationData": {
            "cityName": "Buenos Aires",
            "stateName": "Buenos Aires Province",
            "stateId": 999,
            "countryId": 999999,
            "locationHierarchy": ":111:1:13:999999:999:888:",
        }}}}]

        with respx.mock:
            respx.post("https://www.tripadvisor.com/data/graphql/ids").mock(
                return_value=httpx.Response(200, json=non_brazil)
            )
            result = await client.fetch_attraction_geo(111)

        assert result is None, f"Expected None for non-Brazil countryId, got {result!r}"

    @pytest.mark.asyncio
    async def test_403_raises_session_expired(self, monkeypatch):
        """403 response → raises SessionExpiredError."""
        import fakeredis

        from brave.config.settings import AppConfig
        from brave.lanes.tripadvisor.client import SessionExpiredError, TripAdvisorClient

        config = AppConfig().tripadvisor
        redis = fakeredis.FakeRedis()
        client = TripAdvisorClient(config=config, redis=redis)

        stub_session = {"cookies": {"datadome": "abc"}, "user_agent": "", "acquired_at": "2026-06-24"}
        monkeypatch.setattr(client, "_get_session", lambda: stub_session)

        with respx.mock:
            respx.post("https://www.tripadvisor.com/data/graphql/ids").mock(
                return_value=httpx.Response(403, json={"error": "forbidden"})
            )
            with pytest.raises(SessionExpiredError):
                await client.fetch_attraction_geo(312332)

    @pytest.mark.asyncio
    async def test_429_raises_session_expired(self, monkeypatch):
        """429 response → raises SessionExpiredError."""
        import fakeredis

        from brave.config.settings import AppConfig
        from brave.lanes.tripadvisor.client import SessionExpiredError, TripAdvisorClient

        config = AppConfig().tripadvisor
        redis = fakeredis.FakeRedis()
        client = TripAdvisorClient(config=config, redis=redis)

        stub_session = {"cookies": {"datadome": "abc"}, "user_agent": "", "acquired_at": "2026-06-24"}
        monkeypatch.setattr(client, "_get_session", lambda: stub_session)

        with respx.mock:
            respx.post("https://www.tripadvisor.com/data/graphql/ids").mock(
                return_value=httpx.Response(429, json={"error": "rate_limited"})
            )
            with pytest.raises(SessionExpiredError):
                await client.fetch_attraction_geo(312332)


# ---------------------------------------------------------------------------
# TestFakeTripAdvisorClientGeo — fetch_attraction_geo in FakeTripAdvisorClient
# ---------------------------------------------------------------------------


class TestFakeTripAdvisorClientGeo:
    """Tests for FakeTripAdvisorClient.fetch_attraction_geo (fixture_geo + geo_calls)."""

    @pytest.mark.asyncio
    async def test_fake_fixture_geo_returns_configured(self):
        """FakeTripAdvisorClient with fixture_geo returns the configured dict."""
        from tests.fakes.fake_tripadvisor import FakeTripAdvisorClient

        geo_dict = {
            "location_id": 312332,
            "city_name": "Foz do Iguacu",
            "state_name": "State of Parana",
            "city_geo_id": 303444,
            "state_geo_id": 303435,
        }
        fake = FakeTripAdvisorClient(fixture_geo={312332: geo_dict})
        result = await fake.fetch_attraction_geo(312332)
        assert result == geo_dict

    @pytest.mark.asyncio
    async def test_fake_fixture_geo_returns_none_on_miss(self):
        """FakeTripAdvisorClient returns None for a locationId not in fixture_geo."""
        from tests.fakes.fake_tripadvisor import FakeTripAdvisorClient

        fake = FakeTripAdvisorClient()
        result = await fake.fetch_attraction_geo(999)
        assert result is None

    @pytest.mark.asyncio
    async def test_fake_fixture_geo_records_calls(self):
        """geo_calls list records each fetch_attraction_geo call's location_id."""
        from tests.fakes.fake_tripadvisor import FakeTripAdvisorClient

        geo_dict = {"location_id": 312332, "city_name": "Foz do Iguacu",
                    "state_name": "State of Parana", "city_geo_id": 303444, "state_geo_id": 303435}
        fake = FakeTripAdvisorClient(fixture_geo={312332: geo_dict})
        await fake.fetch_attraction_geo(312332)
        await fake.fetch_attraction_geo(999)
        assert fake.geo_calls == [312332, 999]
