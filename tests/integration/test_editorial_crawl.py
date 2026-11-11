"""crawl_uf against the test DB with a fake fetcher (no network): persistence, hash skip,
re-extraction on change, concurrency cap."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from brave.core.models import EditorialDocument
from brave.domains.editorial.crawler import category_url, crawl_uf, kml_url

pytestmark = pytest.mark.integration

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "editorial"
POST = "https://www.viagensecaminhos.com/anchieta-es/"
MID = "1o_KkTvTTcAWuokGAkchwX9oj9_94T0U"


class FakeFetcher:
    """url -> text (None = 404). Records concurrency; an Exception value is raised."""

    def __init__(self, pages: dict[str, str | Exception | None]) -> None:
        self.pages, self.calls, self.now, self.peak = pages, [], 0, 0

    async def get_text(self, url: str) -> str | None:
        self.calls.append(url)
        self.now += 1
        self.peak = max(self.peak, self.now)
        await asyncio.sleep(0.01)
        self.now -= 1
        page = self.pages.get(url)
        if isinstance(page, Exception):
            raise page
        return page


def _pages(html: str | None = None) -> dict[str, str | None]:
    category = f'<h3 class="entry-title td-module-title"><a href="{POST}">Anchieta</a></h3>'
    return {
        category_url("ES", 1): category,
        category_url("ES", 2): None,  # 404 ends the paging
        POST: html if html is not None else (FIX / "anchieta-es.html").read_text(encoding="utf-8"),
        kml_url(MID): (FIX / "anchieta-es.kml").read_text(encoding="utf-8"),
    }


@pytest.fixture
def session(db_engine):
    conn = db_engine.connect()
    outer = conn.begin()
    s = Session(bind=conn, join_transaction_mode="create_savepoint")
    try:
        yield s
    finally:
        s.close()
        outer.rollback()
        conn.close()


def test_crawl_stores_the_post_with_body_date_and_kml_points(session) -> None:
    result = asyncio.run(crawl_uf(session, FakeFetcher(_pages()), "ES"))
    assert (result.fetched, result.unchanged, result.failed) == (1, 0, 0)
    doc = session.get(EditorialDocument, POST)
    assert doc.uf == "ES" and doc.source == "viagensecaminhos"
    assert str(doc.published_time) == "2020-02-17"
    assert "A cidade de Anchieta" in doc.body_text
    assert len(doc.kml_points) == 22
    assert doc.extracted_at is None and doc.batch_id is None and doc.error is None
    assert len(doc.content_hash) == 64


def test_same_content_is_not_fetched_for_kml_nor_reset(session) -> None:
    asyncio.run(crawl_uf(session, FakeFetcher(_pages()), "ES"))
    doc = session.get(EditorialDocument, POST)
    stamp = datetime(2026, 1, 1, tzinfo=UTC)
    doc.extracted_at = stamp
    session.flush()

    fetcher = FakeFetcher(_pages())
    result = asyncio.run(crawl_uf(session, fetcher, "ES"))
    assert (result.fetched, result.unchanged) == (0, 1)
    assert kml_url(MID) not in fetcher.calls
    assert session.get(EditorialDocument, POST).extracted_at == stamp


def test_changed_content_resets_the_extraction_state(session) -> None:
    asyncio.run(crawl_uf(session, FakeFetcher(_pages()), "ES"))
    doc = session.get(EditorialDocument, POST)
    doc.extracted_at, doc.error = datetime(2026, 1, 1, tzinfo=UTC), "old"
    session.flush()

    changed = (
        (FIX / "anchieta-es.html")
        .read_text(encoding="utf-8")
        .replace(
            "A cidade de <strong>Anchieta</strong>", "A cidade de <strong>Anchieta (ES)</strong>"
        )
    )
    result = asyncio.run(crawl_uf(session, FakeFetcher(_pages(changed)), "ES"))
    assert result.fetched == 1
    session.refresh(doc)
    assert doc.extracted_at is None and doc.error is None and "Anchieta (ES)" in doc.body_text


def test_a_post_in_flight_is_left_alone(session) -> None:
    asyncio.run(crawl_uf(session, FakeFetcher(_pages()), "ES"))
    doc = session.get(EditorialDocument, POST)
    doc.batch_id = "msgbatch_x"
    session.flush()
    changed = (
        (FIX / "anchieta-es.html").read_text(encoding="utf-8").replace("Anchieta", "Anchieta!")
    )
    result = asyncio.run(crawl_uf(session, FakeFetcher(_pages(changed)), "ES"))
    assert result.fetched == 0 and result.in_flight == 1
    assert session.get(EditorialDocument, POST).batch_id == "msgbatch_x"


def test_failures_are_counted_and_do_not_stop_the_uf(session) -> None:
    pages = _pages()
    pages[POST] = RuntimeError("boom")
    result = asyncio.run(crawl_uf(session, FakeFetcher(pages), "ES"))
    assert (result.fetched, result.failed) == (0, 1)
    assert session.get(EditorialDocument, POST) is None


def test_kml_failure_keeps_the_post_without_points(session) -> None:
    pages = _pages()
    pages[kml_url(MID)] = None
    asyncio.run(crawl_uf(session, FakeFetcher(pages), "ES"))
    assert session.get(EditorialDocument, POST).kml_points == []


def test_at_most_three_fetches_run_at_once(session) -> None:
    links = [f"https://www.viagensecaminhos.com/p{i}-es/" for i in range(8)]
    pages: dict[str, str | None] = {
        category_url("ES", 1): "".join(
            f'<h3 class="entry-title"><a href="{u}">x</a></h3>' for u in links
        ),
        category_url("ES", 2): None,
    }
    for i, u in enumerate(links):
        pages[u] = f'<div class="td-post-content"><p>texto {i}</p></div>'
    fetcher = FakeFetcher(pages)
    result = asyncio.run(crawl_uf(session, fetcher, "ES"))
    assert result.fetched == 8
    assert 1 < fetcher.peak <= 3
