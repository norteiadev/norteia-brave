"""Editorial resolver against the test DB (SAVEPOINT, rolled back): matches claims to Rio
records, resolves one value per column, writes ONLY ``normalized["editorial"]`` and bumps
``processed_at`` ONLY when that dict changed. UF "ZZ" keeps it apart from real data."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from brave.core.mar.service import upsert_mar_snapshot
from brave.core.models import (
    EditorialClaim,
    EditorialDestination,
    EditorialDocument,
    Municipio,
    NascenteRecord,
    RioRecord,
)
from brave.domains.editorial.resolver import resolve_uf

pytestmark = pytest.mark.integration

UF = "ZZ"
IBGE = "9990001"
LAT, LON = -20.1000, -40.5000
NEAR = (LAT - 0.0009, LON)  # ~100 m


@pytest.fixture
def session(db_engine):
    conn = db_engine.connect()
    outer = conn.begin()
    s = Session(bind=conn, join_transaction_mode="create_savepoint")
    try:
        yield s
    finally:
        s.close()
        outer.rollback()
        conn.close()


def rio(
    session, key, name, at=NEAR, municipio="Pancas", ibge=IBGE, parent=None, routing="mar", **extra
):
    n = NascenteRecord(
        id=uuid.uuid4(), source="t", source_ref=key, entity_type="attraction", uf=UF,
        payload={}, content_hash=f"h:{key}", version=1,
    )  # fmt: skip
    session.add(n)
    session.flush()
    normalized = {
        "name": name, "lat": at[0] if at else None, "lon": at[1] if at else None,
        "municipio": municipio, "municipio_id": ibge, "origem_value": 60.0, **extra,
    }  # fmt: skip
    if parent:
        normalized["parent_source_ref"] = parent
    r = RioRecord(
        nascente_id=n.id, canonical_key=key, entity_type="attraction", uf=UF, municipio_id=ibge,
        routing=routing, normalized=normalized, score=80,
    )  # fmt: skip
    session.add(r)
    session.flush()
    return r


def doc(session, slug, published=date(2020, 2, 17), points=None):
    d = EditorialDocument(
        url=f"https://www.viagensecaminhos.com/{slug}/", source="viagensecaminhos", uf=UF,
        content_hash=slug.ljust(64, "0"), body_text="x", published_time=published,
        kml_points=points if points is not None else [{"name": "Pedra do Frade", "lat": LAT, "lon": LON}],
        extracted_at=datetime.now(UTC),
    )  # fmt: skip
    session.add(d)
    session.flush()
    return d


def claim(session, d, field, value, kind="atrativo", name="Pedra do Frade", municipio="Pancas",
          stability="estavel", status="unmatched"):  # fmt: skip
    c = EditorialClaim(
        id=uuid.uuid4(), document_url=d.url, source=d.source, entity_kind=kind, entity_name=name,
        municipio_text=municipio, field=field, value=value, stability=stability, evidence="x",
        published_time=d.published_time, status=status,
    )  # fmt: skip
    session.add(c)
    session.flush()
    return c


def fresh(session, row):
    session.expire(row)
    return row


# -- attractions --------------------------------------------------------------------------


def test_a_claim_is_matched_by_coordinate_and_written_under_the_editorial_key(session) -> None:
    r = rio(session, "ta:1", "Pedra do Frade")
    d = doc(session, "p1")
    c = claim(session, d, "curiosities", "Tombada em 1940.")
    result = resolve_uf(session, UF)

    fresh(session, c)
    assert (c.status, c.canonical_key, c.match_method, c.match_score) == (
        "matched",
        "ta:1",
        "kml_coord",
        1.0,
    )
    assert (c.lat, c.lon) == (LAT, LON)
    fresh(session, r)
    assert r.normalized["editorial"] == {"curiosities": "Tombada em 1940."}
    assert (
        r.normalized["origem_value"] == 60.0 and r.normalized["name"] == "Pedra do Frade"
    )  # untouched
    assert r.processed_at is not None
    assert result.attractions_written == 1


def test_the_resolver_is_idempotent_and_leaves_the_mar_alone(session) -> None:
    r = rio(session, "ta:1", "Pedra do Frade")
    claim(session, doc(session, "p1"), "curiosities", "Tombada em 1940.")
    resolve_uf(session, UF)
    fresh(session, r)
    stamp = r.processed_at
    mar = upsert_mar_snapshot(session, r)
    assert mar.canonical["editorial"] == {"curiosities": "Tombada em 1940."}

    second = resolve_uf(session, UF)
    fresh(session, r)
    assert r.processed_at == stamp and second.attractions_written == 0
    assert upsert_mar_snapshot(session, r).id == mar.id  # no supersession: nothing to republish
    assert mar.superseded_by_id is None


def test_the_newest_post_wins_per_column_and_a_change_bumps_processed_at(session) -> None:
    r = rio(session, "ta:1", "Pedra do Frade")
    old = doc(session, "old", published=date(2019, 1, 1))
    new = doc(session, "new", published=date(2022, 6, 1))
    claim(session, old, "curiosities", "Fato antigo.")
    claim(session, old, "tips", "Dica só do post antigo.")
    claim(session, new, "curiosities", "Fato recente.")
    resolve_uf(session, UF)
    fresh(session, r)
    assert r.normalized["editorial"] == {
        "curiosities": "Fato recente.",
        "tips": "Dica só do post antigo.",
    }

    first = r.processed_at
    claim(session, doc(session, "newest", published=date(2024, 1, 1)), "tips", "Dica nova.")
    resolve_uf(session, UF)
    fresh(session, r)
    assert r.normalized["editorial"]["tips"] == "Dica nova." and r.processed_at > first


def test_volatile_claims_are_held_and_never_written(session) -> None:
    r = rio(session, "ta:1", "Pedra do Frade")
    d = doc(session, "p1")
    held = claim(session, d, "price", "R$ 10", stability="volatil", status="held_volatile")
    claim(session, d, "curiosities", "Fato.")
    resolve_uf(session, UF)
    fresh(session, r)
    fresh(session, held)
    assert r.normalized["editorial"] == {"curiosities": "Fato."}
    assert held.status == "held_volatile" and held.canonical_key is None


def test_two_candidates_make_the_claims_ambiguous_and_nothing_is_written(session) -> None:
    a = rio(session, "ta:a", "Pedra do Frade", at=NEAR)
    b = rio(session, "ta:b", "Pedra do Frade", at=(LAT - 0.002, LON))
    c = claim(session, doc(session, "p1"), "curiosities", "Fato.")
    result = resolve_uf(session, UF)
    fresh(session, c)
    assert c.status == "ambiguous" and c.canonical_key is None
    assert (
        "editorial" not in fresh(session, a).normalized
        and "editorial" not in fresh(session, b).normalized
    )
    assert result.claims["ambiguous"] == 1


def test_no_candidate_is_unmatched_and_kept_for_discovery(session) -> None:
    c = claim(session, doc(session, "p1"), "curiosities", "Fato.")
    resolve_uf(session, UF)
    fresh(session, c)
    assert c.status == "unmatched" and c.canonical_key is None and (c.lat, c.lon) == (LAT, LON)


def test_the_municipality_in_the_text_never_decides_alone(session) -> None:
    # same name, right municipality text, but the map point is 5 km from the Brave record
    rio(session, "ta:far", "Pedra do Frade", at=(LAT - 0.045, LON))
    c = claim(session, doc(session, "p1"), "curiosities", "Fato.", municipio="Pancas")
    resolve_uf(session, UF)
    assert fresh(session, c).status == "unmatched"


def test_name_only_inside_the_municipality_when_the_post_has_no_map(session) -> None:
    r = rio(session, "ta:1", "Pedra do Frade", at=None)
    c = claim(session, doc(session, "nomap", points=[]), "curiosities", "Fato.")
    resolve_uf(session, UF)
    assert fresh(session, c).match_method == "name_municipio"
    assert fresh(session, r).normalized["editorial"] == {"curiosities": "Fato."}


def test_discarded_records_are_not_candidates(session) -> None:
    rio(session, "ta:gone", "Pedra do Frade", routing="descarte")
    c = claim(session, doc(session, "p1"), "curiosities", "Fato.")
    resolve_uf(session, UF)
    assert fresh(session, c).status == "unmatched"


def test_a_dlq_record_gets_the_field_too_so_it_travels_when_promoted(session) -> None:
    r = rio(session, "ta:dlq", "Pedra do Frade", routing="dlq")
    claim(session, doc(session, "p1"), "curiosities", "Fato.")
    resolve_uf(session, UF)
    assert fresh(session, r).normalized["editorial"] == {"curiosities": "Fato."}


def test_editorial_that_no_longer_has_support_is_removed_and_the_change_is_stamped(session) -> None:
    r = rio(session, "ta:1", "Pedra do Frade")
    c = claim(session, doc(session, "p1"), "curiosities", "Fato.")
    resolve_uf(session, UF)
    fresh(session, r)
    first = r.processed_at
    session.delete(c)
    session.flush()
    resolve_uf(session, UF)
    fresh(session, r)
    assert "editorial" not in r.normalized and r.normalized["name"] == "Pedra do Frade"
    assert r.processed_at > first


def test_records_of_other_ufs_are_never_touched(session) -> None:
    other = rio(session, "ta:other", "Pedra do Frade")
    other.uf = "ZY"
    session.flush()
    claim(session, doc(session, "p1"), "curiosities", "Fato.")
    resolve_uf(session, UF)
    assert "editorial" not in fresh(session, other).normalized


# -- destinations -------------------------------------------------------------------------


def municipio(session, ibge=IBGE, nome="Pancas"):
    session.add(Municipio(ibge_code=ibge, nome=nome, uf=UF, lat=LAT, lng=LON))
    session.flush()


def dest_claims(session, d, **fields):
    for field, value in fields.items():
        claim(session, d, field, value, kind="destino", name="Pancas", municipio="Pancas")


def test_destination_fields_land_in_the_outbox_with_the_api_source_ref(session) -> None:
    municipio(session)
    rio(session, "ta:1", "Pedra do Frade", parent="ibge:ZZ:9990001")
    rio(session, "ta:2", "Gruta Azul", parent="ibge:ZZ:9990001", at=None)
    d = doc(session, "pancas")
    dest_claims(session, d, best_time_to_visit="De maio a agosto.", seasonality=["chuva de out a jan"],
                pace_profile="lento")  # fmt: skip
    claim(session, d, "safety_alerts", "Cuidado.", kind="destino", name="Pancas",
          stability="volatil", status="held_volatile")  # fmt: skip
    result = resolve_uf(session, UF)

    row = session.get(EditorialDestination, "ibge:ZZ:9990001")
    assert row.fields == {"best_time_to_visit": "De maio a agosto.", "seasonality": ["chuva de out a jan"],
                          "pace_profile": "lento"}  # fmt: skip
    assert (row.uf, row.municipio_ibge, row.tourist_name) == (UF, IBGE, "Pancas")
    assert row.pushed_at is None and row.push_hash is None
    assert result.destinations_changed == 1
    c = session.scalars(select(EditorialClaim).where(EditorialClaim.field == "pace_profile")).one()
    assert (c.status, c.canonical_key, c.match_method) == ("matched", IBGE, "municipio_ibge")


def test_destination_source_ref_prefers_what_the_attractions_carry_else_the_ibge_ref(
    session,
) -> None:
    municipio(session)
    rio(session, "ta:1", "Pedra do Frade", parent="mtur:ZZ:42")
    dest_claims(session, doc(session, "pancas"), tips="Chegue cedo.")
    resolve_uf(session, UF)
    assert session.get(EditorialDestination, "mtur:ZZ:42") is not None
    assert session.get(EditorialDestination, "ibge:ZZ:9990001") is None

    session.rollback()  # fresh scenario: no attraction knows the destination
    municipio(session, "9990002", "Colatina")
    d2 = doc(session, "colatina")
    claim(session, d2, "tips", "Leve água.", kind="destino", name="Colatina", municipio="Colatina")
    resolve_uf(session, UF)
    only = session.get(EditorialDestination, "ibge:ZZ:9990002")
    assert only is not None and only.tourist_name == "Colatina"  # the municipios table name


def test_destination_resolver_is_idempotent_and_a_change_makes_it_pending_again(session) -> None:
    municipio(session)
    d = doc(session, "pancas", published=date(2020, 1, 1))
    dest_claims(session, d, tips="Chegue cedo.")
    resolve_uf(session, UF)
    row = session.get(EditorialDestination, "ibge:ZZ:9990001")
    row.pushed_at, row.push_hash = datetime.now(UTC), "abc"
    session.flush()
    stamp, updated = row.pushed_at, row.updated_at

    again = resolve_uf(session, UF)
    fresh(session, row)
    assert again.destinations_changed == 0 and row.pushed_at == stamp and row.updated_at == updated

    claim(session, doc(session, "newer", published=date(2023, 1, 1)), "tips", "Vá no inverno.",
          kind="destino", name="Pancas")  # fmt: skip
    resolve_uf(session, UF)
    fresh(session, row)
    assert (
        row.fields == {"tips": "Vá no inverno."}
        and row.pushed_at is None
        and row.push_hash == "abc"
    )


def test_a_destination_with_no_pushable_field_gets_no_outbox_row(session) -> None:
    municipio(session)
    d = doc(session, "pancas")
    claim(session, d, "safety_alerts", "Cuidado.", kind="destino", name="Pancas",
          stability="volatil", status="held_volatile")  # fmt: skip
    resolve_uf(session, UF)
    assert (
        session.scalars(select(EditorialDestination).where(EditorialDestination.uf == UF)).all()
        == []
    )
