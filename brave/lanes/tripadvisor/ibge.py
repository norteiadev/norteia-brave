"""IBGE municipality resolver for the TripAdvisor lane (TA-03).

Resolves a TripAdvisor location name + UF to an IBGE municipality record via:
  1. rapidfuzz token_sort_ratio with explicit accent-folding via unicodedata (NFKD) (threshold ≥ 88 by default)
  2. Haversine distance fallback if coordinates are provided (< 15km by default)
  3. Returns None if neither succeeds → caller quarantines as "ibge_unmatched"

Dataset: data/ibge/ibge_municipios.csv (5570 rows: ibge_code, nome, uf, lat, lng)
Source: github.com/kelvins/municipios-brasileiros (CC0, IBGE official data)

See CONTEXT.md TA-03 for the IBGE linkage specification.
"""

from __future__ import annotations

import csv
import math
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from rapidfuzz import fuzz, process, utils as rfuzz_utils


# ---------------------------------------------------------------------------
# IbgeMunicipio dataclass
# ---------------------------------------------------------------------------


@dataclass
class IbgeMunicipio:
    """Single IBGE municipality record.

    ibge_code: 7-digit IBGE municipality code (e.g. "3550308" for São Paulo)
    nome:      Official IBGE municipality name (UTF-8, with diacritics)
    uf:        2-letter state code (e.g. "SP", "BA")
    lat:       Latitude (decimal degrees)
    lng:       Longitude (decimal degrees)
    """

    ibge_code: str
    nome: str
    uf: str
    lat: float
    lng: float


# ---------------------------------------------------------------------------
# CSV loader
# ---------------------------------------------------------------------------


def load_ibge_csv(path: Path | str) -> list[IbgeMunicipio]:
    """Load IBGE municipality CSV into a list of IbgeMunicipio records.

    CSV header: ibge_code,nome,uf,lat,lng

    Args:
        path: Path to ibge_municipios.csv (Path or str — str is coerced).

    Returns:
        List of IbgeMunicipio records (empty list if file has only header).
    """
    path = Path(path)
    records: list[IbgeMunicipio] = []
    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            records.append(
                IbgeMunicipio(
                    ibge_code=row["ibge_code"].strip(),
                    nome=row["nome"].strip(),
                    uf=row["uf"].strip(),
                    lat=float(row["lat"]),
                    lng=float(row["lng"]),
                )
            )
    return records


# ---------------------------------------------------------------------------
# Haversine distance (pure math, no library)
# ---------------------------------------------------------------------------


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Compute great-circle distance between two points in kilometres.

    Pure math implementation — no external library.

    Args:
        lat1, lon1: Coordinates of point 1 (decimal degrees).
        lat2, lon2: Coordinates of point 2 (decimal degrees).

    Returns:
        Distance in kilometres.
    """
    R = 6371.0  # Earth radius in km
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


# ---------------------------------------------------------------------------
# Accent-fold helper
# ---------------------------------------------------------------------------


def _fold_accents(s: str) -> str:
    """Strip combining diacritical marks (Unicode category Mn) after NFKD decomposition.

    This is the explicit accent-fold step used by resolve_municipio — default_process
    alone does NOT remove diacritics (it only lowercases and strips non-alphanumeric
    ASCII punctuation). Without this, 'Maringa' vs 'Maringá' scores 85.7 < 88.
    """
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", s) if unicodedata.category(ch) != "Mn"
    )


# ---------------------------------------------------------------------------
# Municipality resolver
# ---------------------------------------------------------------------------


def resolve_municipio(
    name: str,
    uf: str,
    records: list[IbgeMunicipio],
    *,
    threshold: int = 88,
    max_distance_km: float = 15.0,
    candidate_lat: float | None = None,
    candidate_lng: float | None = None,
) -> IbgeMunicipio | None:
    """Resolve a TripAdvisor location name to an IBGE municipality record.

    Resolution strategy (TA-03):
      1. Filter records by UF (only search within the same state).
      2. rapidfuzz process.extractOne with scorer=fuzz.token_sort_ratio,
         score_cutoff=threshold (default 88). Accent-folding via _fold_accents
         (unicodedata NFKD) is applied explicitly to both query and choices before
         matching so pure diacritic differences score 100 ('Maringa' ↔ 'Maringá').
         Returned record always carries the original accented IBGE nome.
      3. On miss, haversine fallback if candidate_lat/lng are provided:
         return the first UF record within max_distance_km.
      4. Return None if neither matches → caller quarantines as "ibge_unmatched".

    Args:
        name:          Location name as returned by TripAdvisor.
        uf:            2-letter state code to filter candidates.
        records:       Full list of IbgeMunicipio records (from load_ibge_csv).
        threshold:     rapidfuzz score_cutoff (default 88, per TripAdvisorConfig).
        max_distance_km: Haversine fallback radius in km (default 15.0).
        candidate_lat: Latitude of the TripAdvisor location (for haversine fallback).
        candidate_lng: Longitude of the TripAdvisor location (for haversine fallback).

    Returns:
        Matching IbgeMunicipio record, or None if unresolvable.
    """
    # Step 1: filter by UF
    uf_records = [r for r in records if r.uf == uf]
    if not uf_records:
        return None

    # Step 2: rapidfuzz fuzzy match — accent-folded query + choices so pure diacritic
    # differences score 100 instead of ~85 (e.g. 'Maringa' ↔ 'Maringá').
    # NOTE: rapidfuzz default_process does NOT fold accents — that step is done
    # explicitly here via _fold_accents (unicodedata NFKD + strip Mn).
    # processor=default_process then handles case normalisation and non-alnum stripping.
    folded_name = _fold_accents(name)
    choices = [_fold_accents(r.nome) for r in uf_records]
    result = process.extractOne(
        folded_name,
        choices,
        scorer=fuzz.token_sort_ratio,
        score_cutoff=threshold,
        processor=rfuzz_utils.default_process,
    )
    if result is not None:
        _matched_name, _score, index = result
        return uf_records[index]  # original accented record — fold is never written back

    # Step 3: haversine fallback (only when coordinates are provided)
    if candidate_lat is not None and candidate_lng is not None:
        for r in uf_records:
            dist = haversine_km(candidate_lat, candidate_lng, r.lat, r.lng)
            if dist < max_distance_km:
                return r

    # Step 4: unresolved → caller quarantines as "ibge_unmatched"
    return None


# ---------------------------------------------------------------------------
# National municipality resolver (Phase 15, TA-12)
# ---------------------------------------------------------------------------


def resolve_municipio_national(
    candidate_lat: float | None,
    candidate_lng: float | None,
    records: list[IbgeMunicipio],
    *,
    max_distance_km: float = 50.0,
) -> IbgeMunicipio | None:
    """Resolve geocoded coordinates to the nearest IBGE municipality across ALL states.

    The all-Brazil bulk attractions lane (geoId 294280) has no per-UF context and no
    parent destino — the only signal is the attraction's geocoded lat/lng. This
    resolver runs a pure haversine over EVERY IBGE seat (no UF filter), picks the
    minimum-distance record, and returns it only if within ``max_distance_km``. The
    returned record carries ``.uf`` (the derived state) and ``.ibge_code`` (município),
    so the bulk lane derives UF from coordinates with no per-UF input.

    Args:
        candidate_lat:  Latitude of the geocoded attraction (decimal degrees).
        candidate_lng:  Longitude of the geocoded attraction (decimal degrees).
        records:        Full list of IbgeMunicipio records (from load_ibge_csv).
        max_distance_km: Relaxed match radius in km (default 50.0 — IBGE coords are
            the município seat; natural attractions sit ~15-25 km out, per Phase 14).

    Returns:
        The nearest IbgeMunicipio within ``max_distance_km``, or None when the
        coordinates are None or nothing falls within the radius.
    """
    # None coords → no derivation possible.
    if candidate_lat is None or candidate_lng is None:
        return None

    nearest: IbgeMunicipio | None = None
    nearest_km = float("inf")
    for r in records:
        dist = haversine_km(candidate_lat, candidate_lng, r.lat, r.lng)
        if dist < nearest_km:
            nearest_km = dist
            nearest = r

    if nearest is not None and nearest_km <= max_distance_km:
        return nearest
    return None
