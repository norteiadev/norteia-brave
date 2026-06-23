"""IBGE municipality resolver for the TripAdvisor lane (TA-03).

Resolves a TripAdvisor location name + UF to an IBGE municipality record via:
  1. rapidfuzz token_sort_ratio fuzzy match (threshold ≥ 88 by default)
  2. Haversine distance fallback if coordinates are provided (< 15km by default)
  3. Returns None if neither succeeds → caller quarantines as "ibge_unmatched"

Dataset: data/ibge/ibge_municipios.csv (5570 rows: ibge_code, nome, uf, lat, lng)
Source: github.com/kelvins/municipios-brasileiros (CC0, IBGE official data)

See CONTEXT.md TA-03 for the IBGE linkage specification.
"""

from __future__ import annotations

import csv
import math
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
         score_cutoff=threshold (default 88). Handles accent normalization
         ('Sao Paulo' ↔ 'São Paulo') and word-order variation.
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

    # Step 2: rapidfuzz fuzzy match (processor=default_process handles case normalization
    # and accent-agnostic comparison — 'Sao Paulo' ↔ 'São Paulo', 'salvador' ↔ 'Salvador')
    choices = [r.nome for r in uf_records]
    result = process.extractOne(
        name,
        choices,
        scorer=fuzz.token_sort_ratio,
        score_cutoff=threshold,
        processor=rfuzz_utils.default_process,
    )
    if result is not None:
        _matched_name, _score, index = result
        return uf_records[index]

    # Step 3: haversine fallback (only when coordinates are provided)
    if candidate_lat is not None and candidate_lng is not None:
        for r in uf_records:
            dist = haversine_km(candidate_lat, candidate_lng, r.lat, r.lng)
            if dist < max_distance_km:
                return r

    # Step 4: unresolved → caller quarantines as "ibge_unmatched"
    return None
