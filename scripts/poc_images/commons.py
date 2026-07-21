"""Minimal keyless MediaWiki/Commons client for the image POC.

Serves BOTH lanes that talk to commons.wikimedia.org:
  - the MTur lane (category enumeration of an already-imported Flickr stream)
  - the Commons lane (geosearch + phrase search)

House rules baked in, all verified against the live API:
  - A descriptive User-Agent is MANDATORY. No UA -> 403. `python-requests/x` -> 403.
    See https://foundation.wikimedia.org/wiki/Policy:User-Agent_policy
  - Requests go out in SERIES, never parallel (API:Etiquette).
  - `list=search` inflates wildly without quotes: "Vale do Pati" -> 3717 unquoted
    vs 176 phrase-quoted. Always phrase-quote.
  - geosearch defaults to gsnamespace=0 (articles). File pages need gsnamespace=6.
"""

from __future__ import annotations

import time
from typing import Any, Iterator

import httpx

API = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = (
    "NorteiaBrave-POC/0.1 (https://norteia.com.br; leandro.freire08@gmail.com)"
)

# Files whose names look like page chrome rather than photographs.
_CHROME = ("flag of", "-logo", "commons-logo", "disambig", "icon", "coat of arms")
_BAD_EXT = (".svg", ".tif", ".tiff", ".pdf", ".ogv", ".webm", ".xcf")


class Commons:
    """Serial, throttled, keyless Commons client."""

    def __init__(self, throttle: float = 0.35, timeout: float = 30.0):
        self.throttle = throttle
        self._last = 0.0
        self._http = httpx.Client(
            timeout=timeout,
            headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"},
            follow_redirects=True,
        )

    def _get(self, params: dict[str, Any]) -> dict:
        gap = self.throttle - (time.monotonic() - self._last)
        if gap > 0:
            time.sleep(gap)
        params = {**params, "format": "json", "formatversion": "2"}
        r = self._http.get(API, params=params)
        self._last = time.monotonic()
        r.raise_for_status()
        return r.json()

    # ---------------------------------------------------------------- MTur lane

    def category_members(self, category: str, page_cap: int = 100) -> Iterator[dict]:
        """Yield File: members of a category, following cmcontinue.

        page_cap is a runaway guard, not a coverage limit -- 5092 files at
        cmlimit=500 needs 11 pages.
        """
        cont: str | None = None
        for _ in range(page_cap):
            params = {
                "action": "query",
                "list": "categorymembers",
                "cmtitle": category,
                "cmlimit": "500",
                "cmnamespace": "6",
            }
            if cont:
                params["cmcontinue"] = cont
            data = self._get(params)
            yield from data.get("query", {}).get("categorymembers", [])
            cont = data.get("continue", {}).get("cmcontinue")
            if not cont:
                return

    # ------------------------------------------------------------ shared detail

    def image_info(self, titles: list[str], width: int = 1280) -> dict[str, dict]:
        """Batch imageinfo + categories. Batches of 50 (API limit for anon)."""
        out: dict[str, dict] = {}
        for i in range(0, len(titles), 50):
            chunk = titles[i : i + 50]
            data = self._get({
                "action": "query",
                "titles": "|".join(chunk),
                "prop": "imageinfo|categories",
                "iiprop": "url|size|mime|user|extmetadata",
                "iiurlwidth": str(width),
                "cllimit": "max",
            })
            for page in data.get("query", {}).get("pages", []):
                ii = (page.get("imageinfo") or [{}])[0]
                if not ii:
                    continue
                meta = ii.get("extmetadata") or {}
                out[page["title"]] = {
                    "title": page["title"],
                    "url": ii.get("url"),
                    "thumb": ii.get("thumburl"),
                    "descriptionurl": ii.get("descriptionurl"),
                    "width": ii.get("width") or 0,
                    "height": ii.get("height") or 0,
                    "mime": ii.get("mime"),
                    "uploader": ii.get("user"),
                    "categories": [
                        c["title"].removeprefix("Category:")
                        for c in (page.get("categories") or [])
                    ],
                    # extmetadata values are {"value": ..., "source": ...}
                    **{
                        k.lower(): (meta.get(k) or {}).get("value")
                        for k in (
                            "License", "LicenseShortName", "UsageTerms", "LicenseUrl",
                            "Artist", "Credit", "ImageDescription", "ObjectName",
                            "Restrictions", "Assessments", "DateTimeOriginal",
                        )
                    },
                }
        return out

    # ------------------------------------------------------------ Commons lane

    def geosearch(self, lat: float, lon: float, radius_m: int, limit: int = 50) -> list[dict]:
        """File-namespace geosearch. gsnamespace=6 is REQUIRED (default is 0)."""
        data = self._get({
            "action": "query",
            "list": "geosearch",
            "gscoord": f"{lat}|{lon}",
            "gsradius": str(max(10, min(radius_m, 10000))),  # API range: 10..10000
            "gslimit": str(min(limit, 500)),
            "gsnamespace": "6",
        })
        return [
            {"title": g["title"], "dist": g.get("dist")}
            for g in data.get("query", {}).get("geosearch", [])
        ]

    def search(self, phrase: str, limit: int = 50) -> list[dict]:
        """Phrase-quoted File: search. Unquoted counts are noise -- see module docstring."""
        data = self._get({
            "action": "query",
            "list": "search",
            "srsearch": f'"{phrase}"',
            "srnamespace": "6",
            "srlimit": str(min(limit, 500)),
        })
        return [{"title": s["title"]} for s in data.get("query", {}).get("search", [])]

    def close(self) -> None:
        self._http.close()


def is_photo(rec: dict) -> bool:
    """Drop SVG/TIFF and obvious page chrome."""
    title = (rec.get("title") or "").lower()
    if any(title.endswith(e) for e in _BAD_EXT):
        return False
    if not (rec.get("mime") or "").startswith("image/"):
        return False
    return not any(c in title for c in _CHROME)


def license_verdict(rec: dict) -> str:
    """Classify against the POC policy: permissive + share-alike, no ND, no NC.

    Returns "ok" | "rejected:<reason>" | "unknown". Anything not confidently
    matched lands in "unknown" and is COUNTED in the report -- never silently
    accepted. Commons licensing is self-asserted, so this is a filter, not proof.
    """
    lic = (rec.get("license") or rec.get("licenseshortname") or "").lower()
    if rec.get("restrictions"):
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
