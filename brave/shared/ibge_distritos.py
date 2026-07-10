"""IBGE distrito resolver (DTB — Divisão Territorial Brasileira), name-only match.

Shared/kernel home for the distrito enrichment resolver. It lives here — not in a
domain package — because more than one collection lane needs it: the Places
discovery lane (``brave.lanes.atrativos.discovery_agent``) resolves an attraction's
``administrative_area_level_3`` hint, and the TripAdvisor lane reserves the same
canonical keys. A domain must not import a sibling domain (D-18), so the resolver
sits in ``brave.shared`` where every domain may reach it.

The DTB carries NO GPS — distritos are resolved by NAME only (no haversine
fallback), scoped to the parent município so the candidate set is a handful of
rows and the fuzzy match is safe.

Dataset: data/ibge/ibge_distritos.csv (distrito_code, nome, ibge_code, municipio_nome, uf)
"""

from __future__ import annotations

import csv
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from rapidfuzz import fuzz, process
from rapidfuzz import utils as rfuzz_utils

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

# ---------------------------------------------------------------------------
# IbgeDistrito dataclass (IBGE DTB 2025)
# ---------------------------------------------------------------------------


@dataclass
class IbgeDistrito:
    """Single IBGE distrito record (from the DTB — Divisão Territorial Brasileira).

    distrito_code:  9-digit IBGE distrito code (e.g. "292530307" for Arraial D'Ajuda)
    nome:           Official IBGE distrito name (UTF-8, with diacritics)
    ibge_code:      7-digit parent município code (e.g. "2925303" for Porto Seguro)
    municipio_nome: Parent município name
    uf:             2-letter state code (e.g. "BA")

    The DTB has no GPS — distritos are resolved by NAME only (no haversine fallback).
    """

    distrito_code: str
    nome: str
    ibge_code: str
    municipio_nome: str
    uf: str


# ---------------------------------------------------------------------------
# Distrito CSV loader
# ---------------------------------------------------------------------------


def load_distritos_csv(path: Path | str) -> list[IbgeDistrito]:
    """Load the IBGE distrito CSV into a list of IbgeDistrito records.

    CSV header: distrito_code,nome,ibge_code,municipio_nome,uf

    Args:
        path: Path to ibge_distritos.csv (Path or str — str is coerced).

    Returns:
        List of IbgeDistrito records (empty list if file has only header).
    """
    path = Path(path)
    records: list[IbgeDistrito] = []
    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            records.append(
                IbgeDistrito(
                    distrito_code=row["distrito_code"].strip(),
                    nome=row["nome"].strip(),
                    ibge_code=row["ibge_code"].strip(),
                    municipio_nome=row["municipio_nome"].strip(),
                    uf=row["uf"].strip(),
                )
            )
    return records


# ---------------------------------------------------------------------------
# DB loader (reference table — supersedes the CSV loader in the live path)
# ---------------------------------------------------------------------------


def load_distritos(session: "Session") -> list[IbgeDistrito]:
    """Load IBGE distrito reference rows from the ``distritos`` table.

    Returns the SAME ``IbgeDistrito`` dataclass list as ``load_distritos_csv`` so
    every downstream resolver (``resolve_distrito``, ``resolve_distrito_place``) is
    untouched — only the loader source changes (static CSV → DB reference table
    seeded at migrate time).

    Args:
        session: SQLAlchemy synchronous Session.

    Returns:
        List of IbgeDistrito records (empty list if the table has no rows).
    """
    from brave.core.models import Distrito

    return [
        IbgeDistrito(
            distrito_code=row.distrito_code,
            nome=row.nome,
            ibge_code=row.ibge_code,
            municipio_nome=row.municipio_nome,
            uf=row.uf,
        )
        for row in session.query(Distrito).all()
    ]


# ---------------------------------------------------------------------------
# Accent / apostrophe fold helpers
# ---------------------------------------------------------------------------


def _fold_accents(s: str) -> str:
    """Strip combining diacritical marks (Unicode category Mn) after NFKD decomposition.

    default_process alone does NOT remove diacritics (it only lowercases and strips
    non-alphanumeric ASCII punctuation). Without this, 'Maringa' vs 'Maringá' scores
    85.7 < 88. Mirrors ``resolve_municipio``'s fold step (kept private per module so
    the two geo resolvers stay decoupled).
    """
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", s) if unicodedata.category(ch) != "Mn"
    )


def _strip_apostrophes(s: str) -> str:
    """Remove ASCII and typographic apostrophes so elided-preposition distrito names
    ("Arraial D'Ajuda") collapse to a single token ("darjuda"→"dajuda") that matches a
    de-apostrophised hint.
    """
    return s.replace("’", "").replace("'", "")


# ---------------------------------------------------------------------------
# Distrito resolver (IBGE DTB — name-only, no GPS)
# ---------------------------------------------------------------------------


def resolve_distrito(
    name: str,
    municipio_ibge_code: str,
    distritos: list[IbgeDistrito],
    *,
    threshold: int = 88,
) -> IbgeDistrito | None:
    """Resolve a distrito NAME hint to an IBGE distrito record within a município.

    The DTB carries no GPS, so the only usable signal is the distrito name text (from
    Google Places ``administrative_area_level_3``). This first filters distritos to the
    parent município (``ibge_code == municipio_ibge_code``) — a handful of candidates —
    which makes the fuzzy match safe, then applies an accent-folded token_sort_ratio
    strategy (no haversine fallback).

    Args:
        name:                Distrito name hint (e.g. Places admin_area_level_3 text).
        municipio_ibge_code: 7-digit parent município code to scope candidates.
        distritos:           Full list of IbgeDistrito records (from load_distritos_csv).
        threshold:           rapidfuzz score_cutoff (default 88, mirrors resolve_municipio).

    Returns:
        Matching IbgeDistrito record, or None when the name is falsy/non-str, the
        município has no distritos, or nothing scores above threshold → keys stay null.
    """
    # Step 0: guard falsy/non-str names — Places can omit admin_area_level_3 entirely,
    # so distrito_hint is often None. Treat as unmatched → keys stay null (never crash).
    if not isinstance(name, str) or not name.strip():
        return None
    # Step 1: filter to the parent município (small, safe candidate set).
    muni_distritos = [d for d in distritos if d.ibge_code == municipio_ibge_code]
    if not muni_distritos:
        return None

    # Step 2: accent-folded token_sort_ratio, with one distrito-specific twist: strip
    # apostrophes BEFORE folding. Distrito names carry the elided-preposition apostrophe
    # ("Arraial D'Ajuda", "Alta Floresta D'Oeste") that município seats never have.
    # default_process turns "D'Ajuda" into two tokens ("d ajuda"), so a de-apostrophised
    # hint ("arraial dajuda") tokenises to one ("dajuda") and token_sort_ratio drops to
    # ~62. Collapsing the apostrophe to nothing first makes both forms tokenise
    # identically → score 100.
    folded_name = _fold_accents(_strip_apostrophes(name))
    choices = [_fold_accents(_strip_apostrophes(d.nome)) for d in muni_distritos]
    result = process.extractOne(
        folded_name,
        choices,
        scorer=fuzz.token_sort_ratio,
        score_cutoff=threshold,
        processor=rfuzz_utils.default_process,
    )
    if result is not None:
        _matched_name, _score, index = result
        return muni_distritos[index]  # original accented record — fold never written back

    # Step 3: unresolved → keys stay null.
    return None


def resolve_distrito_place(
    place: str,
    municipio_ibge_code: str,
    distritos: list[IbgeDistrito],
    *,
    threshold: int = 88,
) -> IbgeDistrito | None:
    """Resolve a Melhores Destinos breadcrumb <Place> to a genuinely sub-município distrito.

    The MD breadcrumb flattens município AND distrito into a single <Place> level
    ("Arraial d'Ajuda" sits under Bahia as a peer of Porto Seguro). Scoped to the
    attraction's parent município, this layers a SEAT-GUARD over ``resolve_distrito``:
    every município has a seat distrito that carries the município's own name (Porto
    Seguro the município has a Porto Seguro seat distrito), and assigning that seat adds
    no finer-than-município signal. So when the match name equals the parent município
    name (accent/case-folded), it is the seat → return None. Only a sub-município
    distrito (Arraial d'Ajuda != Porto Seguro) is a genuine distrito assignment.

    Args:
        place:               Breadcrumb <Place> text (município OR flattened distrito).
        municipio_ibge_code: 7-digit parent município code to scope candidates.
        distritos:           Full list of IbgeDistrito records (from load_distritos_csv).
        threshold:           rapidfuzz score_cutoff (default 88, mirrors resolve_distrito).

    Returns:
        Matching IbgeDistrito record (whose ``ibge_code`` is the parent município code),
        or None when place is falsy, nothing matches, or the match is the seat distrito.
    """
    # Falsy place → nothing to resolve (guarded here too, though resolve_distrito guards).
    if not place:
        return None
    match = resolve_distrito(place, municipio_ibge_code, distritos, threshold=threshold)
    if match is None:
        return None
    # Seat guard: a match whose name folds to the parent município name is the seat
    # distrito (município-level, not finer) → drop it.
    if _fold_accents(match.nome).casefold() == _fold_accents(match.municipio_nome).casefold():
        return None
    return match
