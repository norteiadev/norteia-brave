"""Offline unit tests for TripAdvisorClient and FakeTripAdvisorClient (TA-01).

All tests run without Playwright or network I/O:
- test_fake_records_calls: FakeTripAdvisorClient records destinations + attractions calls
- test_fake_returns_fixture: FakeTripAdvisorClient returns configured fixture data
- test_session_expired_on_403: httpx 403 from GraphQL endpoint raises SessionExpiredError
- test_protocol_compliance_fake: FakeTripAdvisorClient satisfies TripAdvisorClientProtocol
- test_no_playwright_at_module_level: playwright not imported at module top-level
- test_session_key_constant: BRAVE_TA_SESSION_KEY = "brave:ta:session"

Tests touching _bootstrap_session are gated with @pytest.mark.real_browser
and are never run in CI.
"""

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
        """Playwright must not appear in the module-level imports of client.py."""
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
        # Collect top-level import nodes (not inside function defs)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                # Check if this import is inside a function definition
                # ast.walk doesn't give parents, so we check by col_offset heuristic:
                # top-level imports have col_offset == 0
                if node.col_offset == 0:
                    for alias in getattr(node, "names", []):
                        assert "playwright" not in alias.name.lower(), (
                            f"playwright found in top-level import: {alias.name}"
                        )
                    module = getattr(node, "module", "") or ""
                    assert "playwright" not in module.lower(), (
                        f"playwright found in top-level from import: {module}"
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

        # Stub _get_session so no Playwright is needed
        stub_session = {
            "cookies": [{"name": "__ddg1_", "value": "stub", "domain": ".tripadvisor.com"}],
            "query_ids": {"destinations": "stub_qid_dest", "attractions": "stub_qid_attr"},
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
            "cookies": [{"name": "__ddg1_", "value": "stub", "domain": ".tripadvisor.com"}],
            "query_ids": {"destinations": "stub_qid_dest", "attractions": "stub_qid_attr"},
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
# Marker gate: real_browser tests (skipped in CI — opt-in only)
# ---------------------------------------------------------------------------

@pytest.mark.real_browser
class TestTripAdvisorClientRealBrowser:
    """Tests that require a live browser and real TripAdvisor access.

    These are NEVER run in CI. Gate with: pytest -m real_browser
    """

    @pytest.mark.asyncio
    async def test_bootstrap_session_returns_cookies(self):
        """Live Playwright bootstrap must produce a non-empty cookie jar."""
        import fakeredis

        from brave.config.settings import AppConfig
        from brave.lanes.tripadvisor.client import TripAdvisorClient

        config = AppConfig().tripadvisor
        redis = fakeredis.FakeRedis()
        client = TripAdvisorClient(config=config, redis=redis)
        session = client._bootstrap_session()
        assert session["cookies"], "Expected at least one DataDome cookie"
        assert "query_ids" in session
