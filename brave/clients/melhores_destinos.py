"""RealMelhoresDestinosClient — plain async httpx scraper for Guia Melhores Destinos.

Powers the DescriptionEnrichmentAgent (description-enrichment lane, post-Signal):
  find_attraction_url() — fuzzy-match an atrativo to its ``-l.html`` editorial page
                          via the public /sitemap.xml (Redis-cached index).
  fetch_description()   — GET the page and recover the editorial description with
                          stdlib re + json (house style — NO lxml/bs4).

Template = brave/clients/nominatim.py (plain GET, run_real_externals guard, Redis
cache + TTL, throttle, identifiable User-Agent, tenacity retry). Deliberately WITHOUT
the TripAdvisor session/DataDome complexity — this site is server-rendered and open
(robots.txt Disallow: empty; see docs/poc/melhores-destinos-atrativo-descricao.md).

URL grammar (POC §2): ``<slug>-<cityCode>-<attrId>-l.html`` — the triple is validated
together (no walkable enumeration), so discovery = filter the sitemap for the ``-l``
pages and fuzzy-match the ``<slug>`` against the slugified attraction name. Matching
reuses rapidfuzz (already a dep, used by brave/domains/tripadvisor/ibge.py) with the
same explicit accent-fold as that module.

Guard: raises RuntimeError when AppConfig().run_real_externals is False.
Use NullMelhoresDestinosClient (brave/clients/null_melhores_destinos.py) in CI/offline.
Use FakeMelhoresDestinosClient (tests/fakes/fake_melhores_destinos.py) in unit tests.

LGPD/legal (POC §4): the scraped editorial text is TRANSIENT LLM context only — it is
NOT persisted as canonical. Only the Norteia-voice rewrite (or, on rewrite failure, the
scraped text) is stored, with source provenance. Images (imgmd.net) are never re-hosted.
"""

from __future__ import annotations

import asyncio
import html
import json
import re
import time
import unicodedata
from typing import TYPE_CHECKING, Any

import httpx
import structlog
from rapidfuzz import fuzz, process
from rapidfuzz import utils as rfuzz_utils
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

if TYPE_CHECKING:
    from brave.config.settings import MelhoresDestinosConfig

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

# Redis keys: sitemap index (one long-lived list of (slug, url) pairs) + per-page cache.
MD_SITEMAP_CACHE_KEY: str = "brave:md:sitemap"
MD_PAGE_CACHE_KEY_PREFIX: str = "brave:md:page:"

# The ``-l`` attraction page suffix (POC §2 URL grammar): <slug>-<cityCode>-<attrId>-l.html
_L_PAGE_RE = re.compile(r"/([^/]+?)-\d+-\d+-l\.html$")
# <loc>…</loc> entries in the flat sitemap.xml.
_LOC_RE = re.compile(r"<loc>\s*(.*?)\s*</loc>", re.IGNORECASE | re.DOTALL)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _decode(value: Any) -> str:
    """Decode Redis response bytes to str (mirrors nominatim.py pattern)."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def _fold_accents(s: str) -> str:
    """Strip combining diacritics after NFKD (analog: ibge.py:_fold_accents)."""
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", s) if unicodedata.category(ch) != "Mn"
    )


def _slugify(name: str) -> str:
    """Fold accents + lowercase + hyphenate → a slug comparable to the URL slug."""
    folded = _fold_accents(name).lower()
    return re.sub(r"[^a-z0-9]+", "-", folded).strip("-")


def _is_retryable(exc: BaseException) -> bool:
    """429 / 5xx / connection errors are retryable; other 4xx are not."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 500, 502, 503, 504)
    if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError)):
        return True
    return False


def _extract_description(html_text: str) -> str | None:
    """Recover the editorial description from a page using ONLY stdlib re + json.

    Strategy (house style — no lxml/bs4; analog: tripadvisor/client.py peels the JSON
    island with re+json): collect candidate description strings from the JSON-LD
    ``"description"`` fields and the ``og:description`` meta tag, HTML-unescape them,
    and return the LONGEST (the editorial body beats the short social blurb). Returns
    None when no usable description is present. Never raises.
    """
    if not html_text:
        return None

    candidates: list[str] = []

    # og:description meta (order-insensitive on the two attributes). The content value
    # is delimited by a backreference to its OWN opening quote (\1), so an inner
    # apostrophe inside a double-quoted value (or vice-versa) does not truncate it.
    for m in re.finditer(
        r"""<meta[^>]+property=["']og:description["'][^>]+content=(["'])(.*?)\1""",
        html_text,
        re.IGNORECASE | re.DOTALL,
    ):
        candidates.append(m.group(2))
    for m in re.finditer(
        r"""<meta[^>]+content=(["'])(.*?)\1[^>]+property=["']og:description["']""",
        html_text,
        re.IGNORECASE | re.DOTALL,
    ):
        candidates.append(m.group(2))

    # JSON-LD "description":"…" (peel one JSON escape level via json.loads).
    for m in re.finditer(r'"description"\s*:\s*("(?:[^"\\]|\\.)*")', html_text, re.DOTALL):
        try:
            candidates.append(json.loads(m.group(1)))
        except (ValueError, TypeError):
            continue

    cleaned = [html.unescape(c).strip() for c in candidates]
    cleaned = [c for c in cleaned if len(c) >= 10]
    if not cleaned:
        return None
    return max(cleaned, key=len)


# ---------------------------------------------------------------------------
# Real client
# ---------------------------------------------------------------------------


class RealMelhoresDestinosClient:
    """Real Melhores Destinos scraper — async httpx, tenacity retry, Redis cache, throttle.

    Structurally satisfies MelhoresDestinosClientProtocol.
    Guard: raises RuntimeError when AppConfig().run_real_externals is False.

    Args:
        config: MelhoresDestinosConfig (base_url, UA, timeout, throttle, TTL, threshold).
        redis:  Redis client (sync — compatible with Celery worker + asyncio contexts).
    """

    def __init__(self, config: MelhoresDestinosConfig, redis: Any) -> None:
        from brave.config.settings import AppConfig

        if not AppConfig().run_real_externals:
            raise RuntimeError(
                "RealMelhoresDestinosClient: run_real_externals=False — "
                "use NullMelhoresDestinosClient / FakeMelhoresDestinosClient in the "
                "default test suite. Set RUN_REAL_EXTERNALS=true to enable real calls."
            )
        self._config = config
        self._redis = redis
        self._min_interval: float = config.throttle_seconds
        self._last_request_ts: float = 0.0
        self._cache_ttl: int = config.cache_ttl

    async def _throttle(self) -> None:
        """Politeness throttle — ≥ throttle_seconds between page GETs."""
        if self._min_interval <= 0:
            return
        elapsed = time.monotonic() - self._last_request_ts
        if elapsed < self._min_interval:
            await asyncio.sleep(self._min_interval - elapsed)
        self._last_request_ts = time.monotonic()

    def _http_kwargs(self) -> dict[str, Any]:
        kw: dict[str, Any] = {
            "timeout": self._config.timeout_seconds,
            "follow_redirects": True,
        }
        if self._config.proxy_url:
            kw["proxy"] = self._config.proxy_url
        return kw

    @retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def _load_sitemap_index(self) -> list[tuple[str, str]]:
        """Load (slug, url) pairs for every ``-l`` attraction page (Redis-cached).

        Fetches /sitemap.xml once (long TTL), extracts the ``<loc>`` entries, keeps
        only the ``-l.html`` attraction pages, and indexes their ``<slug>`` for fuzzy
        matching. The whole index is cached as a JSON list of [slug, url] pairs.
        """
        raw = _decode(self._redis.get(MD_SITEMAP_CACHE_KEY))
        if raw:
            return [(s, u) for s, u in json.loads(raw)]

        await self._throttle()
        url = f"{self._config.base_url.rstrip('/')}/sitemap.xml"
        headers = {"User-Agent": self._config.user_agent}
        async with httpx.AsyncClient(**self._http_kwargs()) as hc:
            resp = await hc.get(url, headers=headers)
        resp.raise_for_status()

        index: list[tuple[str, str]] = []
        for loc in _LOC_RE.findall(resp.text):
            m = _L_PAGE_RE.search(loc)
            if m:
                index.append((m.group(1).lower(), loc))

        self._redis.setex(
            MD_SITEMAP_CACHE_KEY, self._cache_ttl, json.dumps(index)
        )
        logger.info("md_sitemap_indexed", attraction_pages=len(index))
        return index

    async def find_attraction_url(
        self, nome: str, municipio: str, uf: str
    ) -> str | None:
        """Fuzzy-match an atrativo name to its ``-l.html`` page URL, or None.

        The site URL slug carries the attraction name only (POC §2: the cityCode is
        site-internal, distrito-level, NOT IBGE), so matching is fuzzy on the
        slugified ``nome`` against the sitemap slugs (rapidfuzz token_sort_ratio,
        accent-folded, threshold from config). ``municipio``/``uf`` are logged for
        traceability; a miss returns None → the agent keeps the floor.
        """
        if not nome or not nome.strip():
            return None

        try:
            index = await self._load_sitemap_index()
        except Exception:  # noqa: BLE001 — a sitemap miss must degrade to the floor
            logger.warning("md_sitemap_unavailable", uf=uf, municipio=municipio)
            return None

        if not index:
            return None

        query = _fold_accents(_slugify(nome))
        choices = [_fold_accents(slug) for slug, _ in index]
        result = process.extractOne(
            query,
            choices,
            scorer=fuzz.token_sort_ratio,
            score_cutoff=self._config.match_threshold,
            processor=rfuzz_utils.default_process,
        )
        if result is None:
            logger.info("md_no_match", uf=uf, municipio=municipio)
            return None

        _matched, _score, idx = result
        url = index[idx][1]
        logger.info("md_matched", uf=uf, municipio=municipio, score=_score)
        return url

    @retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def fetch_description(self, url: str) -> str | None:
        """GET a page URL and return its editorial description, or None (Redis-cached).

        Server-rendered HTML (POC §2: ``curl`` gets the content, no JS). The scraped
        text is TRANSIENT LLM context (POC §4) — cached only to avoid re-fetching the
        same page within TTL. Any fetch/parse miss returns None (never raises).
        """
        if not url:
            return None

        key = f"{MD_PAGE_CACHE_KEY_PREFIX}{url}"
        raw = _decode(self._redis.get(key))
        if raw:
            cached = json.loads(raw)
            return None if cached.get("__no_desc") else cached.get("description")

        await self._throttle()
        headers = {"User-Agent": self._config.user_agent}
        async with httpx.AsyncClient(**self._http_kwargs()) as hc:
            resp = await hc.get(url, headers=headers)
        resp.raise_for_status()

        description = _extract_description(resp.text)
        payload = {"description": description} if description else {"__no_desc": True}
        self._redis.setex(key, self._cache_ttl, json.dumps(payload))
        return description


# ---------------------------------------------------------------------------
# Structural type check
# ---------------------------------------------------------------------------


def _check_protocol_compliance() -> None:
    """Compile-time structural typing assertion (not called at runtime)."""
    from brave.clients.base import MelhoresDestinosClientProtocol

    _c: MelhoresDestinosClientProtocol = RealMelhoresDestinosClient  # type: ignore[assignment]  # noqa: F841
