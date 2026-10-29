"""Which Commons files may be used, and which become cover / gallery (pure).

Licence policy (Lei 9.610/98 — credit is mandatory, so the credit metadata travels with
every photo): permissive + share-alike only — CC0, PD, CC BY, CC BY-SA. No NC, no ND,
nothing with restrictions; anything not confidently matched is "unknown" and dropped.
Commons licensing is self-asserted, so this is a filter, not proof.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from brave.clients.wikimedia import CommonsFile

# File names that look like page chrome rather than photographs (en + pt).
CHROME = (
    "flag of", "-logo", "commons-logo", "disambig", "icon", "coat of arms",
    "bandeira", "brasao", "brasão", "mapa", "locator", "location", "map of", "flag",
)
BAD_EXT = (".svg", ".tif", ".tiff", ".pdf", ".ogv", ".webm", ".xcf", ".gif")
COLLAGE = ("collage", "montagem", "montage", "mosaico", "composicao", "composição")
COVER_MIN_WIDTH = 1280
COVER_MIN_ASPECT = 1.2

# Attraction: a cover is a photo OF the item (P18, then P180); proximity (geo500) and the
# item's category (P373) are gallery-only.
ATTRACTION_COVER_LANES = ("p18", "p180")
ATTRACTION_GALLERY_LANES = ("p180", "p373", "geo500")


@dataclass(frozen=True)
class Pick:
    title: str
    role: str  # cover | gallery
    rank: int  # 0 = cover, 1.. = gallery order
    lane: str  # p18 | p180 | p373 | geo500 | muni_top | atrativo


def license_verdict(f: CommonsFile) -> str:
    """"ok" | "rejected:<reason>" | "unknown" (port of scripts/poc_images/commons.py)."""
    lic = (f.license_code or f.license).lower()
    if f.restrictions:
        return "rejected:restrictions"
    if not lic:
        return "unknown"
    if "-nd" in lic or "noderiv" in lic:
        return "rejected:nd"
    if "-nc" in lic or "noncommercial" in lic:
        return "rejected:nc"
    if lic.startswith(("cc0", "pd", "cc-by")) or "public domain" in lic:
        return "ok"
    return "unknown"


def reject_reason(f: CommonsFile | None) -> str | None:
    """Why ``f`` may not be used at all, or None when it may."""
    if f is None:
        return "missing"
    title = f.title.lower()
    if title.endswith(BAD_EXT) or not f.mime.startswith("image/"):
        return "not_photo"
    if any(c in title for c in CHROME):
        return "chrome"
    verdict = license_verdict(f)
    if verdict != "ok":
        return "license:" + verdict.removeprefix("rejected:")
    return None


def cover_reject(title: str, width: int, height: int) -> str | None:
    """A destino cover is a wide landscape photo, not a collage."""
    if any(c in title.lower() for c in COLLAGE):
        return "collage"
    if width < COVER_MIN_WIDTH:
        return "narrow"
    if not height or width / height < COVER_MIN_ASPECT:
        return "portrait"
    return None


def _usable(
    lanes: Mapping[str, Sequence[str]], files: Mapping[str, CommonsFile], rejected: Counter[str]
) -> dict[str, list[str]]:
    """Per lane, the titles that pass reject_reason (each bad title counted once)."""
    seen: set[str] = set()
    out: dict[str, list[str]] = {}
    for lane, titles in lanes.items():
        out[lane] = []
        for t in dict.fromkeys(titles):
            reason = reject_reason(files.get(t))
            if reason is None:
                out[lane].append(t)
            elif t not in seen:
                rejected[reason] += 1
            seen.add(t)
    return out


def pick_attraction(
    lanes: Mapping[str, Sequence[str]], files: Mapping[str, CommonsFile], gallery_max: int
) -> tuple[list[Pick], Counter[str]]:
    """Cover = first usable P18, else P180. Gallery = the rest of P180, then P373, then
    geo500, up to ``gallery_max``. Returns the picks and the rejection counts by reason."""
    rejected: Counter[str] = Counter()
    ok = _usable(lanes, files, rejected)
    cover = next((Pick(ok[lane][0], "cover", 0, lane) for lane in ATTRACTION_COVER_LANES if ok.get(lane)), None)
    used = {cover.title} if cover else set()
    gallery: list[Pick] = []
    for lane in ATTRACTION_GALLERY_LANES:
        for t in ok.get(lane, []):
            if len(gallery) < gallery_max and t not in used:
                used.add(t)
                gallery.append(Pick(t, "gallery", len(gallery) + 1, lane))
    return ([cover] if cover else []) + gallery, rejected


def pick_destination(
    lane: str, titles: Sequence[str], files: Mapping[str, CommonsFile]
) -> tuple[list[Pick], Counter[str]]:
    """The destino cover from one lane: the first title that is usable AND passes
    cover_reject. Rejections by reason ("cover:<why>" for cover_reject)."""
    rejected: Counter[str] = Counter()
    for t in _usable({lane: titles}, files, rejected)[lane]:
        f = files[t]
        why = cover_reject(t, f.width, f.height)
        if why is None:
            return [Pick(t, "cover", 0, lane)], rejected
        rejected["cover:" + why] += 1
    return [], rejected
