"""Match an editorial entity to a Brave record. Pure: candidates are plain dataclasses.

Attraction, in order:
  1. a KML point of the post whose name is compatible with the entity's → the Rio attractions
     of the UF within ``RADIUS_KM`` of that POINT with a compatible name. One → ``matched``,
     two or more → ``ambiguous``, none → ``unmatched`` (no fallback: the post located the
     place and Brave has nothing there).
  2. no compatible point → name-only inside the municipality named in the text, with a high
     cutoff. The municipality only SCOPES this search; it never decides alone, and it never
     vetoes a coordinate match.

Destination: the municipality's name → its IBGE code (``municipios`` table).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from brave.shared.geo import haversine_km
from brave.shared.names import name_score, norm

RADIUS_KM = 0.3
NAME_ONLY_CUTOFF = 0.85


@dataclass(frozen=True)
class RioCandidate:
    canonical_key: str
    name: str
    lat: float | None
    lon: float | None
    municipio: str | None  # the municipality NAME the record carries


@dataclass(frozen=True)
class Municipio:
    ibge_code: str
    nome: str


@dataclass(frozen=True)
class Match:
    status: str  # matched | ambiguous | unmatched
    canonical_key: str | None = None
    method: str | None = None  # kml_coord | name_municipio | municipio_ibge
    score: float = 0.0
    lat: float | None = None  # the KML point's coordinates, when a compatible point exists
    lon: float | None = None
    candidates: tuple[str, ...] = ()


def _decide(found: dict[str, float], method: str, lat: float | None, lon: float | None) -> Match:
    if not found:
        return Match("unmatched", lat=lat, lon=lon)
    if len(found) > 1:
        return Match("ambiguous", lat=lat, lon=lon, candidates=tuple(sorted(found)))
    ((key, score),) = found.items()
    return Match("matched", key, method, score, lat, lon, (key,))


def match_attraction(
    name: str,
    municipio: str | None,
    kml_points: list[dict[str, Any]],
    candidates: list[RioCandidate],
    *,
    radius_km: float = RADIUS_KM,
    cutoff: float = NAME_ONLY_CUTOFF,
) -> Match:
    scored = [(name_score(name, p["name"]), p) for p in kml_points]
    top = max((s for s, _ in scored), default=0.0)
    if top > 0:
        best = [p for s, p in scored if s == top]
        found: dict[str, float] = {}
        for p in best:
            for c in candidates:
                if c.lat is None or c.lon is None:
                    continue
                if haversine_km(p["lat"], p["lon"], c.lat, c.lon) > radius_km:
                    continue
                score = max(name_score(name, c.name), name_score(p["name"], c.name))
                if score > 0:
                    found[c.canonical_key] = max(found.get(c.canonical_key, 0.0), score)
        return _decide(found, "kml_coord", best[0]["lat"], best[0]["lon"])

    if not municipio:
        return Match("unmatched")
    scope = norm(municipio)
    found = {}
    for c in candidates:
        if c.municipio and norm(c.municipio) == scope:
            score = name_score(name, c.name)
            if score >= cutoff:
                found[c.canonical_key] = score
    return _decide(found, "name_municipio", None, None)


def match_destination(municipio: str, municipios: list[Municipio]) -> Match:
    """The municipality's IBGE code by (accent/case-folded) name; the match key IS the code."""
    wanted = norm(municipio)
    hits = {m.ibge_code: 1.0 for m in municipios if norm(m.nome) == wanted}
    return _decide(hits, "municipio_ibge", None, None)
