"""Image lane policy + name matching (pure, no I/O)."""

from __future__ import annotations

from brave.clients.wikimedia import CommonsFile, WdItem
from brave.domains.images.matching import best_match, name_score
from brave.domains.images.policy import Pick, cover_reject, pick_attraction, reject_reason


def _f(title: str, w: int = 3000, h: int = 2000, lic: str = "cc-by-sa-4.0", **kw) -> CommonsFile:
    return CommonsFile(title=title, url="", page="", width=w, height=h, mime="image/jpeg",
                       license_code=lic, **kw)


def test_generic_word_alone_never_matches_the_city() -> None:
    assert name_score("Mercado Municipal de Goiânia", "Goiânia") == 0
    assert name_score("Praia do Saco", "praia do saco") == 1.0
    assert name_score("Igreja Matriz", "Igreja do Carmo") == 0


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


def test_cover_reject_for_destino() -> None:
    assert cover_reject(_f("File:Aracaju.jpg")) is None
    assert cover_reject(_f("File:Montagem Aracaju.jpg")) == "collage"
    assert cover_reject(_f("File:Aracaju.jpg", w=1000, h=600)) == "narrow"
    assert cover_reject(_f("File:Aracaju.jpg", w=2000, h=2000)) == "portrait"


def test_attraction_cover_is_the_first_usable_p18() -> None:
    files = {t: _f(t) for t in ("File:A.jpg", "File:B.jpg")}
    files["File:A.jpg"] = _f("File:A.jpg", lic="cc-by-nc-2.0")
    picks, rejected = pick_attraction({"p18": ["File:A.jpg", "File:B.jpg"]}, files, gallery_max=5)
    assert picks == [Pick("File:B.jpg", "cover", 0, "p18")]
    assert rejected == {"license:nc": 1}


def test_attraction_without_any_lane_gets_nothing() -> None:
    assert pick_attraction({}, {}, gallery_max=5) == ([], {})
