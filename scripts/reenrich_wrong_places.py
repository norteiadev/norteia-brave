"""Revert a wrong Google Places match on specific atrativos and re-run the enrichment.

Before 2026-09-18 a coordless atrativo matched Places by name alone, so some records carry
the place_id, coords, address, hours and contacts of a same-name place in another state (or
of a river). This strips every Places-derived key, restores the TA-side values from the
Nascente payload, logs the revert (audit + Log tab), then re-runs PlacesEnrichmentAgent —
which now only accepts a place inside the UF when the record has no coords.

Usage (inside the worker container, which has the keys and RUN_REAL_EXTERNALS):
    python scripts/reenrich_wrong_places.py <rio_id> [<rio_id> ...]            # dry run
    python scripts/reenrich_wrong_places.py --apply <rio_id> [<rio_id> ...]
    python scripts/reenrich_wrong_places.py --apply --recheck <rio_id> ...  # re-evaluate a
        Places descarte (e.g. closed_place from before CLOSED_TEMPORARILY went to the DLQ)
"""

from __future__ import annotations

import sys
import uuid

from sqlalchemy.orm.attributes import flag_modified

from brave.core.models import NascenteRecord, RioRecord
from brave.observability.audit import write_audit
from brave.observability.record_events import record_event
from brave.shared.opening_hours import to_hours_map
from brave.tasks.pipeline import _enrich_one, _get_session

# Every key PlacesEnrichmentAgent writes from a Places match (description keys excluded:
# the description lane is off and none of these records has one).
_PLACES_KEYS = (
    "google_enriched", "google_place_id", "place_id_cache", "weekday_text", "address",
    "phone", "website", "price_level", "signal",
)
_PLACES_DISTRITO_KEYS = (
    "distrito_name", "distrito_code", "distrito_municipio_ibge", "subdistrito_name",
    "subdistrito_code", "distrito_source",
)
# Places overwrote these; the TA-side value lives in the Nascente payload.
_RESTORE_FROM_PAYLOAD = {"lat": "lat", "lon": "lng", "atualidade_value": "atualidade_value",
                         "most_recent_review_at": "most_recent_review_at"}


def _reverted(normalized: dict, payload: dict) -> dict:
    out = {k: v for k, v in normalized.items() if k not in _PLACES_KEYS}
    if out.get("distrito_source") == "places_admin_area_level_3":
        out = {k: v for k, v in out.items() if k not in _PLACES_DISTRITO_KEYS}
    for key, pkey in _RESTORE_FROM_PAYLOAD.items():
        if payload.get(pkey) is None:
            out.pop(key, None)
        else:
            out[key] = payload[pkey]
    return out


def main(argv: list[str]) -> None:
    apply = "--apply" in argv
    recheck = "--recheck" in argv
    ids = [a for a in argv if a not in ("--apply", "--recheck")]
    session, _ = _get_session()
    try:
        for rid in ids:
            rio = session.get(RioRecord, uuid.UUID(rid), with_for_update=True)
            nascente = session.get(NascenteRecord, rio.nascente_id)
            old = dict(rio.normalized or {})
            new = _reverted(old, nascente.payload or {})
            wrong = {k: old.get(k) for k in ("google_place_id", "address", "lat", "lon")}
            print(f"\n{old.get('name')} [{rio.uf}] score={rio.score} "
                  f"routing={rio.routing}\n  wrong: {wrong}\n  removed: "
                  f"{sorted(set(old) - set(new))}")
            if not apply:
                continue

            rio.normalized = new
            flag_modified(rio, "normalized")
            if recheck and rio.routing == "descarte":
                rio.routing, rio.dlq_reason = "dlq", None  # the enrichment decides again
            write_audit(
                session=session, action="places_match_reverted", entity_type="attraction",
                record_id=rio.id, actor="reenrich_wrong_places",
                before_state={k: old.get(k) for k in sorted(set(old) - set(new))},
                after_state={
                    "reason": "recheck" if recheck else "place_outside_uf_or_geographic_feature"
                },
            )
            record_event(
                session=session, source="tripadvisor", source_ref=rio.canonical_key or "",
                stage="places_match_reverted", status="fail",
                message=(
                    "Reavaliação no Google Places (descarte anterior por fechamento)"
                    if recheck
                    else f"Match do Google Places desfeito — lugar errado: {old.get('address')}"
                ),
                entity_type="attraction", uf=rio.uf, rio_id=rio.id,
                data={"wrong_place_id": old.get("google_place_id"),
                      "wrong_address": old.get("address")},
            )
            session.flush()

            _enrich_one(session, rio)
            session.commit()
            n = rio.normalized or {}
            print(f"  now: place_id={n.get('google_place_id')} address={n.get('address')} "
                  f"lat={n.get('lat')} score={rio.score} routing={rio.routing}\n"
                  f"  hours={to_hours_map(n.get('weekday_text'))} "
                  f"raw={n.get('weekday_text')}")
    finally:
        session.rollback()
        session.close()


if __name__ == "__main__":
    main(sys.argv[1:])
