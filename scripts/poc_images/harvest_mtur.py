#!/usr/bin/env python3
"""Harvest the MTur Destinos stream from Wikimedia Commons. Runs ONCE.

The Ministerio do Turismo's Flickr stream (NSID 163189519@N03, ~5936 photos, all
Public Domain Mark 1.0) was bulk-imported to Commons and license-reviewed by
FlickreviewR 2. That import is reachable KEYLESS, which matters because Flickr
disabled API-key creation for free accounts and a commercial key needs staff
review.

    Category:Files from MTur Destinos Flickr stream  ->  ~5092 files (~87% of the
    stream; the missing ~736 cannot be identified without Flickr-side access)

Writes scripts/poc_images/out/mtur_index.json. Read-only w.r.t. the DB.

Run:
    .venv/bin/python -m scripts.poc_images.harvest_mtur
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from scripts.poc_images.commons import Commons, is_photo, license_verdict

CATEGORY = "Category:Files from MTur Destinos Flickr stream"
OUT = Path(__file__).parent / "out" / "mtur_index.json"

# Commons renames on import; the Flickr photo id survives in parentheses, e.g.
# "File:Credito obrigatorio Rogerio Cassimiro MTur (40998476882).jpg"
_FLICKR_ID = re.compile(r"\((\d{8,})\)")

# Categories that carry no territorial signal -- import/licensing bookkeeping.
_ADMIN_CAT = (
    "Files from MTur Destinos Flickr stream",
    "Flickr images reviewed by",
    "Flickr images missing SDC",
    "Flickr public domain images",
    "PD-author-FlickrPDM",
    "CC-PD-Mark",
    "Self-published work",
    "Media with locations",
    "Photographs by",
    "Uploaded with",
    "Files with no machine-readable",
    "Large images",
    "Media missing",
    "Images with",
    "Files with",
    "Taken with",
    "Panoramics",
    "Pages with",
)


def _place_categories(cats: list[str]) -> list[str]:
    """Categories left after stripping import/licensing bookkeeping.

    Verified at ~98% presence on a 100-file sample. This is the territorial join
    key -- 0/100 sampled files carry coordinates, so geosearch is NOT available
    for this lane.
    """
    return [c for c in cats if not any(c.startswith(a) or a in c for a in _ADMIN_CAT)]


def main() -> None:
    ap = argparse.ArgumentParser(description="Harvest MTur Destinos from Commons (keyless).")
    ap.add_argument("--limit", type=int, default=0, help="stop after N files (0 = all)")
    args = ap.parse_args()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    cx = Commons()

    print(f"Enumerating {CATEGORY} ...")
    titles = [m["title"] for m in cx.category_members(CATEGORY)]
    if args.limit:
        titles = titles[: args.limit]
    print(f"  {len(titles)} file titles")

    print("Fetching imageinfo (batches of 50) ...")
    info = cx.image_info(titles)
    print(f"  {len(info)} records with imageinfo")

    records = []
    for title, rec in info.items():
        if not is_photo(rec):
            continue
        fid = _FLICKR_ID.search(title)
        records.append({
            "title": title,
            "flickr_id": fid.group(1) if fid else None,
            "url": rec["url"],
            "thumb": rec.get("thumb"),
            "page_url": rec.get("descriptionurl"),
            "width": rec["width"],
            "height": rec["height"],
            "author": rec.get("artist"),
            "credit": rec.get("credit"),
            "license": rec.get("licenseshortname") or rec.get("license"),
            "license_url": rec.get("licenseurl"),
            "description": rec.get("imagedescription"),
            "object_name": rec.get("objectname"),
            "place_categories": _place_categories(rec.get("categories") or []),
            "all_categories": rec.get("categories") or [],
            "license_verdict": license_verdict(rec),
            "date": rec.get("datetimeoriginal"),
        })

    OUT.write_text(json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8")
    cx.close()

    with_id = sum(1 for r in records if r["flickr_id"])
    with_place = sum(1 for r in records if r["place_categories"])
    lic = {}
    for r in records:
        lic[r["license_verdict"]] = lic.get(r["license_verdict"], 0) + 1
    mp = sorted((r["width"] * r["height"]) / 1e6 for r in records if r["width"])

    print(f"\nwrote {OUT}  ({len(records)} photos)")
    print(f"  flickr_id recoverable : {with_id} ({with_id / max(len(records), 1):.1%})")
    print(f"  has place category    : {with_place} ({with_place / max(len(records), 1):.1%})")
    print(f"  license verdict       : {lic}")
    if mp:
        print(f"  megapixels min/med/max: {mp[0]:.1f} / {mp[len(mp) // 2]:.1f} / {mp[-1]:.1f}")


if __name__ == "__main__":
    main()
