"""Name matching between a Brave entity and Wikidata labels (port of poc_db_images.py)."""

from __future__ import annotations

from collections.abc import Iterable

from brave.clients.wikimedia import WdItem

# The matcher itself lives in brave.shared.names (shared with the editorial lane, D-18).
from brave.shared.names import GENERIC, SAME, STOP, name_score, norm  # noqa: F401


def best_match(name: str, items: Iterable[WdItem]) -> WdItem | None:
    """The best-scoring item for ``name`` (ties → nearest), or None when nothing matches."""
    scored = [(max((name_score(name, lbl) for lbl in it.labels), default=0.0), it) for it in items]
    scored = [(s, it) for s, it in scored if s > 0]
    return max(scored, key=lambda sc: (sc[0], -sc[1].dist_km))[1] if scored else None
