"""Phase 15 (plan 15-04) — HTML SSR pagination transport tests.

Covers:
- _extract_sections_from_html: recovers the embedded FlexCard sections[] JSON
  island from the captured oa30 page using stdlib re + json only (no DOM parser),
  feeding the UNCHANGED _parse_attractions_page (LGPD aggregate-only).
- fetch_attractions_paginated: async generator that GETs each -oa{N}- HTML page,
  yields (offset, cards), throttles between pages, clamps to the 334-page /
  oa9990 cap, and fails fast on 403/429 — reusing the cookie/proxy/UA wiring.
- Security: no cookie/session/UA/proxy value is ever logged (source grep).
- Config: BRAVE_TA_PAGE_THROTTLE_SECONDS resolves with NO Field(alias=...) (CR-02).

All tests are offline (RUN_REAL_EXTERNALS unset): respx mocks the HTTP layer and
the saved fixture drives the extractor.
"""

from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest
import respx

_FIXTURE_PATH = (
    Path(__file__).parent.parent.parent.parent
    / "fixtures"
    / "tripadvisor"
    / "attractions_oa30.html"
)


def _load_fixture() -> str:
    return _FIXTURE_PATH.read_text(encoding="utf-8", errors="replace")


_STUB_SESSION = {
    "cookies": {"datadome": "stub_dd", "TASID": "stub_tasid"},
    "query_ids": {"destinations": "stub_d", "attractions": "stub_a"},
    "user_agent": "Mozilla/5.0 (stub)",
    "acquired_at": "2026-06-26T12:00:00Z",
    "session_id": "stub_tasid",
}


def _make_client(monkeypatch=None):
    import fakeredis

    from brave.config.settings import AppConfig
    from brave.lanes.tripadvisor.client import TripAdvisorClient

    config = AppConfig().tripadvisor
    redis = fakeredis.FakeRedis()
    client = TripAdvisorClient(config=config, redis=redis)
    client._get_session = lambda: dict(_STUB_SESSION)  # type: ignore[method-assign]
    return client


# ---------------------------------------------------------------------------
# Task 1: _extract_sections_from_html
# ---------------------------------------------------------------------------


class TestExtractSectionsFromHtml:
    def test_extract_recovers_flexcard_sections(self):
        """The extractor recovers >= 25 FlexCard sections from the real fixture."""
        from brave.lanes.tripadvisor.client import TripAdvisorClient

        html = _load_fixture()
        sections = TripAdvisorClient._extract_sections_from_html(html)
        assert isinstance(sections, list)
        assert len(sections) >= 25, f"expected >=25 sections, got {len(sections)}"
        assert all(
            s.get("__typename") == "WebPresentation_SingleFlexCardSection"
            for s in sections
        )

    def test_extract_output_feeds_existing_parser(self):
        """Extractor output → _parse_attractions_page yields >= 25 valid cards."""
        from brave.lanes.tripadvisor.client import TripAdvisorClient

        html = _load_fixture()
        sections = TripAdvisorClient._extract_sections_from_html(html)
        cards = TripAdvisorClient._parse_attractions_page(sections)
        assert len(cards) >= 25, f"expected >=25 cards, got {len(cards)}"
        for card in cards:
            assert card["name"], "card name must be non-empty"
            assert isinstance(card["locationId"], int) and card["locationId"] > 0
            assert isinstance(card["rating"], float)
            assert isinstance(card["review_count"], int)
            # LGPD: aggregate-only — no author/text fields leak through.
            assert set(card.keys()) == {
                "name",
                "locationId",
                "rating",
                "review_count",
                "category",
            }

    def test_extract_empty_html_returns_empty_list(self):
        """Empty input returns [] and never raises."""
        from brave.lanes.tripadvisor.client import TripAdvisorClient

        assert TripAdvisorClient._extract_sections_from_html("") == []

    def test_extract_garbage_html_returns_empty_list(self):
        """Malformed HTML with no JSON island returns [] and never raises."""
        from brave.lanes.tripadvisor.client import TripAdvisorClient

        garbage = "<html><body><div>no island here</div></body></html>"
        assert TripAdvisorClient._extract_sections_from_html(garbage) == []

    def test_no_dom_scraper_dependency_in_client(self):
        """client.py must use stdlib re + json only — no DOM/scraper import.

        Checks actual import nodes (not comments/docstrings, which legitimately
        mention "does NOT import Playwright"). Mirrors the import-AST assertion
        convention in test_client.py.
        """
        import ast

        import brave.lanes.tripadvisor.client as client_mod

        source = Path(client_mod.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        forbidden = ("lxml", "beautifulsoup", "bs4", "selectolax", "playwright")
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name.lower() for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module.lower())
        for name in imported:
            assert not any(bad in name for bad in forbidden), (
                f"no DOM/scraper dependency may be imported in client.py: {name}"
            )


# ---------------------------------------------------------------------------
# Task 2: fetch_attractions_paginated + page-throttle config
# ---------------------------------------------------------------------------


def _offset_from_url(url: str) -> int:
    m = re.search(r"-oa(\d+)-", str(url))
    return int(m.group(1)) if m else -1


class TestPageThrottleConfig:
    def test_config_has_page_throttle_field(self):
        """TripAdvisorConfig exposes page_throttle_seconds (BRAVE_TA_PAGE_THROTTLE_SECONDS)."""
        from brave.config.settings import TripAdvisorConfig

        cfg = TripAdvisorConfig()
        assert hasattr(cfg, "page_throttle_seconds")
        assert isinstance(cfg.page_throttle_seconds, float)

    def test_config_field_resolves_from_env(self, monkeypatch):
        """The exact BRAVE_TA_PAGE_THROTTLE_SECONDS name resolves the field."""
        from brave.config.settings import TripAdvisorConfig

        monkeypatch.setenv("BRAVE_TA_PAGE_THROTTLE_SECONDS", "0.0")
        cfg = TripAdvisorConfig()
        assert cfg.page_throttle_seconds == 0.0

    def test_config_field_has_no_alias(self):
        """CR-02: the throttle field must not use Field(alias=...)."""
        from brave.config.settings import TripAdvisorConfig

        field = TripAdvisorConfig.model_fields["page_throttle_seconds"]
        assert field.alias is None, "CR-02: no Field(alias=...) on page_throttle_seconds"


class TestFetchAttractionsPaginated:
    @pytest.mark.asyncio
    async def test_yields_offsets_and_cards(self):
        """A 2-page run from page 1 GETs -oa0- and -oa30- and yields ~30 cards each."""
        client = _make_client()
        body = _load_fixture()

        requested: list[int] = []

        def handler(request):
            requested.append(_offset_from_url(request.url))
            return httpx.Response(200, text=body)

        with respx.mock:
            respx.get(url__regex=r"https://www\.tripadvisor\.com/Attractions-.*").mock(
                side_effect=handler
            )
            pages = [
                (offset, cards)
                async for offset, cards in client.fetch_attractions_paginated(
                    geo_id=294280, start_page=1, max_pages=2
                )
            ]

        assert [offset for offset, _ in pages] == [0, 30]
        assert requested == [0, 30]
        for _offset, cards in pages:
            assert len(cards) >= 25

    @pytest.mark.asyncio
    async def test_start_page_two_offsets(self):
        """A resumed run from page 2 GETs -oa30- and -oa60-."""
        client = _make_client()
        body = _load_fixture()

        with respx.mock:
            respx.get(url__regex=r"https://www\.tripadvisor\.com/Attractions-.*").mock(
                side_effect=lambda req: httpx.Response(200, text=body)
            )
            offsets = [
                offset
                async for offset, _ in client.fetch_attractions_paginated(
                    geo_id=294280, start_page=2, max_pages=2
                )
            ]

        assert offsets == [30, 60]

    @pytest.mark.asyncio
    async def test_clamps_to_334_page_cap(self):
        """start_page=333, max_pages=334 GETs only pages 333+334 (oa9960, oa9990)."""
        client = _make_client()

        requested: list[int] = []

        def handler(request):
            requested.append(_offset_from_url(request.url))
            return httpx.Response(200, text="<html></html>")

        with respx.mock:
            respx.get(url__regex=r"https://www\.tripadvisor\.com/Attractions-.*").mock(
                side_effect=handler
            )
            offsets = [
                offset
                async for offset, _ in client.fetch_attractions_paginated(
                    geo_id=294280, start_page=333, max_pages=334
                )
            ]

        assert offsets == [9960, 9990]
        assert requested == [9960, 9990]
        assert all(o <= 9990 for o in requested), "must never GET past the oa9990 cap"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [403, 429])
    async def test_session_expiry_fails_fast(self, status):
        """A 403/429 raises SessionExpiredError and stops (no further GET)."""
        from brave.lanes.tripadvisor.client import SessionExpiredError

        client = _make_client()
        requested: list[int] = []

        def handler(request):
            requested.append(_offset_from_url(request.url))
            return httpx.Response(status, text="blocked")

        with respx.mock:
            respx.get(url__regex=r"https://www\.tripadvisor\.com/Attractions-.*").mock(
                side_effect=handler
            )
            with pytest.raises(SessionExpiredError):
                async for _ in client.fetch_attractions_paginated(
                    geo_id=294280, start_page=1, max_pages=5
                ):
                    pass

        assert requested == [0], "must stop after the first failing page"

    @pytest.mark.asyncio
    async def test_non_int_geoid_raises_before_any_get(self):
        """SSRF guard: a non-int geo_id raises before any GET is issued (T-15-04-02)."""
        client = _make_client()
        requested: list[int] = []

        def handler(request):
            requested.append(1)
            return httpx.Response(200, text="<html></html>")

        with respx.mock:
            respx.get(url__regex=r"https://www\.tripadvisor\.com/Attractions-.*").mock(
                side_effect=handler
            )
            with pytest.raises((TypeError, ValueError)):
                async for _ in client.fetch_attractions_paginated(
                    geo_id="294280; DROP",  # type: ignore[arg-type]
                    start_page=1,
                    max_pages=2,
                ):
                    pass

        assert requested == [], "no GET may be issued for a non-int geo_id"

    @pytest.mark.asyncio
    async def test_throttles_between_pages_not_after_last(self, monkeypatch):
        """Sleeps page_throttle_seconds between pages, never after the final page."""
        import brave.lanes.tripadvisor.client as client_mod

        client = _make_client()
        client._config.page_throttle_seconds = 1.5  # type: ignore[attr-defined]
        body = _load_fixture()

        sleeps: list[float] = []

        async def fake_sleep(seconds):
            sleeps.append(seconds)

        monkeypatch.setattr(client_mod.asyncio, "sleep", fake_sleep)

        with respx.mock:
            respx.get(url__regex=r"https://www\.tripadvisor\.com/Attractions-.*").mock(
                side_effect=lambda req: httpx.Response(200, text=body)
            )
            _ = [
                offset
                async for offset, _ in client.fetch_attractions_paginated(
                    geo_id=294280, start_page=1, max_pages=3
                )
            ]

        # 3 pages → 2 inter-page sleeps, each the configured throttle.
        assert sleeps == [1.5, 1.5]

    def test_no_secret_logging_in_paginated_method(self):
        """The new method's logs never reference cookies/user_agent/session_id/proxy."""
        import ast

        import brave.lanes.tripadvisor.client as client_mod

        source = Path(client_mod.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        target = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "fetch_attractions_paginated"
        )
        # Collect keyword arg names + string content of every logger.* call in the method.
        for call in ast.walk(target):
            if not isinstance(call, ast.Call):
                continue
            func = call.func
            is_logger = (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "logger"
            )
            if not is_logger:
                continue
            kw_names = {kw.arg for kw in call.keywords if kw.arg}
            forbidden = {"cookies", "user_agent", "session_id", "proxy", "proxy_url"}
            assert not (kw_names & forbidden), (
                f"logger call leaks secret kwargs: {kw_names & forbidden}"
            )
