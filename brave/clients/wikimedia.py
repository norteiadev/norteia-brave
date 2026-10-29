"""WikimediaClient — Wikidata Query Service + Commons API + upload server (image lane).

Async port of the image POCs (scripts/poc_images/commons.py, research_notes/.../poc/
poc_db_images.py and poc_gallery.py), shaped like brave/clients/nominatim.py.

House rules, all verified against the live services:
  - A descriptive User-Agent is MANDATORY (no UA or a library UA → 403).
  - Requests go out in SERIES (API:Etiquette, WDQS limits): one gap between ANY two
    Wikimedia calls, local gate + a Redis slot shared by every worker process.
  - Commons answers HTTP 200 with ``{"error": {"code": "cirrussearch-too-busy-error"}}``
    (also ``maxlag``, ``ratelimited``). Read as an empty result it silently wipes a lane,
    so those codes are retried like a 429; any other error code raises at once.
  - The upload server renders only its standard thumbnail widths (250/330/960/1280/1920);
    any other width is a 400. The original URL carries a ``?utm_`` query that must be
    stripped before the ``/thumb/`` URL is built.

Guard: raises RuntimeError when AppConfig().run_real_externals is False.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote

import httpx
import structlog
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential

if TYPE_CHECKING:
    from brave.config.settings import ImagesConfig

logger = structlog.get_logger(__name__)

WIKIMEDIA_SLOT_KEY: str = "brave:wikimedia:slot"

# Commons API error codes that mean "come back later", not "no result".
_BUSY_CODES = frozenset({"cirrussearch-too-busy-error", "maxlag", "ratelimited"})

_EXT_KEYS = ("License", "LicenseShortName", "LicenseUrl", "Artist", "Restrictions", "Assessments")


class WikimediaError(Exception):
    """A Commons API error answer (``{"error": ...}`` with HTTP 200)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class WdItem:
    """A Wikidata item candidate: labels (pt/en), P18 file titles, P373 category."""

    qid: str
    labels: frozenset[str]
    p18: tuple[str, ...] = ()
    p373: str | None = None
    dist_km: float = 0.0
    sitelinks: int = 0


@dataclass(frozen=True)
class CommonsFile:
    """One Commons file's imageinfo. ``license_code`` is the machine code (cc-by-sa-4.0),
    ``license`` the display name (CC BY-SA 4.0); ``artist`` has the HTML stripped."""

    title: str
    url: str
    page: str
    width: int
    height: int
    mime: str
    license_code: str = ""
    license: str = ""
    license_url: str = ""
    artist: str = ""
    restrictions: str = ""
    assessment: str = ""


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, WikimediaError):
        return exc.code in _BUSY_CODES
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 500, 502, 503, 504)
    return isinstance(exc, (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError))


def _strip_html(s: str | None) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s or "")).strip()


def file_title(commons_url: str) -> str:
    """``.../Special:FilePath/Foo%20bar.jpg`` (a WDQS P18 value) → ``File:Foo bar.jpg``."""
    return "File:" + unquote(commons_url.rsplit("/", 1)[-1]).replace("_", " ")


def _qid(uri: str) -> str:
    return uri.rsplit("/", 1)[-1]


def _items(bindings: list[dict[str, Any]]) -> list[WdItem]:
    """Fold SPARQL rows (one per label × P18) into one WdItem per QID, first-seen order."""
    acc: dict[str, dict[str, Any]] = {}
    for r in bindings:
        q = _qid(r["item"]["value"])
        e = acc.setdefault(q, {"labels": set(), "p18": [], "p373": None, "dist": 0.0, "sl": 0})
        if "label" in r:
            e["labels"].add(r["label"]["value"])
        if "p18" in r:
            t = file_title(r["p18"]["value"])
            if t not in e["p18"]:
                e["p18"].append(t)
        if "p373" in r:
            e["p373"] = r["p373"]["value"]
        if "dist" in r:
            e["dist"] = float(r["dist"]["value"])
        if "sl" in r:
            e["sl"] = int(r["sl"]["value"])
    return [
        WdItem(q, frozenset(e["labels"]), tuple(e["p18"]), e["p373"], e["dist"], e["sl"])
        for q, e in acc.items()
    ]


# Municipality seats (P1585 = IBGE code) and human settlements are never an attraction.
_NOT_A_PLACE = """
  FILTER NOT EXISTS { ?item wdt:P1585 [] }
  FILTER NOT EXISTS { ?item wdt:P31/wdt:P279* wd:Q486972 }"""


class WikimediaClient:
    """Real Wikimedia client — async httpx, tenacity retry, serial pacing.

    Args:
        config: ImagesConfig (URLs, UA, pacing, retry, thumb width).
        redis:  Redis client for the cross-process request slot.
    """

    def __init__(self, config: ImagesConfig, redis: Any) -> None:
        from brave.config.settings import AppConfig

        if not AppConfig().run_real_externals:
            raise RuntimeError(
                "WikimediaClient: run_real_externals=False — use NullWikimediaClient offline. "
                "Set RUN_REAL_EXTERNALS=true to enable real Wikimedia calls."
            )
        self._config = config
        self._redis = redis
        self._last_request_ts = 0.0
        self._hc: httpx.AsyncClient | None = None

    def _new_http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self._config.timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": self._config.user_agent, "Accept-Encoding": "gzip"},
        )

    async def __aenter__(self) -> WikimediaClient:
        self._hc = self._new_http()
        return self

    async def __aexit__(self, *args: Any) -> None:
        hc, self._hc = self._hc, None
        if hc is not None:
            await hc.aclose()

    @asynccontextmanager
    async def _http(self) -> AsyncIterator[httpx.AsyncClient]:
        if self._hc is not None:
            yield self._hc
            return
        async with self._new_http() as hc:
            yield hc

    async def _pace(self) -> None:
        """Local gap, then the cross-process slot (best-effort, as in nominatim.py)."""
        gap = self._config.min_interval_s - (time.monotonic() - self._last_request_ts)
        if gap > 0:
            await asyncio.sleep(gap)
        self._last_request_ts = time.monotonic()
        px = int(self._config.min_interval_s * 1000)
        if px <= 0:
            return
        # ponytail: SET NX PX spin on one key, no fairness; a queue if many workers contend.
        try:
            for _ in range(100):
                if self._redis.set(WIKIMEDIA_SLOT_KEY, "1", nx=True, px=px):
                    return
                await asyncio.sleep(max(self._redis.pttl(WIKIMEDIA_SLOT_KEY), 10) / 1000)
        except Exception as exc:  # noqa: BLE001
            logger.warning("wikimedia_slot_error", error=type(exc).__name__)

    async def _send(self, method: str, url: str, *, api: bool = False, **kw: Any) -> httpx.Response:
        """One paced request with retry. ``api=True`` also checks Commons' 200-with-error."""
        cfg = self._config
        async for attempt in AsyncRetrying(
            retry=retry_if_exception(_retryable),
            stop=stop_after_attempt(cfg.max_attempts),
            wait=wait_exponential(multiplier=cfg.backoff_s, max=60),
            reraise=True,
        ):
            with attempt:
                await self._pace()
                async with self._http() as hc:
                    resp = await hc.request(method, url, **kw)
                resp.raise_for_status()
                if api:
                    err = resp.json().get("error")
                    if err:
                        logger.info("wikimedia_api_error", code=err.get("code"))
                        raise WikimediaError(str(err.get("code")))
                return resp
        raise AssertionError("unreachable")  # pragma: no cover

    async def _api(self, params: dict[str, Any]) -> dict[str, Any]:
        resp = await self._send(
            "GET", self._config.commons_api, api=True,
            params={**params, "format": "json", "formatversion": "2"},
        )
        return resp.json()

    async def _sparql(self, query: str) -> list[dict[str, Any]]:
        resp = await self._send(
            "POST", self._config.wdqs_url, data={"query": query},
            headers={"Accept": "application/sparql-results+json"},
        )
        return resp.json()["results"]["bindings"]

    # -- Wikidata -------------------------------------------------------------------

    async def items_around(self, lat: float, lon: float, km: float) -> list[WdItem]:
        """Items with coordinates within ``km`` of the point (no municipalities)."""
        return _items(await self._sparql(f"""
            SELECT ?item ?label ?p18 ?p373 ?dist WHERE {{
              SERVICE wikibase:around {{
                ?item wdt:P625 ?loc .
                bd:serviceParam wikibase:center "Point({lon} {lat})"^^geo:wktLiteral .
                bd:serviceParam wikibase:radius "{km}" .
                bd:serviceParam wikibase:distance ?dist .
              }}
              ?item rdfs:label ?label . FILTER(LANG(?label) IN ("pt", "pt-br", "en"))
              {_NOT_A_PLACE}
              OPTIONAL {{ ?item wdt:P18 ?p18 }}
              OPTIONAL {{ ?item wdt:P373 ?p373 }}
            }} LIMIT 3000"""))

    async def items_by_name(self, name: str, lat: float, lon: float, km: float) -> list[WdItem]:
        """Entity search for ``name`` (pt), kept when its coordinates are within ``km``.

        The fallback for drifted coordinates: the caller still has to name-match the labels.
        """
        return _items(await self._sparql(f"""
            SELECT ?item ?label ?p18 ?p373 ?dist WHERE {{
              SERVICE wikibase:mwapi {{
                bd:serviceParam wikibase:api "EntitySearch"; wikibase:endpoint "www.wikidata.org";
                                mwapi:search {json.dumps(name)}; mwapi:language "pt" .
                ?item wikibase:apiOutputItem mwapi:item .
              }}
              ?item wdt:P625 ?loc ; rdfs:label ?label . FILTER(LANG(?label) IN ("pt", "en"))
              FILTER NOT EXISTS {{ ?item wdt:P1585 [] }}
              OPTIONAL {{ ?item wdt:P18 ?p18 }}
              OPTIONAL {{ ?item wdt:P373 ?p373 }}
              BIND(geof:distance(?loc, "Point({lon} {lat})"^^geo:wktLiteral) AS ?dist)
              FILTER(?dist <= {km})
            }} LIMIT 200"""))

    # -- Commons --------------------------------------------------------------------

    async def image_info(self, titles: list[str]) -> dict[str, CommonsFile]:
        """imageinfo + extmetadata, in batches of 50 (the anonymous API limit)."""
        out: dict[str, CommonsFile] = {}
        titles = list(dict.fromkeys(titles))
        for i in range(0, len(titles), 50):
            data = await self._api({
                "action": "query",
                "titles": "|".join(titles[i : i + 50]),
                "prop": "imageinfo",
                "iiprop": "url|size|mime|extmetadata",
            })
            for page in data.get("query", {}).get("pages", []):
                ii = (page.get("imageinfo") or [None])[0]
                if not ii:
                    continue
                meta = {k: str((ii.get("extmetadata") or {}).get(k, {}).get("value") or "") for k in _EXT_KEYS}
                out[page["title"]] = CommonsFile(
                    title=page["title"],
                    url=ii.get("url") or "",
                    page=ii.get("descriptionurl") or "",
                    width=int(ii.get("width") or 0),
                    height=int(ii.get("height") or 0),
                    mime=ii.get("mime") or "",
                    license_code=meta["License"],
                    license=meta["LicenseShortName"],
                    license_url=meta["LicenseUrl"],
                    artist=_strip_html(meta["Artist"])[:255],
                    restrictions=meta["Restrictions"],
                    assessment=meta["Assessments"],
                )
        return out

    def download_url(self, f: CommonsFile) -> str:
        """Standard-width thumbnail, or the original when it is not wider; never a query."""
        url = f.url.split("?", 1)[0]
        width = self._config.thumb_width
        if f.width <= width:
            return url
        base, name = url.rsplit("/", 1)
        return f"{base.replace('/commons/', '/commons/thumb/', 1)}/{name}/{width}px-{name}"

    async def download(self, f: CommonsFile) -> tuple[bytes, str]:
        """The file's bytes and Content-Type (see download_url)."""
        resp = await self._send("GET", self.download_url(f))
        return resp.content, resp.headers.get("content-type", f.mime).split(";")[0]


def _check_protocol_compliance() -> None:
    """Compile-time structural typing assertion (not called at runtime)."""
    from brave.clients.base import WikimediaClientProtocol

    _c: WikimediaClientProtocol = WikimediaClient  # type: ignore[assignment]  # noqa: F841
