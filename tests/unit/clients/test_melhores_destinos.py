"""Unit tests for RealMelhoresDestinosClient / NullMelhoresDestinosClient.

100% offline (respx mocks httpx, fakeredis mocks Redis). Template: test_nominatim.py.

Covers:
  - Guard raises RuntimeError when run_real_externals=False
  - find_attraction_url fuzzy-matches the atrativo name → the -l.html page URL
  - find_attraction_url miss → None (below threshold)
  - Sitemap is fetched once then Redis-cached (2nd find → no 2nd sitemap GET)
  - fetch_description extracts the editorial description (og:description / JSON-LD)
  - fetch_description cache hit → no 2nd page GET
  - NullMelhoresDestinosClient returns None for both methods, no network
"""

from __future__ import annotations

import fakeredis
import httpx
import pytest
import respx

from brave.config.settings import MelhoresDestinosConfig

SITEMAP_URL = "https://guia.melhoresdestinos.com.br/sitemap.xml"
PRAIA_URL = "https://guia.melhoresdestinos.com.br/praia-do-forte-54-249-l.html"
MERCADO_URL = "https://guia.melhoresdestinos.com.br/mercado-modelo-12-88-l.html"

SITEMAP_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>{PRAIA_URL}</loc></url>
  <url><loc>{MERCADO_URL}</loc></url>
  <url><loc>https://guia.melhoresdestinos.com.br/nao-e-atrativo.html</loc></url>
</urlset>
"""

PAGE_HTML = """<!doctype html><html><head>
<meta property="og:title" content="Praia do Forte" />
<meta property="og:description" content="Vila de pescadores na Costa dos Coqueiros, a Praia do Forte encanta com piscinas naturais e o Projeto Tamar." />
<script type="application/ld+json">{"@type":"Article","description":"Descrição curta JSON-LD."}</script>
</head><body><h1>Praia do Forte</h1></body></html>
"""


def _cfg(**kw) -> MelhoresDestinosConfig:
    kw.setdefault("throttle_seconds", 0.0)  # keep the offline suite fast
    return MelhoresDestinosConfig(**kw)


def test_guard_raises() -> None:
    """RealMelhoresDestinosClient raises RuntimeError when run_real_externals=False."""
    from brave.clients.melhores_destinos import RealMelhoresDestinosClient

    redis = fakeredis.FakeRedis()
    with pytest.raises(RuntimeError, match="run_real_externals=False"):
        RealMelhoresDestinosClient(config=_cfg(), redis=redis)


async def test_find_attraction_url_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    """A close name fuzzy-matches its -l.html page slug and returns the URL."""
    monkeypatch.setenv("RUN_REAL_EXTERNALS", "true")
    from brave.clients.melhores_destinos import RealMelhoresDestinosClient

    redis = fakeredis.FakeRedis()
    with respx.mock:
        respx.get(SITEMAP_URL).mock(return_value=httpx.Response(200, text=SITEMAP_XML))
        # UF guard: find_attraction_url now GETs the candidate page to read its
        # breadcrumb State — Bahia → BA matches the requested uf, so it is accepted.
        respx.get(PRAIA_URL).mock(
            return_value=httpx.Response(200, text=BREADCRUMB_DISTRITO_HTML)
        )
        client = RealMelhoresDestinosClient(config=_cfg(), redis=redis)
        url = await client.find_attraction_url("Praia do Forte", "Mata de São João", "BA")

    assert url == PRAIA_URL


async def test_find_attraction_url_miss_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A name with no close slug → None (below the fuzzy threshold)."""
    monkeypatch.setenv("RUN_REAL_EXTERNALS", "true")
    from brave.clients.melhores_destinos import RealMelhoresDestinosClient

    redis = fakeredis.FakeRedis()
    with respx.mock:
        respx.get(SITEMAP_URL).mock(return_value=httpx.Response(200, text=SITEMAP_XML))
        client = RealMelhoresDestinosClient(config=_cfg(), redis=redis)
        url = await client.find_attraction_url(
            "Cachoeira Totalmente Diferente XYZ", "Lugar", "BA"
        )

    assert url is None


async def test_sitemap_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """The sitemap is fetched once then served from Redis — 2nd find → no 2nd GET."""
    monkeypatch.setenv("RUN_REAL_EXTERNALS", "true")
    from brave.clients.melhores_destinos import RealMelhoresDestinosClient

    redis = fakeredis.FakeRedis()
    with respx.mock:
        route = respx.get(SITEMAP_URL).mock(
            return_value=httpx.Response(200, text=SITEMAP_XML)
        )
        # Each find now GETs its candidate page for the UF-guard breadcrumb (Bahia → BA).
        respx.get(PRAIA_URL).mock(
            return_value=httpx.Response(200, text=BREADCRUMB_DISTRITO_HTML)
        )
        respx.get(MERCADO_URL).mock(
            return_value=httpx.Response(200, text=BREADCRUMB_DISTRITO_HTML)
        )
        client = RealMelhoresDestinosClient(config=_cfg(), redis=redis)
        await client.find_attraction_url("Praia do Forte", "X", "BA")
        await client.find_attraction_url("Mercado Modelo", "Salvador", "BA")

    assert route.call_count == 1, "sitemap must be fetched once, then cached"


async def test_fetch_description_extracts_editorial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """fetch_description returns the longest editorial description (og:description)."""
    monkeypatch.setenv("RUN_REAL_EXTERNALS", "true")
    from brave.clients.melhores_destinos import RealMelhoresDestinosClient

    redis = fakeredis.FakeRedis()
    with respx.mock:
        respx.get(PRAIA_URL).mock(return_value=httpx.Response(200, text=PAGE_HTML))
        client = RealMelhoresDestinosClient(config=_cfg(), redis=redis)
        desc = await client.fetch_description(PRAIA_URL)

    assert desc is not None
    assert "Costa dos Coqueiros" in desc
    assert "Projeto Tamar" in desc  # og:description (longer) wins over the JSON-LD blurb


ARTICLE_HTML = (
    "<!doctype html><html><head>"
    '<meta property="og:description" content="A Igreja começou a ser construida em 1549 e recebeu esse nome. Hoje em" />'
    "</head><body>"
    '<div class="largura_padrao conteudo-post">'
    '<div class="post-body">'
    "<p>A Igreja Nossa Senhora d'Ajuda foi construida em homenagem a chegada da imagem "
    "trazida pelos jesuitas portugueses, e a igreja original data do seculo XVI.</p>"
    '<p class="share">x</p>'  # short boilerplate — dropped
    "<p>O local atrai grande numero de romeiros devido a tradicao catolica da agua "
    "milagrosa que brota de uma fonte proxima ao templo.</p>"
    # Site self-promo + copyright paragraphs the container also carries — must be dropped.
    "<p>O Guia Melhores Destinos foi lancado em 2012 e e um dos sites mais completos "
    "sobre turismo, com guias gratis produzidos pela nossa equipe de jornalistas.</p>"
    "<p>Copyright 2008 - 2026 Guia Melhores Destinos Politica de Privacidade.</p>"
    "</div></div></body></html>"
)


async def test_fetch_description_prefers_full_article_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The full article body (<p> prose) is returned, NOT the truncated og:description."""
    monkeypatch.setenv("RUN_REAL_EXTERNALS", "true")
    from brave.clients.melhores_destinos import RealMelhoresDestinosClient

    redis = fakeredis.FakeRedis()
    with respx.mock:
        respx.get(PRAIA_URL).mock(return_value=httpx.Response(200, text=ARTICLE_HTML))
        client = RealMelhoresDestinosClient(config=_cfg(), redis=redis)
        desc = await client.fetch_description(PRAIA_URL)

    assert desc is not None
    # Both real paragraphs present, joined; the short share <p> is dropped.
    assert "jesuitas portugueses" in desc
    assert "romeiros" in desc
    assert "\n\n" in desc  # paragraphs joined
    assert "Hoje em" not in desc  # did NOT fall back to the truncated og:description
    # Site self-promo + copyright + short boilerplate paragraphs are all excluded.
    assert "Melhores Destinos" not in desc
    assert "Copyright" not in desc
    assert "Politica de Privacidade" not in desc


async def test_fetch_description_apostrophe_not_truncated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A double-quoted og:description with an inner apostrophe is not truncated."""
    monkeypatch.setenv("RUN_REAL_EXTERNALS", "true")
    from brave.clients.melhores_destinos import RealMelhoresDestinosClient

    page = (
        '<html><head><meta property="og:description" '
        'content="Em Arraial d\'Ajuda, a vila encanta com praias e falesias coloridas." />'
        "</head></html>"
    )
    redis = fakeredis.FakeRedis()
    with respx.mock:
        respx.get(PRAIA_URL).mock(return_value=httpx.Response(200, text=page))
        client = RealMelhoresDestinosClient(config=_cfg(), redis=redis)
        desc = await client.fetch_description(PRAIA_URL)

    assert desc is not None
    assert desc.endswith("coloridas.")  # not cut at the apostrophe in "d'Ajuda"
    assert "falesias" in desc


async def test_fetch_description_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """Second fetch_description for the same URL hits Redis — respx count == 1."""
    monkeypatch.setenv("RUN_REAL_EXTERNALS", "true")
    from brave.clients.melhores_destinos import RealMelhoresDestinosClient

    redis = fakeredis.FakeRedis()
    with respx.mock:
        route = respx.get(PRAIA_URL).mock(return_value=httpx.Response(200, text=PAGE_HTML))
        client = RealMelhoresDestinosClient(config=_cfg(), redis=redis)
        await client.fetch_description(PRAIA_URL)
        await client.fetch_description(PRAIA_URL)

    assert route.call_count == 1, "second fetch must hit the page cache"


async def test_fetch_description_no_description_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A page with no usable description → None (graceful, never raises)."""
    monkeypatch.setenv("RUN_REAL_EXTERNALS", "true")
    from brave.clients.melhores_destinos import RealMelhoresDestinosClient

    redis = fakeredis.FakeRedis()
    with respx.mock:
        respx.get(PRAIA_URL).mock(
            return_value=httpx.Response(200, text="<html><body>no meta</body></html>")
        )
        client = RealMelhoresDestinosClient(config=_cfg(), redis=redis)
        desc = await client.fetch_description(PRAIA_URL)

    assert desc is None


# ---------------------------------------------------------------------------
# Breadcrumb <Place> extraction (IBGE-distrito anchor)
# ---------------------------------------------------------------------------

# Guia Melhores Destinos → Brasil → <Region> → <State> → <Place> → <Attraction>.
# Here <Place> is a distrito (Arraial d'Ajuda) sitting as a peer under Bahia (POC golden).
BREADCRUMB_DISTRITO_HTML = (
    "<!doctype html><html><body>"
    '<nav id="breadcrumbs">'
    "<a>Guia Melhores Destinos</a><a>Brasil</a><a>Nordeste</a><a>Bahia</a>"
    "<a>Arraial d'Ajuda</a><span>Igreja Nossa Senhora d'Ajuda</span>"
    "</nav></body></html>"
)
# <Place> is a município (Belo Horizonte) — same index-2 position in the chain.
BREADCRUMB_MUNICIPIO_HTML = (
    "<!doctype html><html><body>"
    '<nav id="breadcrumbs">'
    "<a>Guia Melhores Destinos</a><a>Brasil</a><a>Sudeste</a><a>Minas Gerais</a>"
    "<a>Belo Horizonte</a><span>Igreja de São Francisco de Assis</span>"
    "</nav></body></html>"
)


def test_extract_breadcrumb_place_distrito() -> None:
    """A distrito <Place> (Arraial d'Ajuda, peer of Porto Seguro) is returned."""
    from brave.clients.melhores_destinos import _extract_breadcrumb_place

    assert _extract_breadcrumb_place(BREADCRUMB_DISTRITO_HTML) == "Arraial d'Ajuda"


def test_extract_breadcrumb_place_municipio() -> None:
    """A município-only <Place> (Belo Horizonte) is returned (same chain index 2)."""
    from brave.clients.melhores_destinos import _extract_breadcrumb_place

    assert _extract_breadcrumb_place(BREADCRUMB_MUNICIPIO_HTML) == "Belo Horizonte"


def test_extract_breadcrumb_place_no_breadcrumb_returns_none() -> None:
    """HTML without an id="breadcrumbs" block → None (no place)."""
    from brave.clients.melhores_destinos import _extract_breadcrumb_place

    assert _extract_breadcrumb_place("<html><body><p>sem trilha</p></body></html>") is None


async def test_fetch_breadcrumb_place_extracts_and_caches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """fetch_breadcrumb_place returns the <Place> and caches it (2nd call → no 2nd GET)."""
    monkeypatch.setenv("RUN_REAL_EXTERNALS", "true")
    from brave.clients.melhores_destinos import RealMelhoresDestinosClient

    redis = fakeredis.FakeRedis()
    with respx.mock:
        route = respx.get(PRAIA_URL).mock(
            return_value=httpx.Response(200, text=BREADCRUMB_DISTRITO_HTML)
        )
        client = RealMelhoresDestinosClient(config=_cfg(), redis=redis)
        place = await client.fetch_breadcrumb_place(PRAIA_URL)
        place2 = await client.fetch_breadcrumb_place(PRAIA_URL)

    assert place == "Arraial d'Ajuda"
    assert place2 == "Arraial d'Ajuda"
    assert route.call_count == 1, "second breadcrumb fetch must hit the cache"


# ---------------------------------------------------------------------------
# Breadcrumb State (chain index 1) + UF guard
# ---------------------------------------------------------------------------

# A -l page whose slug drops the "Nossa Senhora da" tokens — the extra-token class that
# token_sort_ratio@88 missed but WRatio@82 catches. Breadcrumb State = Espírito Santo.
CONVENTO_URL = "https://guia.melhoresdestinos.com.br/convento-da-penha-45-901-l.html"
CONVENTO_SITEMAP_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>{CONVENTO_URL}</loc></url>
  <url><loc>{MERCADO_URL}</loc></url>
</urlset>
"""
BREADCRUMB_ES_HTML = (
    "<!doctype html><html><body>"
    '<nav id="breadcrumbs">'
    "<a>Guia Melhores Destinos</a><a>Brasil</a><a>Sudeste</a><a>Espírito Santo</a>"
    "<a>Vila Velha</a><span>Convento da Penha</span>"
    "</nav></body></html>"
)
# Same-name candidate sitting in the WRONG state (São Paulo), for the reject test.
BREADCRUMB_SP_HTML = (
    "<!doctype html><html><body>"
    '<nav id="breadcrumbs">'
    "<a>Guia Melhores Destinos</a><a>Brasil</a><a>Sudeste</a><a>São Paulo</a>"
    "<a>Guarujá</a><span>Praia do Forte</span>"
    "</nav></body></html>"
)


def test_extract_breadcrumb_state_returns_state_level() -> None:
    """_extract_breadcrumb_state returns chain index 1 (the State name)."""
    from brave.clients.melhores_destinos import _extract_breadcrumb_state

    assert _extract_breadcrumb_state(BREADCRUMB_DISTRITO_HTML) == "Bahia"
    assert _extract_breadcrumb_state(BREADCRUMB_MUNICIPIO_HTML) == "Minas Gerais"
    assert _extract_breadcrumb_state("<html><body><p>sem trilha</p></body></html>") is None


async def test_fetch_breadcrumb_state_extracts_and_caches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """fetch_breadcrumb_state returns the <State> and shares the one cached chain."""
    monkeypatch.setenv("RUN_REAL_EXTERNALS", "true")
    from brave.clients.melhores_destinos import RealMelhoresDestinosClient

    redis = fakeredis.FakeRedis()
    with respx.mock:
        route = respx.get(PRAIA_URL).mock(
            return_value=httpx.Response(200, text=BREADCRUMB_DISTRITO_HTML)
        )
        client = RealMelhoresDestinosClient(config=_cfg(), redis=redis)
        state = await client.fetch_breadcrumb_state(PRAIA_URL)
        # Second call for the PLACE reads the SAME cached chain — no 2nd GET.
        place = await client.fetch_breadcrumb_place(PRAIA_URL)

    assert state == "Bahia"
    assert place == "Arraial d'Ajuda"
    assert route.call_count == 1, "State + Place share one cached breadcrumb chain"


async def test_find_attraction_url_wratio_matches_extra_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WRatio matches a name with extra tokens ('Convento Nossa Senhora da Penha') to a
    shorter slug ('convento-da-penha') that token_sort_ratio@88 would have missed; the
    breadcrumb State (Espírito Santo → ES) matches uf=ES so it is accepted."""
    monkeypatch.setenv("RUN_REAL_EXTERNALS", "true")
    from brave.clients.melhores_destinos import RealMelhoresDestinosClient

    redis = fakeredis.FakeRedis()
    with respx.mock:
        respx.get(SITEMAP_URL).mock(
            return_value=httpx.Response(200, text=CONVENTO_SITEMAP_XML)
        )
        respx.get(CONVENTO_URL).mock(
            return_value=httpx.Response(200, text=BREADCRUMB_ES_HTML)
        )
        client = RealMelhoresDestinosClient(config=_cfg(), redis=redis)
        url = await client.find_attraction_url(
            "Convento Nossa Senhora da Penha", "Vila Velha", "ES"
        )

    assert url == CONVENTO_URL


async def test_find_attraction_url_uf_guard_rejects_wrong_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A high-score candidate whose breadcrumb State ≠ the atrativo UF is rejected —
    prevents a same-name attraction in another state feeding a wrong description."""
    monkeypatch.setenv("RUN_REAL_EXTERNALS", "true")
    from brave.clients.melhores_destinos import RealMelhoresDestinosClient

    redis = fakeredis.FakeRedis()
    with respx.mock:
        respx.get(SITEMAP_URL).mock(return_value=httpx.Response(200, text=SITEMAP_XML))
        # The only ≥threshold candidate (praia-do-forte) sits in São Paulo, but we want
        # BA. mercado-modelo scores below threshold so it is never fetched (not mocked).
        respx.get(PRAIA_URL).mock(return_value=httpx.Response(200, text=BREADCRUMB_SP_HTML))
        client = RealMelhoresDestinosClient(config=_cfg(), redis=redis)
        url = await client.find_attraction_url("Praia do Forte", "Mata de São João", "BA")

    assert url is None, "cross-state candidate must be rejected (kept floor)"


async def test_find_attraction_url_empty_uf_accepts_top_unverified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty uf → state cannot be validated → accept the top qualifying candidate with NO
    page GET (uf_unverified). Only the sitemap is fetched (no breadcrumb route mocked)."""
    monkeypatch.setenv("RUN_REAL_EXTERNALS", "true")
    from brave.clients.melhores_destinos import RealMelhoresDestinosClient

    redis = fakeredis.FakeRedis()
    with respx.mock:
        respx.get(SITEMAP_URL).mock(return_value=httpx.Response(200, text=SITEMAP_XML))
        client = RealMelhoresDestinosClient(config=_cfg(), redis=redis)
        url = await client.find_attraction_url("Praia do Forte", "X", "")

    # No breadcrumb route mocked: if a page GET were issued respx would raise, so this
    # both asserts the URL AND that the empty-uf path skips the UF-guard fetch.
    assert url == PRAIA_URL


async def test_fetch_breadcrumb_place_migrates_legacy_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pre-existing legacy {"place": …} breadcrumb cache entry (no "chain" key) is
    treated as a miss and re-fetched as the full chain — 30-day TTL means legacy entries
    coexist under the same key after deploy; trusting one would drop the State/UF guard."""
    monkeypatch.setenv("RUN_REAL_EXTERNALS", "true")
    import json

    from brave.clients.melhores_destinos import (
        MD_BREADCRUMB_CACHE_KEY_PREFIX,
        RealMelhoresDestinosClient,
    )

    redis = fakeredis.FakeRedis()
    redis.set(
        f"{MD_BREADCRUMB_CACHE_KEY_PREFIX}{PRAIA_URL}",
        json.dumps({"place": "Stale Legacy Value"}),
    )
    with respx.mock:
        route = respx.get(PRAIA_URL).mock(
            return_value=httpx.Response(200, text=BREADCRUMB_DISTRITO_HTML)
        )
        client = RealMelhoresDestinosClient(config=_cfg(), redis=redis)
        place = await client.fetch_breadcrumb_place(PRAIA_URL)
        state = await client.fetch_breadcrumb_state(PRAIA_URL)

    assert place == "Arraial d'Ajuda", "legacy entry must be re-fetched, not trusted"
    assert state == "Bahia"
    assert route.call_count == 1, "one re-fetch, then the migrated chain is cached"


async def test_find_attraction_url_miss_logs_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A below-threshold miss logs the best candidate slug + score (the threshold-vs-
    coverage signal that justified dropping score_cutoff)."""
    monkeypatch.setenv("RUN_REAL_EXTERNALS", "true")
    from structlog.testing import capture_logs

    from brave.clients.melhores_destinos import RealMelhoresDestinosClient

    redis = fakeredis.FakeRedis()
    with respx.mock, capture_logs() as logs:
        respx.get(SITEMAP_URL).mock(return_value=httpx.Response(200, text=SITEMAP_XML))
        client = RealMelhoresDestinosClient(config=_cfg(), redis=redis)
        url = await client.find_attraction_url("Zzz Totalmente Diferente Xyz", "L", "BA")

    assert url is None
    miss = [e for e in logs if e.get("event") == "md_no_match"]
    assert miss, "a below-threshold miss must log md_no_match"
    assert miss[0].get("best_slug") is not None
    assert miss[0].get("best_score") is not None


# ---------------------------------------------------------------------------
# Null client
# ---------------------------------------------------------------------------


async def test_null_client_returns_none() -> None:
    """NullMelhoresDestinosClient returns None for every method (no network)."""
    from brave.clients.null_melhores_destinos import NullMelhoresDestinosClient

    client = NullMelhoresDestinosClient()
    assert await client.find_attraction_url("Praia do Forte", "X", "BA") is None
    assert await client.fetch_description(PRAIA_URL) is None
    assert await client.fetch_breadcrumb_place(PRAIA_URL) is None


def test_protocol_compliance() -> None:
    """Structural typing assertions must not raise."""
    from brave.clients.melhores_destinos import _check_protocol_compliance as real_chk
    from brave.clients.null_melhores_destinos import _check_protocol_compliance as null_chk
    from tests.fakes.fake_melhores_destinos import _check_protocol_compliance as fake_chk

    real_chk()
    null_chk()
    fake_chk()
