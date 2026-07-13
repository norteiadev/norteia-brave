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

# Leaf state-name → UF util (pure dict + NFKD fold, no runtime deps). Client→domain
# import is allowed by the boundary guard (only kernel purity + cross-domain are barred).
from brave.domains.tripadvisor.uf_names import state_name_to_uf

if TYPE_CHECKING:
    from brave.config.settings import MelhoresDestinosConfig

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

# Redis keys: sitemap index (one long-lived list of (slug, url) pairs) + per-page cache
# + per-page breadcrumb-CHAIN cache. The breadcrumb entry stores the whole cleaned
# chain [Region, State, Place, Attraction] once, so the UF guard (State = chain[1]) and
# the distrito step (Place = chain[2]) share ONE page GET per url.
MD_SITEMAP_CACHE_KEY: str = "brave:md:sitemap"
MD_PAGE_CACHE_KEY_PREFIX: str = "brave:md:page:"
MD_BREADCRUMB_CACHE_KEY_PREFIX: str = "brave:md:breadcrumb:"

# Cost cap: max breadcrumb-State GETs the UF guard will make per find_attraction_url on
# the MISS path (a match returns on the first state hit). Bounds per-atrativo cost on a
# whole-Brazil sweep where most names have no MD page but several may clear the score.
_MAX_UF_CHECKS: int = 4

# The ``-l`` attraction page suffix (POC §2 URL grammar): <slug>-<cityCode>-<attrId>-l.html
_L_PAGE_RE = re.compile(r"/([^/]+?)-\d+-\d+-l\.html$")
# <loc>…</loc> entries in the flat sitemap.xml.
_LOC_RE = re.compile(r"<loc>\s*(.*?)\s*</loc>", re.IGNORECASE | re.DOTALL)

# Article-body container classes on a guia.melhoresdestinos.com.br ``-l`` page, in
# preference order. The editorial prose lives as ``<p>`` tags inside ``post-body``
# (== the ``conteudo-post`` region); ``texto`` is the intro block. Matched as a
# whitespace-delimited class token so multi-class attributes still hit.
_ARTICLE_BODY_CLASSES: tuple[str, ...] = ("post-body", "conteudo-post", "texto")
# <script>/<style> blocks are stripped before paragraph extraction (never prose).
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_PARAGRAPH_RE = re.compile(r"<p\b[^>]*>(.*?)</p>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
# A paragraph shorter than this is boilerplate (nav, share, captions) — dropped.
_MIN_PARAGRAPH_CHARS: int = 40
# Site self-promo / legal paragraphs the article container also carries (about-the-site
# blurb, copyright, privacy). Strong brand/legal markers an attraction description would
# never contain, so a case-insensitive substring hit safely drops the whole paragraph.
_BOILERPLATE_RE = re.compile(
    r"melhores destinos|copyright|pol[íi]tica de privacidade|passagens a[ée]reas"
    r"|equipe de jornalistas|guias gr[áa]tis",
    re.IGNORECASE,
)

# Breadcrumb block on a ``-l`` page: an ``id="breadcrumbs"`` container whose text nodes
# spell out Guia Melhores Destinos → Brasil → <Region> → <State> → <Place> → <Attraction>
# (POC: <Place> is the município OR distrito, flattened — the IBGE-distrito anchor).
# Non-greedy up to the first closing nav/ul/ol/div so the whole crumb list is captured.
_BREADCRUMB_BLOCK_RE = re.compile(
    r'id=["\']breadcrumbs["\'].*?</(?:nav|ul|ol|div)>',
    re.IGNORECASE | re.DOTALL,
)
# One text node between tags (separators like ">"/"»" are dropped downstream — no word char).
_BREADCRUMB_ITEM_RE = re.compile(r">([^<>]+)<")
# The two fixed prefixes every crumb starts with (dropped before indexing the chain).
_BREADCRUMB_PREFIXES: frozenset[str] = frozenset({"Guia Melhores Destinos", "Brasil"})


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


def _clean_paragraph(raw: str) -> str:
    """Strip tags, unescape entities, collapse whitespace for one ``<p>`` inner HTML."""
    return re.sub(r"\s+", " ", html.unescape(_TAG_RE.sub("", raw))).strip()


def _extract_article_body(html_text: str) -> str | None:
    """Recover the full editorial article body (the ``<p>`` prose) from a page.

    The ``og:description`` meta is a short, site-truncated social blurb (~160 chars,
    often cut mid-sentence); the real editorial content is the ``<p>`` paragraphs
    inside the article container (``post-body`` / ``conteudo-post``). This locates the
    first such container, strips ``<script>``/``<style>``, collects its paragraph text
    (dropping short fragments AND the site's about/copyright/privacy self-promo
    paragraphs via _BOILERPLATE_RE), and joins with blank lines. Returns None when no
    container/paragraphs are found. ONLY stdlib re — no lxml/bs4 (house style).
    """
    for cls in _ARTICLE_BODY_CLASSES:
        # Match the class as a whitespace-delimited token inside the attribute so a
        # multi-class value (e.g. class="largura_padrao conteudo-post") still matches.
        m = re.search(
            r'<div[^>]*\bclass="[^"]*\b' + re.escape(cls) + r'\b[^"]*"[^>]*>',
            html_text,
            re.IGNORECASE,
        )
        if m is None:
            continue
        # Bounded forward window from the container start (the article body is well
        # under this; avoids walking the whole page and never over-matches into the
        # footer's own paragraphs meaningfully once short ones are filtered).
        window = html_text[m.start() : m.start() + 40_000]
        window = _SCRIPT_STYLE_RE.sub("", window)
        paras = [
            p for p in (_clean_paragraph(raw) for raw in _PARAGRAPH_RE.findall(window))
            if len(p) >= _MIN_PARAGRAPH_CHARS and not _BOILERPLATE_RE.search(p)
        ]
        if paras:
            return "\n\n".join(paras)
    return None


def _extract_description(html_text: str) -> str | None:
    """Recover the editorial description from a page using ONLY stdlib re + json.

    Body-first (house style — no lxml/bs4): prefer the FULL article body (the ``<p>``
    prose inside ``post-body``/``conteudo-post``) so the LLM rewrite sees the real
    facts, not just the ~160-char ``og:description`` social blurb the site truncates
    mid-sentence. Falls back to the JSON-LD ``"description"`` / ``og:description`` meta
    (longest) only when no article body is found. Returns None when nothing usable is
    present. Never raises.
    """
    if not html_text:
        return None

    body = _extract_article_body(html_text)
    if body:
        return body

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


def _extract_breadcrumb_chain(html_text: str) -> list[str] | None:
    """Recover the cleaned breadcrumb chain from a ``-l`` page (ONLY stdlib re).

    The ``id="breadcrumbs"`` container spells out Guia Melhores Destinos → Brasil →
    <Region> → <State> → <Place> → <Attraction>. Locates the block, collects its text
    nodes, unescapes entities, drops empties, pure separators (no word char), and the
    two fixed prefixes, then returns the remaining [Region, State, Place, Attraction]
    chain. Returns None on no breadcrumb or a chain shorter than 2 (so index 1 — the
    State — exists). Never raises. Mirrors _extract_article_body style.
    """
    if not html_text:
        return None

    m = _BREADCRUMB_BLOCK_RE.search(html_text)
    if m is None:
        return None

    chain = [
        item
        for item in (html.unescape(t).strip() for t in _BREADCRUMB_ITEM_RE.findall(m.group(0)))
        if item and re.search(r"\w", item) and item not in _BREADCRUMB_PREFIXES
    ]
    # Need [Region, State] at minimum so index 1 (the State, for the UF guard) exists.
    if len(chain) < 2:
        return None
    return chain


def _extract_breadcrumb_place(html_text: str) -> str | None:
    """The breadcrumb ``<Place>`` level (chain index 2) — the IBGE-distrito anchor.

    POC: <Place> is the município OR distrito, flattened — e.g. "Arraial d'Ajuda" as a
    peer of Porto Seguro under Bahia. Returns None on no breadcrumb, a chain shorter
    than 3, or an empty place. Never raises.
    """
    chain = _extract_breadcrumb_chain(html_text)
    if chain is None or len(chain) < 3:
        return None
    return chain[2] or None


def _extract_breadcrumb_state(html_text: str) -> str | None:
    """The breadcrumb ``<State>`` level (chain index 1) — the UF-guard anchor.

    A bare Portuguese state name (e.g. "Espírito Santo") the caller maps to a 2-letter
    UF via ``state_name_to_uf`` to reject cross-state matches. Returns None on no
    breadcrumb, a chain shorter than 2, or an empty state. Never raises.
    """
    chain = _extract_breadcrumb_chain(html_text)
    if chain is None or len(chain) < 2:
        return None
    return chain[1] or None


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

        The URL slug carries the attraction name only, so matching is fuzzy on the
        slugified ``nome`` against the sitemap slugs — ``rapidfuzz.fuzz.WRatio``
        (weighted partial/token_set/token_sort blend; tolerates extra/missing tokens
        like "Convento **Nossa Senhora da** Penha"), accent-folded, cutoff from config.

        Because matching is BLIND/nationwide over ~4522 slugs, a loosened cutoff would
        risk grabbing a same-name attraction in another state. Guard against that: take
        the top-K candidates ≥ cutoff and return the first whose page breadcrumb State
        maps to the requested ``uf`` (``state_name_to_uf``). A candidate that clears the
        score but sits in the wrong state is logged ``md_uf_rejected`` and skipped. When
        ``uf`` is empty/unknown the top candidate is accepted unverified. Every miss logs
        the best slug + score (diagnostic: near-threshold vs genuine MD coverage gap).
        A miss returns None → the agent keeps the floor.
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
        # No score_cutoff here — we want the best candidates WITH scores so a miss can
        # log how close it was (threshold-tuning vs coverage-gap signal).
        candidates = process.extract(
            query,
            choices,
            scorer=fuzz.WRatio,
            limit=8,
            processor=rfuzz_utils.default_process,
        )

        threshold = self._config.match_threshold
        if not candidates or candidates[0][1] < threshold:
            best = candidates[0] if candidates else None
            logger.info(
                "md_no_match",
                uf=uf,
                municipio=municipio,
                best_slug=(index[best[2]][0] if best else None),
                best_score=(round(best[1], 1) if best else None),
                top3=[(index[c[2]][0], round(c[1], 1)) for c in candidates[:3]],
            )
            return None

        uf_norm = (uf or "").strip().upper()
        uf_checks = 0
        for _choice, _score, _idx in candidates:
            if _score < threshold:
                break
            url = index[_idx][1]
            # Empty/unknown UF → cannot validate; accept the top qualifying candidate.
            if not uf_norm:
                logger.info(
                    "md_matched", uf=uf, municipio=municipio, score=round(_score, 1),
                    uf_unverified=True,
                )
                return url
            # Cost cap: a match returns on the FIRST state hit, but a miss would otherwise
            # fetch a breadcrumb per qualifying candidate. On a whole-Brazil sweep most
            # atrativos have no MD page, so bound the per-miss GETs (the real match, if any,
            # is near the top by name similarity).
            if uf_checks >= _MAX_UF_CHECKS:
                logger.info(
                    "md_uf_check_capped", uf=uf, municipio=municipio, checked=uf_checks
                )
                break
            uf_checks += 1  # noqa: SIM113 — conditional counter, not a loop index (enumerate would miscount)
            try:
                state = await self.fetch_breadcrumb_state(url)
            except Exception:  # noqa: BLE001 — a breadcrumb miss just skips this candidate
                state = None
            candidate_uf = state_name_to_uf(state) if state else None
            if candidate_uf == uf_norm:
                logger.info("md_matched", uf=uf, municipio=municipio, score=round(_score, 1))
                return url
            logger.info(
                "md_uf_rejected",
                uf=uf,
                municipio=municipio,
                score=round(_score, 1),
                slug=index[_idx][0],
                state=state,
                candidate_uf=candidate_uf,
            )

        # Candidates cleared the score but none matched the UF → keep the floor.
        logger.info(
            "md_no_match",
            uf=uf,
            municipio=municipio,
            reason="uf_unmatched",
            best_slug=index[candidates[0][2]][0],
            best_score=round(candidates[0][1], 1),
        )
        return None

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

    @retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def _fetch_breadcrumb_chain(self, url: str) -> list[str] | None:
        """GET a page URL and return its cleaned breadcrumb CHAIN, or None (Redis-cached).

        One GET per url serves BOTH the UF guard (State = chain[1]) and the distrito step
        (Place = chain[2]). Server-rendered HTML (same plumbing as fetch_description).
        Caches the extracted chain (JSON, not raw HTML) under ``brave:md:breadcrumb:<url>``
        as ``{"chain": [...]}`` (or ``{"__no_chain": True}``). Legacy ``{"place": …}`` /
        ``{"__no_place": …}`` entries lack a "chain"/"__no_chain" key → treated as a cache
        miss and re-fetched. Any fetch/parse miss returns None (never raises here).
        """
        if not url:
            return None

        key = f"{MD_BREADCRUMB_CACHE_KEY_PREFIX}{url}"
        raw = _decode(self._redis.get(key))
        if raw:
            cached = json.loads(raw)
            if "chain" in cached:
                return cached["chain"]
            if cached.get("__no_chain"):
                return None
            # else: legacy place-only cache entry → fall through and re-fetch as a chain.

        await self._throttle()
        headers = {"User-Agent": self._config.user_agent}
        async with httpx.AsyncClient(**self._http_kwargs()) as hc:
            resp = await hc.get(url, headers=headers)
        resp.raise_for_status()

        chain = _extract_breadcrumb_chain(resp.text)
        payload = {"chain": chain} if chain else {"__no_chain": True}
        self._redis.setex(key, self._cache_ttl, json.dumps(payload))
        return chain

    async def fetch_breadcrumb_place(self, url: str) -> str | None:
        """The breadcrumb ``<Place>`` level (chain index 2), or None (Redis-cached).

        The IBGE-distrito anchor — the caller crosses it against ibge_distritos.csv scoped
        to the parent município. Shares the one cached breadcrumb chain per url.
        """
        chain = await self._fetch_breadcrumb_chain(url)
        if chain and len(chain) >= 3:
            return chain[2] or None
        return None

    async def fetch_breadcrumb_state(self, url: str) -> str | None:
        """The breadcrumb ``<State>`` level (chain index 1), or None (Redis-cached).

        The UF-guard anchor — a bare Portuguese state name the matcher maps via
        ``state_name_to_uf``. Shares the one cached breadcrumb chain per url.
        """
        chain = await self._fetch_breadcrumb_chain(url)
        if chain and len(chain) >= 2:
            return chain[1] or None
        return None


# ---------------------------------------------------------------------------
# Structural type check
# ---------------------------------------------------------------------------


def _check_protocol_compliance() -> None:
    """Compile-time structural typing assertion (not called at runtime)."""
    from brave.clients.base import MelhoresDestinosClientProtocol

    _c: MelhoresDestinosClientProtocol = RealMelhoresDestinosClient  # type: ignore[assignment]  # noqa: F841
