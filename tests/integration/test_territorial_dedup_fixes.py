"""scripts/territorial_dedup_fixes.py against Postgres (norteia_brave_test).

commit is swapped for flush, so every row (and the merge endpoint's own commit) rolls back
with the db_session fixture. Records are asserted by id: the test DB keeps other tests' rows.
"""

from __future__ import annotations

import importlib.util
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from brave.api.routers.atrativos import _active_mar_keys
from brave.config.settings import ScoreConfig
from brave.core.mar import sync
from brave.core.mar.service import build_push_payload, upsert_mar_snapshot
from brave.core.models import AuditLog, MarRecord, Municipio, RioRecord
from brave.core.nascente.service import store_raw
from brave.core.rio.normalize import name_key
from brave.core.rio.routing import process_nascente_record

pytestmark = pytest.mark.integration

_path = Path(__file__).resolve().parents[2] / "scripts" / "territorial_dedup_fixes.py"
_spec = importlib.util.spec_from_file_location("territorial_dedup_fixes", _path)
assert _spec and _spec.loader
fixes = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fixes)

_LONG_AGO = datetime.now(UTC) - timedelta(days=2)


@pytest.fixture
def db(db_session, monkeypatch):
    def commit() -> None:
        db_session.info["commits"] = db_session.info.get("commits", 0) + 1
        db_session.flush()

    monkeypatch.setattr(db_session, "commit", commit)
    return db_session


def _ta(db, *, name="Praia", municipio_id="2902005", in_mar=True, **normalized) -> RioRecord:
    """A TA atrativo in the Rio (routing 'mar'), with an already-pushed Mar row if in_mar."""
    ref = f"tripadvisor:attraction:t{uuid.uuid4().hex[:10]}"
    payload = {
        "name": name,
        "municipio_id": municipio_id,
        "uf": "BA",
        "review_count": 30,
        "locationId": ref,
    }
    nascente = store_raw(db, "tripadvisor", ref, "attraction", "BA", payload)
    db.flush()
    rio = process_nascente_record(db, nascente, ScoreConfig())
    rio.normalized = {**(rio.normalized or {}), **normalized}
    rio.routing, rio.dlq_reason, rio.score = "mar", None, 85
    rio.processed_at = _LONG_AGO
    db.flush()
    if in_mar:
        mar = upsert_mar_snapshot(db, rio)
        mar.push_hash, mar.pushed_at, mar.push_attempted_at = "h", _LONG_AGO, _LONG_AGO
        mar.published_at = _LONG_AGO
        db.flush()
    return rio


def _mar(db, rio: RioRecord) -> MarRecord:
    mar = db.scalars(
        select(MarRecord).where(MarRecord.rio_id == rio.id, MarRecord.superseded_by_id.is_(None))
    ).one()
    return mar


def _pending(db) -> set:
    return {rio_id for rio_id, _ in sync.pending_push_rows(db, limit=100_000)}


def _manifest(**sections) -> dict:
    return {"attractions": [], "brave_fixes": [], "renames": [], **sections}


def test_merge_discards_the_loser_and_records_it_on_the_kept_mar(db):
    keep = _ta(db, name="Praia do Francês")
    drop = _ta(db, name="Praia do Frances")
    entry = {"drop": {"source_ref": drop.canonical_key}, "keep": {"source_ref": keep.canonical_key}}
    seed = {"drop": {"source_ref": None}, "keep": {"source_ref": keep.canonical_key}}
    manifest = _manifest(attractions=[seed, entry])

    dry = fixes.run(db, manifest, apply=False)
    assert dry["merge"] == 1 and drop.routing == "mar"

    report = fixes.run(db, manifest, apply=True)

    assert report["merge"] == 1
    assert db.info["commits"] == 1  # one transaction: a later failure rolls the merge back
    db.expire_all()
    assert drop.routing == "descarte"
    assert _mar(db, keep).provenance["merged_source_refs"] == [drop.canonical_key]
    assert drop.id in _pending(db)  # the outbox pushes it hidden, then retracts it
    audit = db.scalars(select(AuditLog).where(AuditLog.record_id == drop.id)).one()
    assert (audit.action, audit.actor) == ("dedup_merged", fixes.ACTOR)
    # Idempotent: a second run skips the loser and merges nothing twice.
    assert fixes.run(db, manifest, apply=True)["merge"] == 0
    assert _mar(db, keep).provenance["merged_source_refs"] == [drop.canonical_key]
    # A later write to the keep (a rename) re-derives its Mar: the merge record survives.
    rename = {"source_ref": keep.canonical_key, "from": "Praia do Francês", "to": "Praia Francês"}
    fixes.run(db, _manifest(renames=[rename]), apply=True)
    assert upsert_mar_snapshot(db, keep).provenance["merged_source_refs"] == [drop.canonical_key]


def test_brave_fix_moves_the_municipio_sets_coords_clears_the_place_and_repushes(db):
    db.add(Municipio(ibge_code="2923506", nome="Palmeiras", uf="BA", lat=-12.5, lng=-41.6))
    rio = _ta(
        db,
        name="Morro do Pai Inácio",
        municipio_id="2901601",
        municipio="Andaraí",
        google_place_id="ChIJwrong",
        place_id_cache="ChIJwrong",
        weekday_text=["seg: 8h"],
        parent_source_ref="ibge:BA:2901601",
        distrito_name="Igatu",
        subdistrito_name="Xique-Xique",
        distrito_source="places_admin_area_level_3",
        lat=-12.0,
        lon=-41.0,
    )
    fix = {
        "source_ref": rio.canonical_key,
        "municipio_ibge": "2923506",
        "lat": -12.4575,
        "lng": -41.4731,
        "name": "Morro do Pai Inácio (Palmeiras)",
        "clear_place": True,
    }

    assert fixes.run(db, _manifest(brave_fixes=[fix]), apply=False)["fix"] == 1
    assert rio.municipio_id == "2901601"

    assert fixes.run(db, _manifest(brave_fixes=[fix]), apply=True)["fix"] == 1

    db.expire_all()
    n = rio.normalized or {}
    assert rio.municipio_id == "2923506"
    assert (n["municipio"], n["municipio_id"]) == ("Palmeiras", "2923506")
    assert (n["lat"], n["lon"], n["name"]) == (
        -12.4575,
        -41.4731,
        "Morro do Pai Inácio (Palmeiras)",
    )
    assert n["google_place_id"] is None and n["place_id_cache"] is None
    assert n["parent_source_ref"] is None
    assert n["distrito_name"] is n["subdistrito_name"] is n["distrito_source"] is None
    assert rio.id in _pending(db)
    payload = build_push_payload(upsert_mar_snapshot(db, rio), rio)
    assert payload["municipio_ibge"] == "2923506"
    assert payload["destino"]["source_ref"] == "ibge:BA:2923506"
    assert payload["place"]["place_id"] is None  # sent as null: the API drops the wrong place
    assert fixes.run(db, _manifest(brave_fixes=[fix]), apply=True)["fix"] == 0


def test_clear_place_without_coords_is_skipped(db):
    """Places overwrote lat/lon with the wrong place's: clearing it alone keeps the wrong pin."""
    rio = _ta(db, google_place_id="ChIJwrong", lat=-29.868, lon=-50.068)
    fix = {"source_ref": rio.canonical_key, "clear_place": True}

    assert fixes.run(db, _manifest(brave_fixes=[fix]), apply=True)["fix"] == 0

    db.expire_all()
    assert (rio.normalized or {})["google_place_id"] == "ChIJwrong"


def test_rename_sets_the_name_and_a_resweep_keeps_it(db):
    rio = _ta(db, name="Gruta Lapão")
    rename = {"source_ref": rio.canonical_key or "", "from": "Gruta Lapão", "to": "Gruta do Lapão"}

    assert fixes.run(db, _manifest(renames=[rename]), apply=True)["rename"] == 1

    db.expire_all()
    assert (rio.normalized or {})["name"] == "Gruta do Lapão"
    assert rio.id in _pending(db)
    # A TA re-sweep (new Nascente version of the same card) keeps the manual name:
    # _apply_new_version only takes a key nobody changed since the previous version.
    v2 = store_raw(
        db,
        "tripadvisor",
        rename["source_ref"],
        "attraction",
        "BA",
        {"name": "Gruta Lapão", "municipio_id": "2902005", "uf": "BA", "review_count": 31},
    )
    db.flush()
    process_nascente_record(db, v2, ScoreConfig())
    db.expire_all()
    n = rio.normalized or {}
    assert (n["name"], n["review_count"]) == ("Gruta do Lapão", 31)
    assert fixes.run(db, _manifest(renames=[rename]), apply=True)["rename"] == 0


def test_mar_routed_rio_without_mar_sharing_a_place_id_in_the_municipio_is_discarded(db):
    _ta(db, google_place_id="ChIJsame")
    twin = _ta(db, google_place_id="ChIJsame", in_mar=False)
    elsewhere = _ta(db, google_place_id="ChIJsame", municipio_id="2919306", in_mar=False)
    other = _ta(db, google_place_id="ChIJother", in_mar=False)

    fixes.run(db, _manifest(), apply=False)
    assert twin.routing == "mar"

    report = fixes.run(db, _manifest(), apply=True)

    assert report["same_place_id"] >= 1
    assert (twin.routing, twin.dlq_reason) == ("descarte", "dedup_same_place_id")
    assert elsewhere.routing == other.routing == "mar"


def test_same_place_id_dry_run_sees_the_state_after_merges_and_fixes(db, capsys):
    """The dry run lists exactly what --apply discards, though it writes nothing first."""
    db.add(Municipio(ibge_code="2923506", nome="Palmeiras", uf="BA", lat=-12.5, lng=-41.6))
    cleared = _ta(db, google_place_id="ChIJcleared")
    kept_twin = _ta(db, google_place_id="ChIJcleared", in_mar=False)  # the place's true card
    moved = _ta(db, google_place_id="ChIJmoved")
    new_twin = _ta(db, google_place_id="ChIJmoved", municipio_id="2923506", in_mar=False)
    manifest = _manifest(
        brave_fixes=[
            {"source_ref": cleared.canonical_key, "lat": -12.1, "lng": -41.1, "clear_place": True},
            {"source_ref": moved.canonical_key, "municipio_ibge": "2923506"},
        ]
    )

    fixes.run(db, manifest, apply=False)
    dry = capsys.readouterr().out
    fixes.run(db, manifest, apply=True)
    applied = capsys.readouterr().out

    for out in (dry, applied):
        assert f"[same_place_id] {new_twin.canonical_key}:" in out
        assert f"[same_place_id] {kept_twin.canonical_key}:" not in out
    assert (new_twin.routing, kept_twin.routing) == ("descarte", "mar")


def test_active_mar_keys_reads_name_keys_of_active_mar_attractions_only(db):
    tag = uuid.uuid4().hex[:8]
    live = _ta(db, name=f"Praia do Forte {tag}")
    merged = _ta(db, name=f"Gruta {tag}")
    merged.routing = "descarte"  # merged loser whose Mar the outbox has not retracted yet
    db.flush()

    _, name_keys = _active_mar_keys(db)

    assert (live.municipio_id, name_key(f"Praia Forte {tag}")) in name_keys
    assert (merged.municipio_id, name_key(f"Gruta {tag}")) not in name_keys
