"""Match claims to Brave records and resolve ONE value per column.

``resolve_uf`` is idempotent and re-runnable (Rio changes between runs, so every non-volatile
claim of the UF is re-matched each time):

  1. match each (post, entity) group: attractions by KML coordinate (``matching``), the
     destination by municipality → IBGE; the claim rows are updated with the verdict;
  2. per matched attraction and column keep the value of the most recent post
     (``published_time``, ties by URL — deterministic) and write the dict under
     ``rio_records.normalized["editorial"]`` with ONE atomic UPDATE (``jsonb_set`` on that key;
     no read-modify-write of the whole column, so a concurrent writer of another key cannot be
     clobbered by a stale snapshot). ``processed_at`` is bumped only when the dict actually
     changed — it is what makes the Mar outbox republish, and what keeps a second run silent;
  3. per matched destination, upsert ``editorial_destinations`` (its own outbox: destinations
     are not in Mar). Its ``source_ref`` is the one the API already has: the most common
     ``parent_source_ref`` of the município's attractions, else ``ibge:<UF>:<ibge>``.

Volatile claims (``held_volatile``) are never matched, resolved or pushed.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

import structlog
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from brave.core.models import (
    EditorialClaim,
    EditorialDestination,
    EditorialDocument,
    Municipio,
    RioRecord,
)
from brave.domains.editorial.matching import (
    Match,
    RioCandidate,
    match_attraction,
    match_destination,
)
from brave.domains.editorial.matching import (
    Municipio as MunicipioRef,
)
from brave.domains.editorial.schema import ATTRACTION_PUSHED, DESTINATION_PUSHED

logger = structlog.get_logger(__name__)


@dataclass
class ResolveResult:
    claims: dict[str, int] = field(default_factory=dict)  # status -> count (non-volatile)
    attractions_written: int = 0  # Rio rows whose editorial dict changed
    attractions_cleared: int = 0
    destinations_changed: int = 0


# clock_timestamp(), not now(): now() is the TRANSACTION start, which can precede a
# push_attempted_at stamped meanwhile and make the Mar outbox think the row is not newer.
_SET_EDITORIAL = text(
    """
    UPDATE rio_records
       SET normalized = CAST(
               jsonb_set(coalesce(CAST(normalized AS jsonb), '{}'::jsonb),
                         '{editorial}', CAST(:val AS jsonb), true) AS json),
           processed_at = clock_timestamp()
     WHERE canonical_key = :key AND uf = :uf AND entity_type = 'attraction'
       AND (CAST(normalized AS jsonb) -> 'editorial') IS DISTINCT FROM CAST(:val AS jsonb)
    """
)
_CLEAR_EDITORIAL = text(
    """
    UPDATE rio_records
       SET normalized = CAST(CAST(normalized AS jsonb) - 'editorial' AS json),
           processed_at = clock_timestamp()
     WHERE uf = :uf AND entity_type = 'attraction'
       AND jsonb_exists(CAST(normalized AS jsonb), 'editorial')
       AND canonical_key <> ALL(CAST(:keep AS text[]))
    """
)


def _load_candidates(
    session: Session, uf: str
) -> list[tuple[RioCandidate, str | None, str | None]]:
    """The UF's live attractions: (candidate, municipio_id, parent_source_ref)."""
    n = RioRecord.normalized
    rows = session.execute(
        select(
            RioRecord.canonical_key,
            func.coalesce(n["name"].as_string(), n["nome"].as_string()),
            n["lat"].as_float(),
            n["lon"].as_float(),
            func.coalesce(n["municipio"].as_string(), n["municipio_nome"].as_string()),
            RioRecord.municipio_id,
            n["parent_source_ref"].as_string(),
        ).where(
            RioRecord.uf == uf,
            RioRecord.entity_type == "attraction",
            RioRecord.routing != "descarte",
            RioRecord.canonical_key.isnot(None),
        )
    ).all()
    return [
        (RioCandidate(k, name or "", lat, lon, muni), ibge, parent)
        for k, name, lat, lon, muni, ibge, parent in rows
        if name
    ]


def _newest(claims: list[EditorialClaim]) -> dict[str, Any]:
    """column -> value of the most recent post (published_time, then URL: deterministic)."""
    best: dict[str, tuple[tuple[date, str], Any]] = {}
    for c in claims:
        rank = (c.published_time or date.min, c.document_url)
        if c.field not in best or rank > best[c.field][0]:
            best[c.field] = (rank, c.value)
    return {f: v for f, (_, v) in best.items()}


def _usable(value: Any) -> bool:
    return bool(value.strip()) if isinstance(value, str) else bool(value)


def _match_group(
    kind: str,
    name: str,
    municipio: str | None,
    doc: EditorialDocument,
    candidates: list[RioCandidate],
    municipios: list[MunicipioRef],
) -> Match:
    if kind == "atrativo":
        return match_attraction(name, municipio, doc.kml_points or [], candidates)
    m = match_destination(municipio or name, municipios)
    if m.status == "unmatched" and municipio and municipio != name:
        m = match_destination(name, municipios)
    return m


def resolve_uf(session: Session, uf: str) -> ResolveResult:
    uf = uf.upper()
    result = ResolveResult()
    loaded = _load_candidates(session, uf)
    candidates = [c for c, _, _ in loaded]
    municipios = [
        MunicipioRef(code, nome)
        for code, nome in session.execute(
            select(Municipio.ibge_code, Municipio.nome).where(Municipio.uf == uf)
        )
    ]

    rows = session.execute(
        select(EditorialClaim, EditorialDocument)
        .join(EditorialDocument, EditorialDocument.url == EditorialClaim.document_url)
        .where(EditorialDocument.uf == uf, EditorialClaim.status != "held_volatile")
    ).all()

    # 1. match (post, entity) groups
    groups: dict[tuple[str, str, str], list[EditorialClaim]] = {}
    docs: dict[str, EditorialDocument] = {}
    for claim, doc in rows:
        groups.setdefault((claim.document_url, claim.entity_kind, claim.entity_name), []).append(
            claim
        )
        docs[doc.url] = doc
    for (url, kind, name), claims in groups.items():
        m = _match_group(kind, name, claims[0].municipio_text, docs[url], candidates, municipios)
        for c in claims:
            c.status, c.canonical_key, c.match_method = m.status, m.canonical_key, m.method
            c.match_score = m.score if m.status == "matched" else None
            c.lat, c.lon = m.lat, m.lon
            result.claims[m.status] = result.claims.get(m.status, 0) + 1
    session.flush()

    # 2. attractions → normalized["editorial"]
    by_key: dict[str, list[EditorialClaim]] = {}
    by_ibge: dict[str, list[EditorialClaim]] = {}
    for claim, _ in rows:
        if claim.status != "matched" or not _usable(claim.value) or claim.canonical_key is None:
            continue
        if claim.entity_kind == "atrativo" and claim.field in ATTRACTION_PUSHED:
            by_key.setdefault(claim.canonical_key, []).append(claim)
        elif claim.entity_kind == "destino" and claim.field in DESTINATION_PUSHED:
            by_ibge.setdefault(claim.canonical_key, []).append(claim)

    keep: list[str] = []
    for key, claims in by_key.items():
        editorial = _newest(claims)
        keep.append(key)
        val = json.dumps(editorial, ensure_ascii=False, sort_keys=True)
        result.attractions_written += session.execute(
            _SET_EDITORIAL, {"val": val, "key": key, "uf": uf}
        ).rowcount  # type: ignore[attr-defined]  # a DML Result is a CursorResult
    result.attractions_cleared = session.execute(
        _CLEAR_EDITORIAL, {"uf": uf, "keep": keep}
    ).rowcount  # type: ignore[attr-defined]

    # 3. destinations → editorial_destinations (the outbox)
    parents: dict[str, Counter[str]] = {}
    names: dict[str, Counter[str]] = {}
    for cand, ibge, parent in loaded:
        if ibge and parent:
            parents.setdefault(ibge, Counter())[parent] += 1
        if ibge and cand.municipio:
            names.setdefault(ibge, Counter())[cand.municipio] += 1
    nome_of = {m.ibge_code: m.nome for m in municipios}

    def top(counter: Counter[str] | None) -> str | None:
        return min(counter, key=lambda v: (-counter[v], v)) if counter else None

    for ibge, claims in by_ibge.items():
        fields = _newest(claims)
        source_ref = top(parents.get(ibge)) or f"ibge:{uf}:{ibge}"
        tourist_name = top(names.get(ibge)) or nome_of.get(ibge, "")
        row = session.get(EditorialDestination, source_ref)
        if row is None:
            session.add(
                EditorialDestination(
                    source_ref=source_ref,
                    uf=uf,
                    municipio_ibge=ibge,
                    tourist_name=tourist_name,
                    fields=fields,
                )  # fmt: skip
            )
        elif (row.fields, row.tourist_name, row.municipio_ibge, row.uf) != (
            fields,
            tourist_name,
            ibge,
            uf,
        ):
            row.fields, row.tourist_name, row.municipio_ibge, row.uf = (
                fields,
                tourist_name,
                ibge,
                uf,
            )
            row.pushed_at = row.push_error = (
                None  # pending again (push_hash is kept for the compare)
            )
            row.updated_at = datetime.now(UTC)
        else:
            continue
        result.destinations_changed += 1

    session.commit()
    logger.info(
        "editorial_resolved", uf=uf, claims=result.claims, written=result.attractions_written,
        cleared=result.attractions_cleared, destinations=result.destinations_changed,
    )  # fmt: skip
    return result
