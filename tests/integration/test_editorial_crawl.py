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


# -- review-1: the KML is retried; a post listed under two UFs ------------------------------


def test_a_failed_kml_is_fetched_again_on_the_next_crawl_without_re_extraction(session) -> None:
    pages = _pages()
    pages[kml_url(MID)] = RuntimeError("google 503")
    asyncio.run(crawl_uf(session, FakeFetcher(pages), "ES"))
    doc = session.get(EditorialDocument, POST)
    assert doc.kml_points == [] and "503" in doc.kml_error
    stamp = datetime(2026, 1, 1, tzinfo=UTC)
    doc.extracted_at = stamp
    session.flush()

    fetcher = FakeFetcher(_pages())  # same body, the map answers now
    result = asyncio.run(crawl_uf(session, fetcher, "ES"))
    assert kml_url(MID) in fetcher.calls
    assert (result.unchanged, result.kml_refreshed) == (1, 1)
    session.refresh(doc)
    assert len(doc.kml_points) == 22 and doc.kml_error is None
    assert doc.extracted_at == stamp  # the text did not change: no new paid extraction


def test_a_post_with_maps_but_no_points_retries_the_kml(session) -> None:
    pages = _pages()
    pages[kml_url(MID)] = None  # the map answered 404 the first time
    asyncio.run(crawl_uf(session, FakeFetcher(pages), "ES"))
    assert session.get(EditorialDocument, POST).kml_points == []
    fetcher = FakeFetcher(_pages())
    asyncio.run(crawl_uf(session, fetcher, "ES"))
    assert kml_url(MID) in fetcher.calls
    assert len(session.get(EditorialDocument, POST).kml_points) == 22


def test_a_partial_kml_failure_is_retried_too(session) -> None:
    html = (
        (FIX / "anchieta-es.html")
        .read_text(encoding="utf-8")
        .replace(
            "</body>",
            '<iframe src="https://www.google.com/maps/d/embed?mid=SecondMap123"></iframe></body>',
        )
    )
    pages = _pages(html)
    pages[kml_url("SecondMap123")] = RuntimeError("timeout")
    asyncio.run(crawl_uf(session, FakeFetcher(pages), "ES"))
    doc = session.get(EditorialDocument, POST)
    assert len(doc.kml_points) == 22 and doc.kml_error  # one map in, the other failed
    pages[kml_url("SecondMap123")] = (
        "<kml><Placemark><name>Pedra Nova</name><Point><coordinates>-40.1,-20.2,0"
        "</coordinates></Point></Placemark></kml>"
    )
    asyncio.run(crawl_uf(session, FakeFetcher(pages), "ES"))
    session.refresh(doc)
    assert len(doc.kml_points) == 23 and doc.kml_error is None


def test_a_post_listed_under_two_ufs_keeps_the_first_uf_and_the_crawl_goes_on(session) -> None:
    first = EditorialDocument(
        url=POST, source="viagensecaminhos", uf="MG", content_hash="x" * 64, body_text="mg",
        kml_points=[],
    )  # fmt: skip
    session.add(first)
    session.flush()
    other = "https://www.viagensecaminhos.com/so-es/"
    pages = _pages()
    pages[category_url("ES", 1)] += f'<h3 class="entry-title"><a href="{other}">x</a></h3>'
    pages[other] = '<div class="td-post-content"><p>Só no ES.</p></div>'
    fetcher = FakeFetcher(pages)
    result = asyncio.run(crawl_uf(session, fetcher, "ES"))
    assert (result.other_uf, result.fetched, result.failed) == (1, 1, 0)
    assert POST not in fetcher.calls  # not even downloaded
    session.refresh(first)
    assert first.uf == "MG" and first.body_text == "mg"
    assert session.get(EditorialDocument, other).uf == "ES"


def test_a_concurrent_crawl_that_stored_the_post_first_does_not_bring_the_uf_down(session) -> None:
    from sqlalchemy import insert

    class Racing(FakeFetcher):
        async def get_text(self, url):
            text = await super().get_text(url)
            if url == POST:  # another UF's crawl commits the same URL meanwhile
                session.execute(
                    insert(EditorialDocument).values(
                        url=POST,
                        source="viagensecaminhos",
                        uf="MG",
                        content_hash="y" * 64,
                        body_text="mg",
                        kml_points=[],
                    )  # fmt: skip
                )
            return text

    result = asyncio.run(crawl_uf(session, Racing(_pages()), "ES"))
    assert result.other_uf == 1 and result.fetched == 0


def test_a_map_that_used_to_work_and_fails_now_keeps_its_stored_points(session) -> None:
    """review-2: a refetch with one mid failing must not overwrite good points with partial
    ones. Points are merged while a map fails; replaced only when every map was read."""
    html = (
        (FIX / "anchieta-es.html")
        .read_text(encoding="utf-8")
        .replace(
            "</body>",
            '<iframe src="https://www.google.com/maps/d/embed?mid=SecondMap123"></iframe></body>',
        )
    )
    second = (
        "<kml><Placemark><name>Pedra Nova</name><Point><coordinates>-40.1,-20.2,0"
        "</coordinates></Point></Placemark></kml>"
    )
    pages = _pages(html)
    pages[kml_url("SecondMap123")] = RuntimeError("timeout")  # crawl 1: Anchieta map ok, 2nd fails
    asyncio.run(crawl_uf(session, FakeFetcher(pages), "ES"))
    doc = session.get(EditorialDocument, POST)
    assert len(doc.kml_points) == 22 and doc.kml_error

    pages[kml_url(MID)] = RuntimeError("google 503")  # crawl 2: the map that worked fails now
    pages[kml_url("SecondMap123")] = second
    asyncio.run(crawl_uf(session, FakeFetcher(pages), "ES"))
    session.refresh(doc)
    assert len(doc.kml_points) == 23  # the 22 kept + the new one
    assert MID in doc.kml_error and "SecondMap123" not in doc.kml_error

    pages[kml_url(MID)] = (FIX / "anchieta-es.kml").read_text(encoding="utf-8")
    pages[kml_url("SecondMap123")] = None  # crawl 3: every map read (one is gone: 404)
    asyncio.run(crawl_uf(session, FakeFetcher(pages), "ES"))
    session.refresh(doc)
    assert len(doc.kml_points) == 22 and doc.kml_error is None  # now replaced, exactly


def test_a_changed_post_whose_map_fails_keeps_the_stored_points(session) -> None:
    asyncio.run(crawl_uf(session, FakeFetcher(_pages()), "ES"))
    changed = (
        (FIX / "anchieta-es.html").read_text(encoding="utf-8").replace("Anchieta", "Anchieta!")
    )
    pages = _pages(changed)
    pages[kml_url(MID)] = RuntimeError("google 503")
    asyncio.run(crawl_uf(session, FakeFetcher(pages), "ES"))
    doc = session.get(EditorialDocument, POST)
    assert "Anchieta!" in doc.body_text and len(doc.kml_points) == 22 and doc.kml_error
