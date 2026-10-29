"""Apply the Brave side of a territorial dedup manifest (territorial-dedup-2026-10.json).

Four actions, each idempotent (a record already in the target state is skipped):

  merge          manifest.attractions where drop AND keep are TripAdvisor cards: the
                 Duplicados merge (PATCH /api/v1/dedup/pairs/{rio_id}/resolve) — loser Rio →
                 descarte, its source_ref recorded on the kept Mar. The outbox then pushes
                 the loser as visibility=hidden and stamps retracted_at.
  fix            manifest.brave_fixes: município (IBGE code), lat/lng, name; clear_place
                 nulls the cached Google Places data (pushed as null, so the API drops it).
  rename         manifest.renames: the display name. A TA re-sweep keeps it — the Nascente
                 version merge only takes a key nobody changed since the previous version.
  same_place_id  every Rio routed 'mar' with no active Mar whose google_place_id is already
                 in Mar in the same município → descarte (dedup_same_place_id).

fix/rename stamp processed_at, so the outbox (sync._needs_sync) re-derives and re-pushes.

Usage (worker container or host with BRAVE_DB_URL):
    python scripts/territorial_dedup_fixes.py --manifest territorial-dedup-2026-10.json
    python scripts/territorial_dedup_fixes.py --manifest ... --apply
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from brave.api.routers.dedup import ResolveBody, resolve_pair
from brave.core.models import MarRecord, Municipio, RioRecord
from brave.core.rio.persist import persist_normalized
from brave.observability.audit import write_audit
from brave.tasks.pipeline import _get_session

ACTOR = "territorial_dedup_fixes"
# Everything PlacesEnrichmentAgent writes from a match (as scripts/reenrich_wrong_places.py).
# None, not popped: persist_normalized merges, and build_push_payload sends a key cleared to
# None as null so the API drops the wrong place.
_PLACE_KEYS = (
    "google_place_id",
    "place_id_cache",
    "weekday_text",
    "address",
    "phone",
    "website",
    "price_level",
    "signal",
    "google_fetched_at",
)
_DISTRITO_KEYS = ("distrito_name", "distrito_code", "distrito_municipio_ibge")
# The parent destino of the old município: without it the push falls back to ibge:<UF>:<new>.
_PARENT_KEYS = ("parent_rio_id", "parent_source_ref", "parent_mar_id")


def _rio(session: Session, source_ref: str) -> RioRecord | None:
    return session.scalar(select(RioRecord).where(RioRecord.canonical_key == source_ref))


def _active_mar(session: Session, source_ref: str) -> MarRecord | None:
    return session.scalar(
        select(MarRecord).where(
            MarRecord.source_ref == source_ref, MarRecord.superseded_by_id.is_(None)
        )
    )


def _merge(session: Session, entry: dict[str, Any], apply: bool) -> str:
    drop, keep = entry["drop"]["source_ref"], entry["keep"]["source_ref"]
    loser, mar = _rio(session, drop), _active_mar(session, keep)
    if loser is None or mar is None:
        return f"skip: {'drop' if loser is None else 'keep Mar'} not found"
    if loser.routing == "descarte":
        return "skip: already descarte"
    if mar.rio.routing != "mar":
        return f"skip: keep is routed {mar.rio.routing!r}"
    if apply:
        resolve_pair(loser.id, ResolveBody(action="merge", mar_id=mar.id), session)
    return "merged" if apply else "would merge"


def _null(after: dict[str, Any], keys: tuple[str, ...]) -> None:
    for key in keys:
        if after.get(key) is not None:
            after[key] = None


def _edit(
    session: Session,
    rio: RioRecord,
    after: dict[str, Any],
    municipio: Municipio | None,
    action: str,
    apply: bool,
) -> str:
    """Persist ``after`` (+ the município columns) and mark the record for the outbox."""
    before = rio.normalized or {}
    changed = sorted(k for k in after if before.get(k) != after[k])
    moved = municipio is not None and rio.municipio_id != municipio.ibge_code
    if not changed and not moved:
        return "skip: already applied"
    if apply:
        old = {k: before.get(k) for k in changed}
        old_municipio = rio.municipio_id
        persist_normalized(session, rio, before, after)
        if moved:
            assert municipio is not None
            rio.municipio_id, rio.uf = municipio.ibge_code, municipio.uf
        rio.processed_at = datetime.now(UTC)  # sync._needs_sync: re-push the snapshot
        write_audit(
            session,
            action=action,
            entity_type=rio.entity_type,
            record_id=rio.id,
            before_state={**old, "municipio_id": old_municipio},
            after_state={**{k: after[k] for k in changed}, "municipio_id": rio.municipio_id},
            actor=ACTOR,
        )
    return f"{'applied' if apply else 'would apply'}: {', '.join(changed)}"


def _fix(session: Session, fix: dict[str, Any], apply: bool) -> str:
    rio = _rio(session, fix["source_ref"])
    if rio is None:
        return "skip: not found"
    after = dict(rio.normalized or {})
    municipio = None
    if fix.get("municipio_ibge"):
        municipio = session.get(Municipio, fix["municipio_ibge"])
        if municipio is None:
            return f"skip: município {fix['municipio_ibge']} not in municipios"
        if rio.municipio_id != municipio.ibge_code:
            _null(after, _DISTRITO_KEYS + _PARENT_KEYS)
        after.update(municipio=municipio.nome, municipio_id=municipio.ibge_code)
    if fix.get("lat") is not None and fix.get("lng") is not None:
        after.update(lat=fix["lat"], lon=fix["lng"])
    if fix.get("name"):
        after["name"] = fix["name"]
    if fix.get("clear_place"):
        _null(after, _PLACE_KEYS)
        if after.get("distrito_source") == "places_admin_area_level_3":
            _null(after, _DISTRITO_KEYS)
    return _edit(session, rio, after, municipio, "territorial_fix", apply)


def _rename(session: Session, rename: dict[str, Any], apply: bool) -> str:
    rio = _rio(session, rename["source_ref"])
    if rio is None:
        return "skip: not found"
    after = {**(rio.normalized or {}), "name": rename["to"]}
    return _edit(session, rio, after, None, "territorial_rename", apply)


def _same_place_id(session: Session, apply: bool) -> list[RioRecord]:
    """Rio routed 'mar' with no active Mar whose place is in Mar in the same município."""
    pid = RioRecord.normalized["google_place_id"].as_string()
    active = (
        select(MarRecord.id)
        .where(MarRecord.rio_id == RioRecord.id, MarRecord.superseded_by_id.is_(None))
        .exists()
    )
    in_mar = {
        (municipio_id, place_id)
        for municipio_id, place_id in session.execute(
            select(RioRecord.municipio_id, pid).where(
                active, RioRecord.routing == "mar", RioRecord.entity_type == "attraction"
            )
        ).all()
        if municipio_id and place_id
    }
    twins = [
        rio
        for rio in session.scalars(
            select(RioRecord).where(
                ~active,
                RioRecord.routing == "mar",
                RioRecord.entity_type == "attraction",
                pid.isnot(None),
            )
        ).all()
        if (rio.municipio_id, (rio.normalized or {}).get("google_place_id")) in in_mar
    ]
    if apply:
        for rio in twins:
            rio.routing, rio.dlq_reason = "descarte", "dedup_same_place_id"
            write_audit(
                session,
                action="dedup_same_place_id",
                entity_type=rio.entity_type,
                record_id=rio.id,
                before_state={"routing": "mar"},
                after_state={"routing": "descarte"},
                actor=ACTOR,
            )
    return twins


def run(session: Session, manifest: dict[str, Any], apply: bool) -> dict[str, int]:
    """Run every action (dry run unless ``apply``), print one line per record, return counts."""
    counts = {"merge": 0, "fix": 0, "rename": 0, "same_place_id": 0, "skipped": 0}

    def report(action: str, ref: str, outcome: str) -> None:
        counts["skipped" if outcome.startswith("skip") else action] += 1
        print(f"[{action}] {ref}: {outcome}")

    for entry in manifest.get("attractions", []):
        refs = (entry["drop"].get("source_ref") or "", entry["keep"].get("source_ref") or "")
        if all(r.startswith("tripadvisor:") for r in refs):
            report("merge", " -> ".join(refs), _merge(session, entry, apply))
    for fix in manifest.get("brave_fixes", []):
        report("fix", fix["source_ref"], _fix(session, fix, apply))
    for rename in manifest.get("renames", []):
        report("rename", rename["source_ref"], _rename(session, rename, apply))
    for rio in _same_place_id(session, apply):
        report(
            "same_place_id",
            rio.canonical_key or str(rio.id),
            "descarte" if apply else "would descarte",
        )
    if apply:
        session.commit()
    print(f"{'APPLIED' if apply else 'DRY RUN'}: {counts}")
    return counts


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Apply a territorial dedup manifest.")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    with open(args.manifest, encoding="utf-8") as fh:
        manifest = json.load(fh)
    session, _ = _get_session()
    try:
        run(session, manifest, args.apply)
    finally:
        session.rollback()
        session.close()


if __name__ == "__main__":
    main()
