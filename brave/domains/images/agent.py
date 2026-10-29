"""ImageLaneAgent: one Rio entity → its photos in the archive + entity_images rows.

``run(rio)``:
  1. ``image_lookups`` row with a final outcome (matched / no_match) → nothing to do (None).
  2. Resolve the Wikidata QID (atrativo: items within around_km name-matched, else entity
     search by name within name_km). No QID → no photo at all: "a photo from nearby"
     misleads (the Jolimont case).
  3. Candidate titles per lane → imageinfo → policy picks.
  4. Each pick reuses its ``images`` row by commons_title (then by sha1 of the bytes);
     otherwise the file is downloaded and written to the archive.
  5. Upsert ``entity_images`` and the lookup.

Writes only the lane's own tables — never ``normalized``. The caller commits. An external
failure (HTTP / Commons error after retries) records ``outcome=error``, which is not a final
marker, so the next run tries again (like _PROPAGATE in places_enrichment).
"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from brave.clients.image_store import ImageStoreProtocol, storage_key
from brave.clients.wikimedia import CommonsFile, WdItem, WikimediaError
from brave.config.settings import ImagesConfig
from brave.core.models import EntityImage, Image, ImageLookup, RioRecord
from brave.domains.images.matching import best_match
from brave.domains.images.policy import Pick, pick_attraction

logger = structlog.get_logger(__name__)

FINAL_OUTCOMES = ("matched", "no_match")
_EXTERNAL_ERRORS = (httpx.HTTPError, WikimediaError)


@dataclass
class LaneResult:
    outcome: str  # matched | no_match | error
    picks: list[Pick] = field(default_factory=list)
    rejected: Counter[str] = field(default_factory=Counter)
    downloaded: int = 0


def credit_line(f: CommonsFile) -> str:
    return f"{f.artist or 'Autor desconhecido'} · {f.license or f.license_code} · Wikimedia Commons"


class ImageLaneAgent:
    def __init__(
        self, session: Session, wikimedia: Any, store: ImageStoreProtocol, config: ImagesConfig
    ) -> None:
        self._s = session
        self._wm = wikimedia
        self._store = store
        self._cfg = config

    async def run(self, rio: RioRecord) -> LaneResult | None:
        key = rio.canonical_key or ""
        lookup = self._s.get(ImageLookup, key)
        if lookup is not None and lookup.outcome in FINAL_OUTCOMES:
            return None
        try:
            result, match, via = await self._attraction(rio)
        except _EXTERNAL_ERRORS as exc:
            logger.warning("images_external_error", canonical_key=key, error=f"{type(exc).__name__}: {exc}")
            self._mark(rio, "error")
            return LaneResult("error")
        self._mark(rio, result.outcome, match, via)
        return result

    # -- atrativo -------------------------------------------------------------------

    async def _resolve(self, name: str, lat: float, lon: float) -> tuple[WdItem | None, str | None]:
        match = best_match(name, await self._wm.items_around(lat, lon, self._cfg.around_km))
        if match is not None:
            return match, "around5km"
        match = best_match(name, await self._wm.items_by_name(name, lat, lon, self._cfg.name_km))
        return match, ("name30km" if match else None)

    async def _attraction(self, rio: RioRecord) -> tuple[LaneResult, WdItem | None, str | None]:
        norm = rio.normalized or {}
        lat, lon = float(norm["lat"]), float(norm["lon"])
        match, via = await self._resolve(norm.get("name") or "", lat, lon)
        if match is None:
            return LaneResult("no_match"), None, None
        lanes = {
            "p18": list(match.p18),
            "p180": await self._wm.depicts(match.qid),
            "p373": await self._wm.category_files(match.p373) if match.p373 else [],
            "geo500": await self._wm.geosearch(lat, lon, self._cfg.geo_m),
        }
        titles = [t for ts in lanes.values() for t in ts]
        files = await self._wm.image_info(titles) if titles else {}
        picks, rejected = pick_attraction(lanes, files, self._cfg.gallery_max)
        result = LaneResult("matched", picks, rejected)
        await self._save(rio, picks, files, match.qid, result)
        return result, match, via

    # -- persistence ----------------------------------------------------------------

    async def _archive(self, f: CommonsFile, result: LaneResult) -> Image:
        img = self._s.scalar(select(Image).where(Image.commons_title == f.title))
        if img is not None:
            return img
        data, mime = await self._wm.download(f)
        sha1 = hashlib.sha1(data).hexdigest()
        img = self._s.scalar(select(Image).where(Image.sha1 == sha1))
        if img is not None:
            return img
        key = storage_key(sha1, mime)
        self._store.put(key, data, mime)
        result.downloaded += 1
        tw = self._cfg.thumb_width
        w, h = (tw, round(f.height * tw / f.width)) if f.width > tw else (f.width, f.height)
        img = Image(
            sha1=sha1, storage_key=key, commons_title=f.title, source_url=f.page,
            file_url=f.url.split("?", 1)[0], width=w, height=h, mime=mime, bytes=len(data),
            author=f.artist or None, license=f.license or f.license_code,
            license_url=f.license_url or None, credit_line=credit_line(f),
            assessment=f.assessment[:64] or None, commons_status="ok",
        )
        self._s.add(img)
        self._s.flush()
        return img

    async def _save(
        self, rio: RioRecord, picks: list[Pick], files: dict[str, CommonsFile], qid: str | None,
        result: LaneResult,
    ) -> None:
        # All downloads first: an error mid-way leaves no entity_images half-set.
        images = [await self._archive(files[p.title], result) for p in picks]
        key = rio.canonical_key or ""
        for pick, img in zip(picks, images, strict=True):
            row = self._s.scalar(
                select(EntityImage).where(EntityImage.canonical_key == key, EntityImage.image_id == img.id)
            )
            if row is None:
                row = EntityImage(canonical_key=key, entity_type=rio.entity_type, image_id=img.id)
                self._s.add(row)
            row.role, row.rank, row.lane, row.wikidata_qid = pick.role, pick.rank, pick.lane, qid
            row.removed_at = None
        self._s.flush()

    def _mark(self, rio: RioRecord, outcome: str, match: WdItem | None = None, via: str | None = None) -> None:
        self._s.merge(ImageLookup(
            canonical_key=rio.canonical_key, entity_type=rio.entity_type, uf=rio.uf,
            qid=match.qid if match else None, via=via,
            dist_km=match.dist_km if match and via != "ibge" else None, outcome=outcome,
            checked_at=datetime.now(UTC),
        ))
        self._s.flush()
