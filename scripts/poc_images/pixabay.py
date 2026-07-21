"""Minimal Pixabay client for the image POC (decorative-fallback lane).

Constraints from the official docs, all load-bearing:
  - "requests must be cached for 24 hours" -- mandatory, not advisory.
  - 100 requests / 60 seconds; X-RateLimit-* headers returned.
  - Hotlinking is forbidden: "permanent hotlinking of images (using Pixabay URLs
    in your app) is not allowed... download them to your server first."
    `webformatURL` is explicitly "valid for 24 hours" -- so it is captured here
    for eyeballing during the POC and must NOT be persisted downstream.
  - ToS section 8 forbids "bulk, large-scale or systematic copying" without
    permission. A 30-attraction POC is fine; a 24/7 whole-Brazil sweep is not,
    and needs an approved limit from Pixabay first.

There is NO geographic search of any kind -- query, orientation, size, color and
locale are the only filters. That is why this lane sits at the bottom of the
cascade and its hits are flagged decorative.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

import httpx

API = "https://pixabay.com/api/"
CACHE_TTL = 86400  # Pixabay REQUIRES 24h caching.


class Pixabay:
    def __init__(self, api_key: str, redis=None, throttle: float = 1.0):
        self.key = api_key
        self.redis = redis
        self.throttle = throttle
        self._last = 0.0
        self.remaining: int | None = None
        self._http = httpx.Client(timeout=30.0)

    def search(self, q: str, lang: str = "pt", category: str | None = None) -> list[dict]:
        params: dict[str, Any] = {
            "key": self.key,
            "q": q[:100],  # documented max length
            "lang": lang,
            "image_type": "photo",
            "orientation": "horizontal",
            "safesearch": "true",
            "min_width": 1280,
            "per_page": 50,  # pool to re-rank locally, not 3
            "order": "popular",
        }
        if category:
            params["category"] = category

        ck = "poc:img:pixabay:" + hashlib.sha1(
            json.dumps({k: v for k, v in params.items() if k != "key"}, sort_keys=True).encode()
        ).hexdigest()
        if self.redis is not None:
            cached = self.redis.get(ck)
            if cached:
                return json.loads(cached)

        gap = self.throttle - (time.monotonic() - self._last)
        if gap > 0:
            time.sleep(gap)
        r = self._http.get(API, params=params)
        self._last = time.monotonic()
        if r.status_code == 429:
            raise RuntimeError("Pixabay rate limit exceeded (429)")
        r.raise_for_status()
        rem = r.headers.get("X-RateLimit-Remaining")
        if rem is not None:
            self.remaining = int(rem)
        hits = r.json().get("hits", [])
        if self.redis is not None:
            self.redis.setex(ck, CACHE_TTL, json.dumps(hits))
        return hits

    def close(self) -> None:
        self._http.close()


def rank_by_engagement(hits: list[dict],
                       keys: tuple[str, ...] = ("views", "downloads", "likes")) -> list[dict]:
    """Mean rank across the engagement metrics. Lower = more popular.

    Raw summation would be dominated by `views`, which is orders of magnitude
    larger than `likes`. Mean-of-ranks is scale-free and needs no magic weights.
    """
    if not hits:
        return []
    tables = []
    for key in keys:
        order = sorted(hits, key=lambda h: int(h.get(key) or 0), reverse=True)
        tables.append({id(h): i for i, h in enumerate(order)})
    return sorted(hits, key=lambda h: sum(t[id(h)] for t in tables) / len(tables))


def demo() -> None:
    """Self-check: no network."""
    hits = [
        {"id": 1, "views": 10, "downloads": 10, "likes": 10},
        {"id": 2, "views": 500, "downloads": 400, "likes": 90},   # best on all three
        {"id": 3, "views": 100, "downloads": 50, "likes": 20},
    ]
    assert [h["id"] for h in rank_by_engagement(hits)] == [2, 3, 1]
    assert rank_by_engagement([]) == []
    # A hit winning only the largest-scale metric must NOT automatically win.
    skew = [
        {"id": "a", "views": 1_000_000, "downloads": 1, "likes": 1},
        {"id": "b", "views": 10, "downloads": 999, "likes": 999},
    ]
    assert rank_by_engagement(skew)[0]["id"] == "b"
    print("pixabay self-check: OK")


if __name__ == "__main__":
    demo()
