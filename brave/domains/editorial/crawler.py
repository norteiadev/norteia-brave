"""Crawl viagensecaminhos.com: category pages → posts → body text + published_time + KML.

Parsing is pure (``extract_body`` & co, tested on real fixtures); ``crawl_uf`` does the I/O
through an ``EditorialFetcher`` (real or null, from ``brave.clients.factory``) and persists
``editorial_documents``. A post whose ``content_hash`` did not change is not re-extracted.

Port of the spike's ``blog_extract.py`` plus the two exclusions the spike did by hand:
reader comments (they live outside ``div.td-post-content``) and affiliate / "leia também"
blocks (any block holding a ``rel="sponsored"`` link, or opening with "Leia também").
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from html.parser import HTMLParser
from typing import Any, Protocol

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from brave.core.models import EditorialDocument

logger = structlog.get_logger(__name__)

SOURCE = "viagensecaminhos"
MAX_CONCURRENT_FETCHES = 3  # the site answers in ~75 s per page: be gentle with it
MAX_CATEGORY_PAGES = 40

BASE_URL = "https://www.viagensecaminhos.com"
CONTENT_CLASS = "td-post-content"

UF_SLUGS: dict[str, str] = {
    "AC": "acre", "AL": "alagoas", "AP": "amapa", "AM": "amazonas", "BA": "bahia",
    "CE": "ceara", "DF": "distrito-federal", "ES": "espirito-santo", "GO": "goias",
    "MA": "maranhao", "MT": "mato-grosso", "MS": "mato-grosso-do-sul", "MG": "minas-gerais",
    "PA": "para", "PB": "paraiba", "PR": "parana", "PE": "pernambuco", "PI": "piaui",
    "RJ": "rio-de-janeiro", "RN": "rio-grande-do-norte", "RS": "rio-grande-do-sul",
    "RO": "rondonia", "RR": "roraima", "SC": "santa-catarina", "SP": "sao-paulo",
    "SE": "sergipe", "TO": "tocantins",
}  # fmt: skip

_BLOCK = {"p", "div", "li", "br", "h1", "h2", "h3", "h4", "h5", "tr", "blockquote"}
_STACKED = {"p", "div", "li", "blockquote", "h1", "h2", "h3", "h4", "h5"}
_SKIP = {"script", "style", "noscript", "figure", "figcaption", "iframe", "svg"}
_RELATED = (
    "leia também",
    "leia tambem",
    "veja também",
    "veja tambem",
    "leia mais",
    "confira também",
)


class _Body(HTMLParser):
    """Text of the largest ``div.td-post-content``; sponsored / related blocks are cut."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0  # div depth inside the container (0 = outside)
        self.skip = 0
        self.sponsored_a = False
        self.out: list[str] = []
        self.stack: list[list[Any]] = []  # [tag, start index in out, tainted]
        self.chunks: list[str] = []

    def _pop(self, index: int) -> None:
        tag, start, tainted = self.stack[index]
        del self.stack[index:]
        text = "".join(self.out[start:]).strip().lower()
        if tainted or text.startswith(_RELATED):
            del self.out[start:]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if self.depth == 0:
            if tag == "div" and CONTENT_CLASS in (a.get("class") or "").split():
                self.depth, self.out, self.stack = 1, [], []
            return
        if tag in _SKIP:
            self.skip += 1
        if tag == "a" and "sponsored" in (a.get("rel") or "").split():
            self.sponsored_a = True
            if self.stack:  # never the container itself: that would wipe the whole post
                self.stack[-1][2] = True
        if tag in _STACKED:
            if tag in {"p", "li"} and self.stack and self.stack[-1][0] == tag:
                self._pop(len(self.stack) - 1)  # an unclosed <p>/<li> ends at the next one
            if tag == "div":
                self.depth += 1
            self.stack.append([tag, len(self.out), False])
        if tag in {"h1", "h2", "h3", "h4"}:
            self.out.append("\n\n" + "#" * int(tag[1]) + " ")
        elif tag == "li":
            self.out.append("\n- ")
        elif tag in _BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self.depth == 0:
            return
        if tag in _SKIP and self.skip:
            self.skip -= 1
        if tag == "a":
            self.sponsored_a = False
        if tag in _STACKED:
            for i in range(len(self.stack) - 1, -1, -1):
                if self.stack[i][0] == tag:
                    self._pop(i)
                    break
        if tag == "div":
            self.depth -= 1
            if self.depth == 0:
                self.chunks.append("".join(self.out))

    def handle_data(self, data: str) -> None:
        if self.depth and not self.skip and not self.sponsored_a:
            self.out.append(data)


def extract_body(html: str) -> str:
    """Clean text of the post body ('' when the page has no ``div.td-post-content``)."""
    parser = _Body()
    parser.feed(html)
    body = max(parser.chunks, key=len, default="")
    text = re.sub(r"[ \t]+", " ", body)
    return re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()


def content_hash(body_text: str) -> str:
    """sha256 of the body with whitespace collapsed (layout noise must not re-extract)."""
    return hashlib.sha256(" ".join(body_text.split()).encode()).hexdigest()


def extract_published_time(html: str) -> date | None:
    """The post's ``article:published_time`` (the only date that ages the facts: the
    ``modified_time`` of nearly every post is one mass plugin update)."""
    m = re.search(r'article:published_time" content="(\d{4})-(\d{2})-(\d{2})', html)
    return date(int(m[1]), int(m[2]), int(m[3])) if m else None


def extract_kml_mids(html: str) -> list[str]:
    """Google My Maps ids embedded in the post, in page order, deduplicated."""
    found = re.findall(r"google\.com/maps/d/[^\"'\s]*?[?&;]mid=([A-Za-z0-9_-]+)", html)
    return list(dict.fromkeys(found))


def kml_url(mid: str) -> str:
    return f"https://www.google.com/maps/d/kml?mid={mid}&forcekml=1"


def parse_kml(xml: str) -> list[dict[str, Any]]:
    """``[{"name", "lat", "lon"}]`` for every Placemark with a Point (KML is ``lon,lat,alt``)."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return []
    points: list[dict[str, Any]] = []
    for pm in root.iter():
        if not pm.tag.endswith("Placemark"):
            continue
        name, coords = "", None
        for el in pm.iter():
            if el.tag.endswith("}name") or el.tag == "name":
                name = (el.text or "").strip()
            elif el.tag.endswith("Point") or el.tag == "Point":
                coords = next((c.text for c in el.iter() if c.tag.endswith("coordinates")), None)
        if coords is None:
            continue
        try:
            lon, lat = (float(x) for x in coords.strip().split(",")[:2])
        except ValueError:
            continue
        points.append({"name": name, "lat": lat, "lon": lon})
    return points


def category_url(uf: str, page: int = 1) -> str:
    base = f"{BASE_URL}/category/destinos/brasil/{UF_SLUGS[uf.upper()]}/"
    return base if page <= 1 else f"{base}page/{page}/"


def post_links(category_html: str) -> list[str]:
    """Post URLs listed on a category page (tagDiv ``h3.entry-title > a``), deduplicated."""
    found = re.findall(
        r'<h3[^>]*class="[^"]*entry-title[^"]*"[^>]*>\s*<a\s+[^>]*href="([^"]+)"', category_html
    )
    return list(dict.fromkeys(found))


class EditorialFetcher(Protocol):
    """Blog / KML transport (real or null, from ``brave.clients.factory``)."""

    async def get_text(self, url: str) -> str | None:
        """The page text, or None when it does not exist (404/410). Raises when it could not
        be read after the retries."""
        ...


@dataclass
class CrawlResult:
    fetched: int = 0  # new or changed: needs extraction
    unchanged: int = 0  # same content_hash: not re-extracted
    in_flight: int = 0  # changed, but its batch is still running: left for the next crawl
    failed: int = 0
    urls: list[str] = field(default_factory=list)


@dataclass
class _Fetched:
    url: str
    body: str
    hash: str
    published: date | None
    points: list[dict[str, Any]]


async def _list_posts(fetcher: EditorialFetcher, uf: str) -> list[str]:
    urls: list[str] = []
    for page in range(1, MAX_CATEGORY_PAGES + 1):
        html = await fetcher.get_text(category_url(uf, page))
        new = [u for u in post_links(html or "") if u not in urls]
        if not new:
            break
        urls += new
    return urls


async def _fetch_post(fetcher: EditorialFetcher, url: str, known_hash: str | None) -> _Fetched:
    """The parsed post; with ``known_hash`` matching, a stub (no KML download). Raises when
    the page has no body."""
    html = await fetcher.get_text(url)
    body = extract_body(html or "")
    if not body:
        raise ValueError("no div.td-post-content in the page")
    digest = content_hash(body)
    if digest == known_hash:
        return _Fetched(url, body, digest, None, [])  # unchanged: no KML download
    points: list[dict[str, Any]] = []
    for mid in extract_kml_mids(html or ""):
        try:
            kml = await fetcher.get_text(kml_url(mid))
        except Exception as exc:  # noqa: BLE001 — a post without its map is still a post
            logger.warning("editorial_kml_failed", url=url, mid=mid, error=str(exc))
            continue
        for p in parse_kml(kml or ""):
            if p not in points:
                points.append(p)
    return _Fetched(url, body, digest, extract_published_time(html or ""), points)


async def crawl_uf(
    session: Session,
    fetcher: EditorialFetcher,
    uf: str,
    *,
    concurrency: int = MAX_CONCURRENT_FETCHES,
) -> CrawlResult:
    """Download every post of ``uf`` and upsert ``editorial_documents``.

    A post whose body hash is unchanged is left untouched (no re-extraction, no KML fetch);
    a changed one is reset to "not extracted" — unless its batch is still running, then it
    waits for the next crawl. One failing post is counted and logged, never stops the UF.
    """
    uf = uf.upper()
    result = CrawlResult()
    urls = await _list_posts(fetcher, uf)
    gate = asyncio.Semaphore(concurrency)
    known = {
        d.url: d
        for d in session.scalars(select(EditorialDocument).where(EditorialDocument.uf == uf))
    }

    async def one(url: str) -> tuple[str, _Fetched | Exception]:
        async with gate:
            try:
                prev = known.get(url)
                fetched = await _fetch_post(fetcher, url, prev.content_hash if prev else None)
                return url, fetched
            except Exception as exc:  # noqa: BLE001
                return url, exc

    for done in asyncio.as_completed([one(u) for u in urls]):
        url, fetched = await done
        if isinstance(fetched, Exception):
            result.failed += 1
            logger.warning("editorial_crawl_post_failed", url=url, error=str(fetched))
            continue
        doc = known.get(url)
        now = datetime.now(UTC)
        if doc is not None and doc.content_hash == fetched.hash:
            doc.fetched_at = now
            result.unchanged += 1
        elif doc is not None and doc.batch_id is not None:
            result.in_flight += 1
        else:
            if doc is None:
                doc = EditorialDocument(url=url, source=SOURCE, uf=uf)
                session.add(doc)
            doc.body_text, doc.content_hash = fetched.body, fetched.hash
            doc.published_time, doc.kml_points = fetched.published, fetched.points
            doc.fetched_at = now
            doc.batch_id = doc.batch_submitted_at = doc.extracted_at = doc.error = None
            result.fetched += 1
            result.urls.append(url)
        session.commit()
    logger.info(
        "editorial_crawl_uf", uf=uf, posts=len(urls), fetched=result.fetched,
        unchanged=result.unchanged, in_flight=result.in_flight, failed=result.failed,
    )  # fmt: skip
    return result
