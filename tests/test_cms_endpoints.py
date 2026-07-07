"""Offline pytest suite for CMS CRUD endpoints (08-07, D-07).

Endpoints under test:
  GET  /api/v1/destinos                     — list destinos (Bearer-guarded)
  GET  /api/v1/destinos/{rio_id}            — detail (Bearer-guarded)
  PATCH /api/v1/destinos/{rio_id}/promote   — steward promote → routing==mar
  PATCH /api/v1/destinos/{rio_id}/descarte  — steward descarte
  GET  /api/v1/atrativos                    — list atrativos (Bearer-guarded)
  GET  /api/v1/atrativos/{rio_id}           — detail (Bearer-guarded, phone_e164 masked)
  PATCH /api/v1/atrativos/{rio_id}/advance  — FSM advance (409 on conflict)
  PATCH /api/v1/atrativos/{rio_id}/descarte — descarte atrativo

Security tests (T-08-01, T-08-04):
  - All GET + PATCH without Authorization: Bearer → 401
  - phone_e164 never in atrativo response — only phone_masked

All tests are integration-marked (require docker-compose Postgres).
Bearer + steward secrets set before client construction via os.environ.

100% offline — no real Celery/Places/LLM (T-08-SC).
"""

import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

# Test tokens — unique to this module to avoid collisions with other test modules.
BEARER_TOKEN = "test-cms-bearer-token-08-07"
STEWARD_SECRET = "test-cms-steward-secret-08-07"

os.environ.setdefault(
    "BRAVE_DB_URL",
    "postgresql+psycopg://brave:brave@localhost:5432/norteia_brave",
)

BEARER_HEADERS = {"Authorization": f"Bearer {BEARER_TOKEN}"}
STEWARD_HEADERS = {"X-Steward-Secret": STEWARD_SECRET}


@pytest.fixture(scope="module")
def app():
    """FastAPI app instance (module-scoped to avoid reimporting)."""
    from brave.api.main import app as _app  # noqa: PLC0415
    return _app


@pytest.fixture(autouse=True)
def _pin_test_secrets():
    """Force our module-specific test secrets before each test.

    pydantic-settings DashboardConfig + StewardConfig re-read os.environ on
    every instantiation (called via Depends on each request). When pytest runs
    multiple test modules in sequence, a prior module may have overwritten
    BRAVE_DASHBOARD_BEARER_TOKEN with its own test token. This autouse fixture
    re-pins our token before every test so auth never fails due to ordering.
    """
    os.environ["BRAVE_DASHBOARD_BEARER_TOKEN"] = BEARER_TOKEN
    os.environ["BRAVE_STEWARD_SECRET"] = STEWARD_SECRET
    yield


@pytest.fixture(scope="module")
def client(app):
    """FastAPI TestClient — bare, no default auth headers (auth tests use explicit headers)."""
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def unlock_editing():
    """Release the card edit-lock (phase C) so a lock-gated mutation reaches its handler.

    require_editing_unlocked returns 423 while the engine mode is LIGADO (the default),
    so the /advance (and /edit, /transition) endpoints need mode PAUSADO to exercise
    their real 200/409 business logic. Sets PAUSADO on the same Redis the app resolves
    via get_redis(), then restores the LIGADO default on teardown so the mode never
    leaks to other tests.

    Only lock-gated integration tests request this fixture — the 401 auth tests
    short-circuit before the lock and must not require a live Redis.
    """
    from brave.api.deps import get_redis  # noqa: PLC0415
    from brave.core import engine as collection_engine  # noqa: PLC0415

    rc = get_redis()
    collection_engine.set_mode(rc, collection_engine.PAUSADO)
    try:
        yield
    finally:
        rc.delete(collection_engine._MODE_KEY)  # restore default (LIGADO)


# ---------------------------------------------------------------------------
# Test-data factory helpers
# ---------------------------------------------------------------------------


def _make_nascente(db_session: Session, uf: str = "BA", entity_type: str = "destination") -> object:
    """Create a minimal NascenteRecord for test data."""
    from brave.core.models import NascenteRecord  # noqa: PLC0415

    src_ref = f"mtur:{uf}:{uuid.uuid4().hex}"
    nascente = NascenteRecord(
        id=uuid.uuid4(),
        source="mtur",
        source_ref=src_ref,
        entity_type=entity_type,
        uf=uf,
        payload={"name": f"Destino Teste {src_ref}"},
        content_hash=f"hash:{src_ref}",
        version=1,
    )
    db_session.add(nascente)
    db_session.flush()
    return nascente


def _make_destino(
    db_session: Session,
    uf: str = "BA",
    routing: str = "dlq",
    score: float = 72.0,
    normalized: dict | None = None,
) -> object:
    """Create a minimal RioRecord (entity_type=destination) for test data."""
    from brave.core.models import RioRecord  # noqa: PLC0415

    nascente = _make_nascente(db_session, uf=uf, entity_type="destination")
    n = normalized or {"name": f"Destino {uf} {uuid.uuid4().hex[:6]}"}
    rio = RioRecord(
        id=uuid.uuid4(),
        nascente_id=nascente.id,
        entity_type="destination",
        uf=uf,
        routing=routing,
        score=score,
        canonical_key=f"destino:{uf}:{uuid.uuid4().hex[:8]}",
        normalized=n,
        score_breakdown={"origem": 30.0, "completude": 15.0, "corroboracao": 12.0},
    )
    db_session.add(rio)
    db_session.flush()
    return rio


def _make_atrativo(
    db_session: Session,
    uf: str = "BA",
    sub_state: str = "discovered",
    routing: str = "in_progress",
    score: float = 65.0,
    normalized: dict | None = None,
) -> object:
    """Create a minimal RioRecord (entity_type=attraction) for test data."""
    from brave.core.models import RioRecord  # noqa: PLC0415

    nascente = _make_nascente(db_session, uf=uf, entity_type="attraction")
    n = normalized or {"name": f"Atrativo {uf} {uuid.uuid4().hex[:6]}"}
    rio = RioRecord(
        id=uuid.uuid4(),
        nascente_id=nascente.id,
        entity_type="attraction",
        uf=uf,
        routing=routing,
        sub_state=sub_state,
        score=score,
        canonical_key=f"atrativo:{uf}:{uuid.uuid4().hex[:8]}",
        normalized=n,
        score_breakdown={"origem": 20.0, "completude": 10.0},
    )
    db_session.add(rio)
    db_session.flush()
    return rio


# ===========================================================================
# DESTINOS — auth tests (no DB required; 401 fires before any DB work)
# ===========================================================================


def test_list_destinos_bearer_required(client):
    """GET /api/v1/destinos without Authorization: Bearer → 401 (T-08-01)."""
    r = client.get("/api/v1/destinos")
    assert r.status_code == 401


def test_get_destino_detail_bearer_required(client):
    """GET /api/v1/destinos/{id} without Authorization: Bearer → 401 (T-08-01)."""
    r = client.get(f"/api/v1/destinos/{uuid.uuid4()}")
    assert r.status_code == 401


def test_promote_destino_bearer_required(client):
    """PATCH /api/v1/destinos/{id}/promote without auth → 401 (T-08-01)."""
    r = client.patch(f"/api/v1/destinos/{uuid.uuid4()}/promote")
    assert r.status_code == 401


def test_descarte_destino_bearer_required(client):
    """PATCH /api/v1/destinos/{id}/descarte without auth → 401 (T-08-01)."""
    r = client.patch(f"/api/v1/destinos/{uuid.uuid4()}/descarte")
    assert r.status_code == 401


# ===========================================================================
# DESTINOS — business logic (integration, require Postgres)
# ===========================================================================


@pytest.mark.integration
def test_list_destinos_with_bearer(client, db_session: Session):
    """GET /api/v1/destinos with Bearer → 200; {items, total, offset, limit}; item has expected keys."""
    # Use rare UF code "AM" to reduce noise from other integration tests
    rio = _make_destino(db_session, uf="AM", routing="dlq")
    db_session.commit()

    # Filter by the test UF to avoid pagination gaps when DB has many records
    r = client.get("/api/v1/destinos?uf=AM&limit=500", headers=BEARER_HEADERS)
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"

    body = r.json()
    assert "items" in body
    assert "total" in body
    assert "offset" in body
    assert "limit" in body
    assert body["total"] >= 1

    # Verify our record is present with the expected shape
    ids = [item["id"] for item in body["items"]]
    assert str(rio.id) in ids, (
        f"Newly created rio {rio.id} should appear in destinos list"
    )

    item = next(i for i in body["items"] if i["id"] == str(rio.id))
    assert "routing" in item
    assert "score" in item
    assert "name" in item
    assert "validation_pending" in item


@pytest.mark.integration
def test_list_destinos_filter_uf(client, db_session: Session):
    """GET /api/v1/destinos?uf=AC returns only AC records (UF filter), not TO records."""
    ac_rio = _make_destino(db_session, uf="AC")
    to_rio = _make_destino(db_session, uf="TO")
    db_session.commit()

    r = client.get("/api/v1/destinos?uf=AC&limit=500", headers=BEARER_HEADERS)
    assert r.status_code == 200

    body = r.json()
    ids = [item["id"] for item in body["items"]]
    assert str(ac_rio.id) in ids, "AC record should appear in uf=AC filter"
    assert str(to_rio.id) not in ids, "TO record should NOT appear in uf=AC filter"


@pytest.mark.integration
def test_get_destino_detail_404(client):
    """GET /api/v1/destinos/{unknown_uuid} with Bearer → 404."""
    r = client.get(f"/api/v1/destinos/{uuid.uuid4()}", headers=BEARER_HEADERS)
    assert r.status_code == 404


@pytest.mark.integration
def test_get_destino_detail(client, db_session: Session):
    """GET /api/v1/destinos/{rio_id} → 200; body has score_breakdown, audit_log, normalized."""
    rio = _make_destino(db_session, uf="CE")
    db_session.commit()

    r = client.get(f"/api/v1/destinos/{rio.id}", headers=BEARER_HEADERS)
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"

    body = r.json()
    assert "score_breakdown" in body, "detail must have score_breakdown"
    assert isinstance(body["score_breakdown"], dict)
    assert "audit_log" in body, "detail must have audit_log"
    assert isinstance(body["audit_log"], list)
    assert "normalized" in body


@pytest.mark.integration
def test_promote_destino_steward(client, db_session: Session):
    """PATCH /api/v1/destinos/{id}/promote with steward secret → 202 {status, routing}.

    Uses score values that push total to >=85 after validacao_humana=100 boost.
    """
    # Create destino with high scores to ensure promote reaches 'mar' after validation
    normalized = {
        "name": "Destino Para Promoção",
        "origem_value": 100.0,
        "completude_value": 100.0,
        "corroboracao_value": 100.0,
        "atualidade_value": 100.0,
        # validacao_humana_value intentionally missing — validate_and_promote_rio adds it
    }
    rio = _make_destino(db_session, uf="PE", routing="dlq", normalized=normalized)
    db_session.commit()

    r = client.patch(f"/api/v1/destinos/{rio.id}/promote", headers=STEWARD_HEADERS)
    assert r.status_code == 202, f"Expected 202, got {r.status_code}: {r.text}"

    body = r.json()
    assert body.get("status") == "accepted"
    assert "routing" in body
    assert body.get("rio_id") == str(rio.id)


@pytest.mark.integration
def test_descarte_destino(client, db_session: Session):
    """PATCH /api/v1/destinos/{id}/descarte with Bearer → 200; routing==descarte."""
    rio = _make_destino(db_session, uf="SP", routing="dlq")
    db_session.commit()

    r = client.patch(f"/api/v1/destinos/{rio.id}/descarte", headers=BEARER_HEADERS)
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"

    body = r.json()
    assert body.get("routing") == "descarte"

    # Verify DB state
    db_session.refresh(rio)
    assert rio.routing == "descarte"


@pytest.mark.integration
def test_descarte_destino_blocked_when_mar_exists(client, db_session: Session):
    """WR-05: descarte on an already-promoted destino (MarRecord exists) → 409.

    Plain descarte does not depublish the canonical MarRecord nor notify
    norteia-api, so it must be refused for promoted records to preserve the
    Mar trust invariant.
    """
    from brave.core.models import MarRecord  # noqa: PLC0415

    rio = _make_destino(db_session, uf="RR", routing="mar", score=90.0)
    db_session.flush()
    mar = MarRecord(
        id=uuid.uuid4(),
        rio_id=rio.id,
        entity_type="destination",
        source_ref=f"mar:test:{uuid.uuid4().hex[:10]}",
        canonical={"name": "Destino Promovido"},
        provenance={"source": "test"},
        reliability_score=90.0,
        score_version="test-v1",
    )
    db_session.add(mar)
    db_session.commit()

    r = client.patch(f"/api/v1/destinos/{rio.id}/descarte", headers=BEARER_HEADERS)
    assert r.status_code == 409, f"Expected 409, got {r.status_code}: {r.text}"

    # Routing must be unchanged — descarte was refused
    db_session.refresh(rio)
    assert rio.routing == "mar", "routing must remain 'mar' when descarte is blocked"


# ===========================================================================
# ATRATIVOS — auth tests (no DB required)
# ===========================================================================


def test_list_atrativos_bearer_required(client):
    """GET /api/v1/atrativos without Authorization: Bearer → 401 (T-08-01)."""
    r = client.get("/api/v1/atrativos")
    assert r.status_code == 401


def test_get_atrativo_detail_bearer_required(client):
    """GET /api/v1/atrativos/{id} without Authorization: Bearer → 401 (T-08-01)."""
    r = client.get(f"/api/v1/atrativos/{uuid.uuid4()}")
    assert r.status_code == 401


def test_advance_atrativo_bearer_required(client):
    """PATCH /api/v1/atrativos/{id}/advance without auth → 401 (T-08-01)."""
    r = client.patch(
        f"/api/v1/atrativos/{uuid.uuid4()}/advance",
        json={"expected_state": "discovered", "next_state": "contacts_found"},
    )
    assert r.status_code == 401


def test_descarte_atrativo_bearer_required(client):
    """PATCH /api/v1/atrativos/{id}/descarte without auth → 401 (T-08-01)."""
    r = client.patch(f"/api/v1/atrativos/{uuid.uuid4()}/descarte")
    assert r.status_code == 401


# ===========================================================================
# ATRATIVOS — business logic (integration, require Postgres)
# ===========================================================================


@pytest.mark.integration
def test_list_atrativos_with_bearer(client, db_session: Session):
    """GET /api/v1/atrativos with Bearer → 200; items include sub_state key."""
    # Use rare UF "AP" to reduce pagination noise
    rio = _make_atrativo(db_session, uf="AP", sub_state="discovered")
    db_session.commit()

    r = client.get("/api/v1/atrativos?uf=AP&limit=500", headers=BEARER_HEADERS)
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"

    body = r.json()
    assert "items" in body
    assert body["total"] >= 1

    ids = [item["id"] for item in body["items"]]
    assert str(rio.id) in ids, (
        f"Newly created atrativo {rio.id} should appear in list"
    )

    item = next(i for i in body["items"] if i["id"] == str(rio.id))
    assert "sub_state" in item, "atrativo list item must have sub_state key"


@pytest.mark.integration
def test_list_atrativos_pii_masked(client, db_session: Session):
    """GET /api/v1/atrativos/{id} → phone_e164 NOT in response; phone_masked present (T-08-04)."""
    normalized = {
        "name": "Mercado Modelo",
        "contacts": {
            "phone_e164": "+5571999990000",
            "website": "https://mercadomodelo.com.br",
        },
    }
    rio = _make_atrativo(db_session, uf="BA", normalized=normalized)
    db_session.commit()

    r = client.get(f"/api/v1/atrativos/{rio.id}", headers=BEARER_HEADERS)
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"

    # T-08-04: phone_e164 must never appear in the response
    response_str = str(r.json())
    assert "phone_e164" not in response_str, (
        "phone_e164 MUST NOT appear anywhere in atrativo response (T-08-04 / LGPD)"
    )
    assert "phone_masked" in response_str, (
        "phone_masked MUST appear in atrativo response (T-08-04)"
    )


@pytest.mark.integration
def test_get_atrativo_detail_contacts_masked(client, db_session: Session):
    """GET /api/v1/atrativos/{id} normalized.contacts has phone_masked not phone_e164 (T-08-04)."""
    phone_raw = "+5571888880000"
    normalized = {
        "name": "Parque Estadual",
        "contacts": {"phone_e164": phone_raw, "email": "parque@ba.gov.br"},
    }
    rio = _make_atrativo(db_session, uf="BA", normalized=normalized)
    db_session.commit()

    r = client.get(f"/api/v1/atrativos/{rio.id}", headers=BEARER_HEADERS)
    assert r.status_code == 200

    body = r.json()
    contacts = body["normalized"].get("contacts", {})
    assert "phone_e164" not in contacts, (
        "normalized.contacts MUST NOT have phone_e164 key (T-08-04)"
    )
    assert "phone_masked" in contacts, (
        "normalized.contacts MUST have phone_masked key (T-08-04)"
    )
    # Raw value must not be present anywhere
    assert phone_raw not in str(body), (
        "Raw E.164 phone number MUST NOT appear in response (T-08-04 / LGPD)"
    )


@pytest.mark.integration
def test_get_atrativo_detail_events_and_engineering_fields(client, db_session: Session):
    """GET /api/v1/atrativos/{id} surfaces the Log-tab timeline + engineering fields.

    The drawer Log tab reads ``events[]`` (RecordEvent timeline keyed by
    rio.canonical_key) plus the scalar engineering fields ``dlq_reason``,
    ``source`` (nascente.source), ``processed_at`` and ``score_version``. This
    seeds two RecordEvents on the atrativo's canonical_key and asserts they come
    back in the detail body, oldest→newest, alongside those fields.
    """
    from brave.observability.record_events import record_event  # noqa: PLC0415

    rio = _make_atrativo(db_session, uf="AC", routing="dlq", score=42.0)
    canonical_key = rio.canonical_key

    # Two timeline events keyed by the universal drawer key (rio.canonical_key).
    record_event(
        db_session,
        source="tripadvisor",
        source_ref=canonical_key,
        stage="ingested",
        status="ok",
        message="Atrativo Log Detail",
        entity_type="attraction",
        uf="AC",
        nascente_id=rio.nascente_id,
        data={"municipio": "Rio Branco", "version": 1},
    )
    record_event(
        db_session,
        source="tripadvisor",
        source_ref=canonical_key,
        stage="routed",
        status="fail",
        message="dlq: score below threshold",
        entity_type="attraction",
        uf="AC",
        rio_id=rio.id,
        data={"routing": "dlq", "score": 42.0},
    )
    db_session.commit()

    r = client.get(f"/api/v1/atrativos/{rio.id}", headers=BEARER_HEADERS)
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    body = r.json()

    # Engineering fields the Log tab / drawer header consume.
    for field in ("dlq_reason", "source", "processed_at", "score_version"):
        assert field in body, f"detail body must include {field!r} for the Log tab; keys={list(body)}"
    # source is derived from the backing NascenteRecord (_make_nascente → 'mtur').
    assert body["source"] == "mtur"

    # events[] carries the RecordEvent timeline, oldest→newest, with the Log shape.
    events = body["events"]
    assert isinstance(events, list)
    stages = [e["stage"] for e in events]
    assert "ingested" in stages and "routed" in stages, (
        f"seeded events must appear in detail events[]; got {stages}"
    )
    assert stages.index("ingested") < stages.index("routed"), (
        f"events[] must be ordered oldest→newest; got {stages}"
    )
    for e in events:
        assert set(e) >= {"stage", "status", "message", "data", "created_at"}, (
            f"each event must expose the Log-line fields; got {list(e)}"
        )
    routed_event = next(e for e in events if e["stage"] == "routed")
    assert routed_event["status"] == "fail"
    assert routed_event["data"] == {"routing": "dlq", "score": 42.0}


@pytest.mark.integration
def test_atrativo_owner_email_never_leaked(client, db_session: Session):
    """CR-01: owner email (and ig_handle) must NOT appear in list or detail responses (LGPD R3)."""
    owner_email = "dono.secreto@example.com"
    owner_ig = "@dono_secreto"
    normalized = {
        "name": "Cachoeira Secreta",
        "contacts": {
            "phone_e164": "+5571777770000",
            "website": "https://cachoeirasecreta.com.br",
            "email": owner_email,
            "ig_handle": owner_ig,
        },
    }
    rio = _make_atrativo(db_session, uf="AP", normalized=normalized)
    db_session.commit()

    # Detail path
    r_detail = client.get(f"/api/v1/atrativos/{rio.id}", headers=BEARER_HEADERS)
    assert r_detail.status_code == 200
    detail_str = str(r_detail.json())
    assert owner_email not in detail_str, (
        "CR-01: owner email MUST NOT appear in atrativo detail response (LGPD R3)"
    )
    assert owner_ig not in detail_str, (
        "CR-01: owner ig_handle MUST NOT appear in atrativo detail response (LGPD R3)"
    )
    assert "email" not in r_detail.json()["normalized"].get("contacts", {})
    # Website (non-PII) is still surfaced
    assert "https://cachoeirasecreta.com.br" in detail_str

    # List path
    r_list = client.get("/api/v1/atrativos?uf=AP&limit=500", headers=BEARER_HEADERS)
    assert r_list.status_code == 200
    list_str = str(r_list.json())
    assert owner_email not in list_str, (
        "CR-01: owner email MUST NOT appear in atrativo list response (LGPD R3)"
    )
    assert owner_ig not in list_str, (
        "CR-01: owner ig_handle MUST NOT appear in atrativo list response (LGPD R3)"
    )
    item = next(i for i in r_list.json()["items"] if i["id"] == str(rio.id))
    summary = item["contacts_summary"] or {}
    assert "email" not in summary and "ig_handle" not in summary
    assert summary.get("website") == "https://cachoeirasecreta.com.br"


@pytest.mark.integration
def test_advance_atrativo_conflict(client, db_session: Session, unlock_editing):
    """PATCH /api/v1/atrativos/{id}/advance → 409 when expected_state != actual sub_state.

    Requires mode PAUSADO (unlock_editing) — the phase-C edit-lock otherwise 423s
    this lock-gated endpoint before the conflict check runs.
    """
    # actual sub_state is "contacts_found"; we send expected_state="discovered" → mismatch → 409
    rio = _make_atrativo(db_session, sub_state="contacts_found")
    db_session.commit()

    r = client.patch(
        f"/api/v1/atrativos/{rio.id}/advance",
        headers=BEARER_HEADERS,
        json={"expected_state": "discovered", "next_state": "contacts_found"},
    )
    assert r.status_code == 409, (
        f"Expected 409 on sub_state mismatch, got {r.status_code}: {r.text}"
    )


@pytest.mark.integration
def test_advance_atrativo_success(client, db_session: Session, unlock_editing):
    """PATCH /api/v1/atrativos/{id}/advance → 200; sub_state advanced when match.

    This is the genuine 200-under-PAUSADO path: unlock_editing sets mode PAUSADO so
    the edit-lock releases and the real FSM advance runs against Postgres.
    """
    rio = _make_atrativo(db_session, sub_state="discovered")
    db_session.commit()

    r = client.patch(
        f"/api/v1/atrativos/{rio.id}/advance",
        headers=BEARER_HEADERS,
        json={"expected_state": "discovered", "next_state": "contacts_found"},
    )
    assert r.status_code == 200, (
        f"Expected 200 on valid advance, got {r.status_code}: {r.text}"
    )

    body = r.json()
    assert body.get("sub_state") == "contacts_found", (
        f"Expected sub_state='contacts_found', got {body.get('sub_state')!r}"
    )


@pytest.mark.integration
def test_descarte_atrativo(client, db_session: Session):
    """PATCH /api/v1/atrativos/{id}/descarte with Bearer → 200; routing==dlq."""
    rio = _make_atrativo(db_session, sub_state="discovered")
    db_session.commit()

    r = client.patch(f"/api/v1/atrativos/{rio.id}/descarte", headers=BEARER_HEADERS)
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"

    body = r.json()
    assert body.get("routing") == "dlq", (
        f"Expected routing='dlq', got {body.get('routing')!r}"
    )


# ---------------------------------------------------------------------------
# promote_destino broker-down: surface (503), never silently drop.
# Unlike the DLQ path, promote_destino commits the promotion (WR-01) BEFORE the
# push, so a failed push cannot roll back — the record IS in Mar. The contract is
# therefore "promoted but downstream publish failed; retry publish", not rollback.
# ---------------------------------------------------------------------------

_PROMOTABLE_NORMALIZED = {
    "name": "Destino Broker Down",
    "origem_value": 100.0,
    "completude_value": 100.0,
    "corroboracao_value": 100.0,
    "atualidade_value": 100.0,
}


@pytest.mark.integration
def test_promote_returns_503_when_push_fails_under_real_externals(
    client, db_session: Session, monkeypatch
):
    """A broker-down push during promote surfaces 503 instead of silently dropping.

    The promotion is already committed (WR-01), so the record stays in Mar — the
    503 tells the steward the downstream publish failed and to retry the publish.
    """
    from brave.core.models import RioRecord
    from brave.tasks.pipeline import push_destination_task

    rio = _make_destino(
        db_session, uf="PE", routing="dlq", normalized=dict(_PROMOTABLE_NORMALIZED)
    )
    rio_id = rio.id
    db_session.commit()

    monkeypatch.setenv("RUN_REAL_EXTERNALS", "true")

    def _broker_down(*args, **kwargs):
        raise RuntimeError("broker unreachable (simulated)")

    monkeypatch.setattr(push_destination_task, "delay", _broker_down)

    r = client.patch(f"/api/v1/destinos/{rio_id}/promote", headers=STEWARD_HEADERS)
    assert r.status_code == 503, f"Expected 503, got {r.status_code}: {r.text}"

    # Promotion is committed (WR-01) — the record stays in Mar, publish is retryable.
    db_session.expire_all()
    reloaded = db_session.get(RioRecord, rio_id)
    assert reloaded is not None
    assert reloaded.routing == "mar", (
        f"promote commits before push (WR-01) — record must stay 'mar', got "
        f"'{reloaded.routing}'"
    )


@pytest.mark.integration
def test_promote_swallows_push_failure_offline(client, db_session: Session, monkeypatch):
    """Offline (run_real_externals=False), a broker-down push is an expected no-op → 202."""
    from brave.tasks.pipeline import push_destination_task

    rio = _make_destino(
        db_session, uf="MA", routing="dlq", normalized=dict(_PROMOTABLE_NORMALIZED)
    )
    rio_id = rio.id
    db_session.commit()

    monkeypatch.setenv("RUN_REAL_EXTERNALS", "false")

    def _broker_down(*args, **kwargs):
        raise RuntimeError("broker unreachable (simulated)")

    monkeypatch.setattr(push_destination_task, "delay", _broker_down)

    r = client.patch(f"/api/v1/destinos/{rio_id}/promote", headers=STEWARD_HEADERS)
    assert r.status_code == 202, f"Expected 202, got {r.status_code}: {r.text}"


# ===========================================================================
# NASCENTE — read-only board cards (GET /api/v1/nascente)
# ===========================================================================


def test_list_nascente_bearer_required(client):
    """GET /api/v1/nascente without Authorization: Bearer → 401."""
    r = client.get("/api/v1/nascente")
    assert r.status_code == 401


@pytest.mark.integration
def test_list_nascente_with_bearer_shape_and_name(client, db_session: Session):
    """GET /api/v1/nascente → 200; envelope shape; name comes from payload.name."""
    nascente = _make_nascente(db_session, uf="AC", entity_type="destination")
    db_session.commit()

    r = client.get("/api/v1/nascente?uf=AC&limit=500", headers=BEARER_HEADERS)
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"

    body = r.json()
    assert set(["items", "total", "offset", "limit"]).issubset(body.keys())
    assert body["total"] >= 1

    item = next(i for i in body["items"] if i["id"] == str(nascente.id))
    assert item["uf"] == "AC"
    assert item["entity_type"] == "destination"
    assert item["source"] == "mtur"
    # name is read from payload.name (allow-list), never the whole payload
    assert item["name"] == nascente.payload["name"]
    assert "payload" not in item
    assert "content_hash" not in item


@pytest.mark.integration
def test_list_nascente_excludes_superseded(client, db_session: Session):
    """Superseded rows (superseded_by_id set) are NOT listed — current versions only."""

    old = _make_nascente(db_session, uf="RR", entity_type="destination")
    new = _make_nascente(db_session, uf="RR", entity_type="destination")
    old.superseded_by_id = new.id
    db_session.commit()

    r = client.get("/api/v1/nascente?uf=RR&limit=500", headers=BEARER_HEADERS)
    assert r.status_code == 200, r.text
    ids = [i["id"] for i in r.json()["items"]]
    assert str(new.id) in ids
    assert str(old.id) not in ids


@pytest.mark.integration
def test_list_nascente_entity_type_filter(client, db_session: Session):
    """entity_type filter narrows the list to attractions only."""
    dest = _make_nascente(db_session, uf="AP", entity_type="destination")
    attr = _make_nascente(db_session, uf="AP", entity_type="attraction")
    db_session.commit()

    r = client.get(
        "/api/v1/nascente?uf=AP&entity_type=attraction&limit=500",
        headers=BEARER_HEADERS,
    )
    assert r.status_code == 200, r.text
    ids = [i["id"] for i in r.json()["items"]]
    assert str(attr.id) in ids
    assert str(dest.id) not in ids


@pytest.mark.integration
def test_list_nascente_unrouted_excludes_rio_twin(client, db_session: Session):
    """unrouted=true lists only Nascente records with NO RioRecord twin (Bug 4).

    A Nascente WITH a Rio twin is excluded; one WITHOUT is included. The default
    (unrouted=false) returns both.
    """
    from brave.core.models import RioRecord  # noqa: PLC0415

    # Nascente with a Rio twin (routed) — must be excluded when unrouted=true.
    routed = _make_nascente(db_session, uf="SE", entity_type="destination")
    rio = RioRecord(
        id=uuid.uuid4(),
        nascente_id=routed.id,
        entity_type="destination",
        uf="SE",
        routing="mar",
        score=90.0,
        canonical_key=f"destino:SE:{uuid.uuid4().hex[:8]}",
        normalized={"name": "Roteado"},
    )
    db_session.add(rio)

    # Nascente WITHOUT a Rio twin — must be included when unrouted=true.
    unrouted = _make_nascente(db_session, uf="SE", entity_type="destination")
    db_session.commit()

    r = client.get(
        "/api/v1/nascente?uf=SE&unrouted=true&limit=500", headers=BEARER_HEADERS
    )
    assert r.status_code == 200, r.text
    ids = [i["id"] for i in r.json()["items"]]
    assert str(unrouted.id) in ids
    assert str(routed.id) not in ids

    # Default (unrouted=false) returns both.
    r2 = client.get("/api/v1/nascente?uf=SE&limit=500", headers=BEARER_HEADERS)
    assert r2.status_code == 200, r2.text
    ids2 = [i["id"] for i in r2.json()["items"]]
    assert str(unrouted.id) in ids2
    assert str(routed.id) in ids2
