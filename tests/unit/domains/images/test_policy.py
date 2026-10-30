"""Image lane policy + name matching (pure, no I/O)."""

from __future__ import annotations

from brave.clients.wikimedia import CommonsFile, WdItem
from brave.domains.images.matching import best_match, name_score
from brave.domains.images.policy import (
    Pick,
    cover_reject,
    pick_attraction,
    pick_destination,
    reject_reason,
)


def _f(title: str, w: int = 3000, h: int = 2000, lic: str = "cc-by-sa-4.0", **kw) -> CommonsFile:
    return CommonsFile(title=title, url="", page="", width=w, height=h, mime="image/jpeg",
                       license_code=lic, **kw)


def test_generic_word_alone_never_matches_the_city() -> None:
    assert name_score("Mercado Municipal de Goiânia", "Goiânia") == 0
    assert name_score("Praia do Saco", "praia do saco") == 1.0
    assert name_score("Igreja Matriz", "Igreja do Carmo") == 0


def test_same_place_in_other_words() -> None:
    """The e2e misses of 30/10 (Ouro Preto) match; the near-misses around them don't."""
    assert name_score("Basílica de Nossa Senhora do Pilar", "Igreja Matriz de Nossa Senhora do Pilar") == 1.0
    assert name_score("Centro Histórico de Ouro Preto", "Cidade Histórica de Ouro Preto") == 1.0
    assert name_score("Recife Antigo", "Antigo Hotel Central de Recife") == 0
    assert name_score("Igreja de Nossa Senhora do Rosário", "Igreja de Nossa Senhora do Pilar") == 0
    assert name_score("Igreja São Francisco", "Igreja de São Francisco de Assis (Ouro Preto)") > 0


def test_best_match_prefers_score_then_distance() -> None:
    far = WdItem("Q1", frozenset({"Praia do Saco"}), dist_km=4.0)
    near = WdItem("Q2", frozenset({"Praia do Saco"}), dist_km=1.0)
    other = WdItem("Q3", frozenset({"Goiânia"}), dist_km=0.1)
    assert best_match("Praia do Saco", [far, other, near]) is near
    assert best_match("Mercado Municipal de Goiânia", [other]) is None


def test_reject_reason() -> None:
    assert reject_reason(_f("File:Praia.jpg")) is None
    assert reject_reason(_f("File:Praia.jpg", lic="cc-by-nc-2.0")) == "license:nc"
    assert reject_reason(_f("File:Praia.jpg", lic="cc-by-nd-4.0")) == "license:nd"
    assert reject_reason(_f("File:Praia.jpg", lic="")) == "license:unknown"
    assert reject_reason(_f("File:Praia.jpg", restrictions="trademarked")) == "license:restrictions"
    assert reject_reason(_f("File:Bandeira de Aracaju.jpg")) == "chrome"
    assert reject_reason(_f("File:Mapa.svg")) == "not_photo"
    assert reject_reason(None) == "missing"
    djvu = CommonsFile(title="File:Livro.djvu", url="", page="", width=3000, height=2000,
                       mime="image/vnd.djvu", license_code="cc0")
    assert reject_reason(djvu) == "not_photo"  # norteia-api takes jpeg/png/webp only


def test_cover_reject_for_destino() -> None:
    assert cover_reject("File:Aracaju.jpg", 3000, 2000) is None
    assert cover_reject("File:Montagem Aracaju.jpg", 3000, 2000) == "collage"
    assert cover_reject("File:Aracaju.jpg", 1000, 600) == "narrow"
    assert cover_reject("File:Aracaju.jpg", 2000, 2000) == "portrait"


def test_destination_cover_skips_rejected_and_collage() -> None:
    files = {
        "File:Montagem.jpg": _f("File:Montagem.jpg"),
        "File:Retrato.jpg": _f("File:Retrato.jpg", w=1500, h=2000),
        "File:Orla.jpg": _f("File:Orla.jpg"),
    }
    picks, rejected = pick_destination("p18", list(files), files)
    assert picks == [Pick("File:Orla.jpg", "cover", 0, "p18")]
    assert rejected == {"cover:collage": 1, "cover:portrait": 1}


def test_attraction_cover_is_the_first_usable_p18() -> None:
    files = {t: _f(t) for t in ("File:A.jpg", "File:B.jpg")}
    files["File:A.jpg"] = _f("File:A.jpg", lic="cc-by-nc-2.0")
    picks, rejected = pick_attraction({"p18": ["File:A.jpg", "File:B.jpg"]}, files, gallery_max=5)
    assert picks == [Pick("File:B.jpg", "cover", 0, "p18")]
    assert rejected == {"license:nc": 1}


def test_attraction_without_any_lane_gets_nothing() -> None:
    assert pick_attraction({}, {}, gallery_max=5) == ([], {})


def test_attraction_gallery_order_and_limit() -> None:
    lanes = {
        "p18": [],
        "p180": ["File:D1.jpg", "File:D2.jpg"],
        "p373": ["File:C1.jpg", "File:D2.jpg", "File:C2.jpg"],
        "geo500": ["File:G1.jpg", "File:G2.jpg", "File:G3.jpg"],
    }
    files = {t: _f(t) for ts in lanes.values() for t in ts}
    picks, _ = pick_attraction(lanes, files, gallery_max=5)
    assert [(p.title, p.role, p.rank, p.lane) for p in picks] == [
        ("File:D1.jpg", "cover", 0, "p180"),  # no P18 → first P180
        ("File:D2.jpg", "gallery", 1, "p180"),
        ("File:C1.jpg", "gallery", 2, "p373"),
        ("File:C2.jpg", "gallery", 3, "p373"),  # D2 already used
        ("File:G1.jpg", "gallery", 4, "geo500"),
        ("File:G2.jpg", "gallery", 5, "geo500"),
    ]


def test_geo500_never_becomes_the_cover() -> None:
    lanes = {"geo500": ["File:G1.jpg"]}
    picks, _ = pick_attraction(lanes, {"File:G1.jpg": _f("File:G1.jpg")}, gallery_max=5)
    assert [p.role for p in picks] == ["gallery"]


def test_p373_is_the_last_resort_cover() -> None:
    """Praia de Boa Viagem: no P18, no P180, only its Commons category."""
    lanes = {"p18": [], "p180": [], "p373": ["File:C1.jpg", "File:C2.jpg"], "geo500": ["File:G1.jpg"]}
    picks, _ = pick_attraction(lanes, {t: _f(t) for ts in lanes.values() for t in ts}, gallery_max=5)
    assert [(p.title, p.role, p.lane) for p in picks] == [
        ("File:C1.jpg", "cover", "p373"), ("File:C2.jpg", "gallery", "p373"), ("File:G1.jpg", "gallery", "geo500"),
    ]


def test_a_wide_photo_titled_with_the_name_beats_a_small_p18() -> None:
    """Museu da Inconfidência: P18 is 864 px; a 1800 px P180 named after the museum wins.
    A wide P180 of something else (the church next door) never does."""
    name = "Museu da Inconfidência"
    lanes = {"p18": ["File:Small.jpg"], "p180": ["File:Igreja das Mercês.jpg", "File:Casa, atual Museu da Inconfidência.jpg"]}
    files = {"File:Small.jpg": _f("File:Small.jpg", w=864, h=665),
             "File:Igreja das Mercês.jpg": _f("File:Igreja das Mercês.jpg", w=4000, h=3000),
             "File:Casa, atual Museu da Inconfidência.jpg": _f("File:Casa, atual Museu da Inconfidência.jpg", w=1800, h=1200)}
    picks, _ = pick_attraction(lanes, files, gallery_max=5, name=name)
    assert picks[0] == Pick("File:Casa, atual Museu da Inconfidência.jpg", "cover", 0, "p180")
    assert picks[1] == Pick("File:Small.jpg", "gallery", 1, "p18")  # the P18 that lost the cover

    del files["File:Casa, atual Museu da Inconfidência.jpg"]
    picks, _ = pick_attraction(lanes, files, gallery_max=5, name=name)
    assert picks[0] == Pick("File:Small.jpg", "cover", 0, "p18")  # nothing better: what there is
