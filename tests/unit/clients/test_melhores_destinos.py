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
# Null client
# ---------------------------------------------------------------------------


async def test_null_client_returns_none() -> None:
    """NullMelhoresDestinosClient returns None for both methods (no network)."""
    from brave.clients.null_melhores_destinos import NullMelhoresDestinosClient

    client = NullMelhoresDestinosClient()
    assert await client.find_attraction_url("Praia do Forte", "X", "BA") is None
    assert await client.fetch_description(PRAIA_URL) is None


def test_protocol_compliance() -> None:
    """Structural typing assertions must not raise."""
    from brave.clients.melhores_destinos import _check_protocol_compliance as real_chk
    from brave.clients.null_melhores_destinos import _check_protocol_compliance as null_chk
    from tests.fakes.fake_melhores_destinos import _check_protocol_compliance as fake_chk

    real_chk()
    null_chk()
    fake_chk()
