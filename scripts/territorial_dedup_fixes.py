"""Apply the Brave side of a territorial dedup manifest (territorial-dedup-2026-10.json).

Four actions, each idempotent (a record already in the target state is skipped):

  merge          manifest.attractions where drop AND keep are TripAdvisor cards: the
                 Duplicados merge (PATCH /api/v1/dedup/pairs/{rio_id}/resolve) — loser Rio →
                 descarte, its source_ref recorded on the kept Mar. The outbox then pushes
                 the loser as visibility=hidden and stamps retracted_at.
  fix            manifest.brave_fixes: município (IBGE code, re-linked to its destino),
                 lat/lng, name; clear_place nulls the cached Google Places data (pushed as
                 null, so the API drops it) and needs lat/lng: Places had moved the pin to
                 the wrong place.
  rename         manifest.renames: the display name. A TA re-sweep keeps it — the Nascente
                 version merge only takes a key nobody changed since the previous version.
  same_place_id  every Rio routed 'mar' with no active Mar whose google_place_id is already
                 in Mar in the same município → descarte (dedup_same_place_id). Judged on the
                 state after this run's merges and fixes — in the dry run too.

fix/rename stamp processed_at, so the outbox (sync._needs_sync) re-derives and re-pushes.
--apply commits once, at the end: a failure midway writes nothing.

Usage (worker container, after `docker cp <manifest> norteia-brave-worker-1:/tmp/`, or host
with BRAVE_DB_URL):
    python scripts/territorial_dedup_fixes.py --manifest /tmp/territorial-dedup-2026-10.json
    python scripts/territorial_dedup_fixes.py --manifest ... --apply
"""

from __future__ import annotations

import argparse
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from brave.api.routers.dedup import merge_into_mar
from brave.config.settings import ScoreConfig
from brave.core.models import MarRecord, Municipio, RioRecord
from brave.core.rio.persist import persist_normalized
from brave.observability.audit import write_audit
from brave.shared.destino import ensure_destino
from brave.tasks.pipeline import _get_session

ACTOR = "territorial_dedup_fixes"
# What PlacesEnrichmentAgent writes from a match, but google_enriched: kept true, no Places
# lane re-enriches the record and re-matches the homonym (unlike reenrich_wrong_places.py).
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
_DISTRITO_KEYS = (
    "distrito_name",
    "distrito_code",
    "distrito_municipio_ibge",
    "subdistrito_name",
    "subdistrito_code",
    "distrito_source",
)
# rio_id → (municipio_id, google_place_id) once this run's merges/fixes land; None = out of Mar.
Planned = dict[uuid.UUID, tuple[str | None, str | None] | None]


def _rio(session: Session, source_ref: str) -> RioRecord | None:
    return session.scalar(select(RioRecord).where(RioRecord.canonical_key == source_ref))


def _active_mar(session: Session, source_ref: str) -> MarRecord | None:
    return session.scalar(
        select(MarRecord).where(
            MarRecord.source_ref == source_ref, MarRecord.superseded_by_id.is_(None)
        )
    )


def _merge(session: Session, entry: dict[str, Any], planned: Planned, apply: bool) -> str:
    drop, keep = entry["drop"]["source_ref"], entry["keep"]["source_ref"]
    loser, mar = _rio(session, drop), _active_mar(session, keep)
    if loser is None or mar is None:
        return f"skip: {'drop' if loser is None else 'keep Mar'} not found"
    if loser.id == mar.rio_id:
        return "skip: drop and keep are the same record"
    if loser.routing == "descarte":
        return "skip: already descarte"
    if mar.rio.routing != "mar":
        return f"skip: keep is routed {mar.rio.routing!r}"
    planned[loser.id] = None
    if apply:
        before_routing = loser.routing
        merge_into_mar(loser, mar)
        write_audit(
            session,
            action="dedup_merged",
            entity_type=loser.entity_type,
            record_id=loser.id,
            before_state={"routing": before_routing},
            after_state={"routing": "descarte", "mar_id": str(mar.id)},
            actor=ACTOR,
        )
    return "merged" if apply else "would merge"


def _null(after: dict[str, Any], keys: tuple[str, ...]) -> None:
    for key in keys:
        if after.get(key) is not None:
            after[key] = None


def _parent(session: Session, municipio: Municipio, apply: bool) -> dict[str, str | None]:
    """The new município's destino linkage, set explicitly: a key left None would let a TA
    re-sweep of the card (still listed in the old município) restore the old parent, since
    the Nascente version merge takes a key whose current value equals the previous version's.
    --apply ensures the destino (as the TA lane does); the dry run only looks it up.
    """
    ref = f"ibge:{municipio.uf}:{municipio.ibge_code}"
    if apply:
        rio_id, ref, mar_id = ensure_destino(
            session,
            ScoreConfig(),
            ibge_code=municipio.ibge_code,
            nome=municipio.nome,
            uf=municipio.uf,
        )
    else:
        rio_id = session.scalar(select(RioRecord.id).where(RioRecord.canonical_key == ref))
        mar = _active_mar(session, ref)
        mar_id = mar.id if mar else None
    return {
        "parent_rio_id": str(rio_id) if rio_id else None,
        "parent_source_ref": ref,
        "parent_mar_id": str(mar_id) if mar_id else None,
    }


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


def _fix(session: Session, fix: dict[str, Any], planned: Planned, apply: bool) -> str:
    rio = _rio(session, fix["source_ref"])
    if rio is None:
        return "skip: not found"
    coords = fix.get("lat") is not None and fix.get("lng") is not None
    if fix.get("clear_place") and not coords:
        return "skip: clear_place without lat/lng (the pin is the wrong place's)"
    after = dict(rio.normalized or {})
    municipio = None
    if fix.get("municipio_ibge"):
        municipio = session.get(Municipio, fix["municipio_ibge"])
        if municipio is None:
            return f"skip: município {fix['municipio_ibge']} not in municipios"
        if rio.municipio_id != municipio.ibge_code:
            _null(after, _DISTRITO_KEYS)
            after.update(_parent(session, municipio, apply))
        after.update(municipio=municipio.nome, municipio_id=municipio.ibge_code)
    if coords:
        after.update(lat=fix["lat"], lon=fix["lng"])
    if fix.get("name"):
        after["name"] = fix["name"]
    if fix.get("clear_place"):
        _null(after, _PLACE_KEYS)
        if after.get("distrito_source") == "places_admin_area_level_3":
            _null(after, _DISTRITO_KEYS)
    planned[rio.id] = (
        municipio.ibge_code if municipio else rio.municipio_id,
        after.get("google_place_id"),
    )
    return _edit(session, rio, after, municipio, "territorial_fix", apply)


def _rename(session: Session, rename: dict[str, Any], apply: bool) -> str:
    rio = _rio(session, rename["source_ref"])
    if rio is None:
        return "skip: not found"
    after = {**(rio.normalized or {}), "name": rename["to"]}
    return _edit(session, rio, after, None, "territorial_rename", apply)


def _same_place_id(session: Session, planned: Planned, apply: bool) -> list[RioRecord]:
    """Rio routed 'mar' with no active Mar whose place is in Mar in the same município.

    ``planned`` overlays this run's merges/fixes, which a dry run never writes.
    """
    pid = RioRecord.normalized["google_place_id"].as_string()
    active = (
        select(MarRecord.id)
        .where(MarRecord.rio_id == RioRecord.id, MarRecord.superseded_by_id.is_(None))
        .exists()
    )
    in_mar = {
        planned.get(rio_id, (municipio_id, place_id))
        for rio_id, municipio_id, place_id in session.execute(
            select(RioRecord.id, RioRecord.municipio_id, pid).where(
                active, RioRecord.routing == "mar", RioRecord.entity_type == "attraction"
            )
        ).all()
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
        if (
            key := planned.get(
                rio.id, (rio.municipio_id, (rio.normalized or {}).get("google_place_id"))
            )
        )
        and all(key)
        and key in in_mar
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
    planned: Planned = {}

    def report(action: str, ref: str, outcome: str) -> None:
        counts["skipped" if outcome.startswith("skip") else action] += 1
        print(f"[{action}] {ref}: {outcome}")

    for entry in manifest.get("attractions", []):
        refs = (entry["drop"].get("source_ref") or "", entry["keep"].get("source_ref") or "")
        if all(r.startswith("tripadvisor:") for r in refs):
            report("merge", " -> ".join(refs), _merge(session, entry, planned, apply))
    for fix in manifest.get("brave_fixes", []):
        report("fix", fix["source_ref"], _fix(session, fix, planned, apply))
    for rename in manifest.get("renames", []):
        report("rename", rename["source_ref"], _rename(session, rename, apply))
    for rio in _same_place_id(session, planned, apply):
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
