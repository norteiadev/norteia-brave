"""Editorial crawler: HTML / KML parsing on real fixtures, offline (no fetcher involved)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from brave.domains.editorial.crawler import (
    UF_SLUGS,
    category_url,
    content_hash,
    extract_body,
    extract_kml_mids,
    extract_modified_time,
    extract_published_time,
    kml_url,
    parse_kml,
    post_links,
)

FIX = Path(__file__).resolve().parents[3] / "fixtures" / "editorial"


def _html(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def test_body_is_the_post_without_affiliate_blocks_or_comments() -> None:
    body = extract_body(_html("anchieta-es.html"))
    assert "A cidade de Anchieta se localiza no sul de Espírito Santo" in body
    assert "Mercado dos Peixes" in body
    # affiliate blocks (rel=sponsored) are dropped whole, with their prose
    assert "DISCOVER CARS" not in body
    assert "Compare os preços de várias locadoras" not in body
    assert "CIVITATIS" not in body
    assert "SEGURO VIAGEM" not in body
    assert "Hotel Ponta das Rochas" not in body
    # the reader comments live outside div.td-post-content
    assert 'id="comments"' not in body and "Responder para" not in body
    # headings survive as markdown-ish markers for the LLM
    assert "\n## " in body or body.startswith("## ")


def test_body_ignores_leia_tambem_blocks() -> None:
    html = (
        '<div class="td-post-content"><p>Conteúdo útil sobre a praia do Morro.</p>'
        "<p>Leia também: dez praias para conhecer no ES</p></div>"
    )
    body = extract_body(html)
    assert "Conteúdo útil" in body
    assert "Leia também" not in body


def test_body_picks_the_largest_matching_container() -> None:
    html = (
        '<div class="td-post-content">curto</div>'
        '<div class="td-post-content"><p>' + "texto longo " * 20 + "</p></div>"
    )
    assert "texto longo" in extract_body(html)
    assert "curto" not in extract_body(html)


def test_a_sponsored_link_straight_under_the_container_does_not_wipe_the_post() -> None:
    html = (
        '<div class="td-post-content"><p>Texto do post.</p>'
        '<a rel="sponsored" href="https://x.example">Reserve já</a></div>'
    )
    body = extract_body(html)
    assert "Texto do post." in body
    assert "Reserve já" not in body


def test_published_time_is_the_article_meta_date() -> None:
    assert extract_published_time(_html("anchieta-es.html")) == date(2020, 2, 17)
    assert extract_published_time(_html("pedra-do-frade-e-a-freira.html")) == date(2020, 1, 27)
    assert extract_published_time("<html></html>") is None


def test_kml_mids_come_from_the_embedded_maps_deduplicated() -> None:
    assert extract_kml_mids(_html("anchieta-es.html")) == ["1o_KkTvTTcAWuokGAkchwX9oj9_94T0U"]
    assert extract_kml_mids(_html("pedra-do-frade-e-a-freira.html")) == [
        "1Lo1mMVRbnuhO4a9vh1oVeQnPe4LZfUsL",  # page order, not alphabetical
        "1BXqw0K8zTGNjCpGDiZh43nnQadEXTII",
    ]
    assert kml_url("abc") == "https://www.google.com/maps/d/kml?mid=abc&forcekml=1"


def test_parse_kml_reads_name_and_lon_lat_order() -> None:
    points = parse_kml((FIX / "anchieta-es.kml").read_text(encoding="utf-8"))
    assert len(points) == 22
    first = points[0]
    assert first["name"] == "Santuário de São José de Anchieta"  # trailing newline stripped
    assert first["lat"] == -20.8031251 and first["lon"] == -40.6524609  # KML is "lon,lat,0"


def test_parse_kml_skips_placemarks_without_a_point_and_bad_xml() -> None:
    kml = (
        '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
        "<Placemark><name>Trilha</name><LineString><coordinates>1,2,0 3,4,0</coordinates>"
        "</LineString></Placemark>"
        "<Placemark><name>Pedra</name><Point><coordinates>-40.5,-20.1,0</coordinates></Point>"
        "</Placemark></Document></kml>"
    )
    assert parse_kml(kml) == [{"name": "Pedra", "lat": -20.1, "lon": -40.5}]
    assert parse_kml("<not-xml") == []


def test_category_links_and_urls() -> None:
    html = (
        '<h3 class="entry-title td-module-title"><a href="https://www.viagensecaminhos.com/a-es/"'
        ' title="A">A</a></h3>'
        '<h3 class="entry-title td-module-title"><a href="https://www.viagensecaminhos.com/b-es/">B</a></h3>'
        '<h3 class="entry-title td-module-title"><a href="https://www.viagensecaminhos.com/a-es/">A</a></h3>'
        '<a href="https://www.viagensecaminhos.com/menu/">menu</a>'
    )
    assert post_links(html) == [
        "https://www.viagensecaminhos.com/a-es/",
        "https://www.viagensecaminhos.com/b-es/",
    ]
    assert category_url("ES", 1) == (
        "https://www.viagensecaminhos.com/category/destinos/brasil/espirito-santo/"
    )
    assert category_url("ES", 2).endswith("/espirito-santo/page/2/")
    assert len(UF_SLUGS) == 27 and UF_SLUGS["MG"] == "minas-gerais"


def test_content_hash_ignores_whitespace_noise_only() -> None:
    assert content_hash("a  b\n") == content_hash("a b")
    assert content_hash("a b") != content_hash("a c")


def test_modified_time_comes_from_the_meta_tag_or_the_json_ld() -> None:
    assert extract_modified_time(_html("anchieta-es.html")) == date(2025, 12, 9)
    ld = '<script type="application/ld+json">{"dateModified":"2024-03-02T10:00:00+00:00"}</script>'
    assert extract_modified_time(ld) == date(2024, 3, 2)
    assert extract_modified_time("<html></html>") is None
