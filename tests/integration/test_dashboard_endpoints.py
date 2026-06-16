"""Dashboard auth tests — Bearer dependency + either-or steward/Bearer guard.

Phase 4 / Plan 04-01 (DASH-06, D-02, RESEARCH §3 R4).

These tests are 100% offline (no DB, no network). The Bearer auth contract is
proven by calling the dependency callable directly — the 401 fires before any DB
work, mirroring the require_steward / webhook auth discipline.

Security contract proven here (threat register T-04-01..04):
  - missing Authorization header → 401
  - wrong Bearer token          → 401
  - valid Bearer token          → passes (returns None)
  - unset BRAVE_DASHBOARD_BEARER_TOKEN → fail-closed: every token rejected
  - constant-time hmac.compare_digest in the code path (no timing test)

Mark: most tests need NO DB. The either-or coexistence tests that hit a real
mutation route are @pytest.mark.integration.
"""

import os
import uuid
from datetime import UTC

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from brave.config.settings import DashboardConfig

BEARER_TOKEN = "test-dashboard-bearer-token-abc123"
STEWARD_SECRET = "test-steward-secret-xyz789"


@pytest.fixture
def bearer_token(monkeypatch):
    """Set and return a test dashboard Bearer token (offline)."""
    monkeypatch.setenv("BRAVE_DASHBOARD_BEARER_TOKEN", BEARER_TOKEN)
    return BEARER_TOKEN


@pytest.fixture
def either_or_secrets(monkeypatch):
    """Set both the Bearer token and the steward secret (either-or coexistence)."""
    monkeypatch.setenv("BRAVE_DASHBOARD_BEARER_TOKEN", BEARER_TOKEN)
    monkeypatch.setenv("BRAVE_STEWARD_SECRET", STEWARD_SECRET)
    return BEARER_TOKEN, STEWARD_SECRET


@pytest.fixture
def client():
    """FastAPI TestClient for route-level auth-gate tests."""
    os.environ.setdefault(
        "BRAVE_DB_URL",
        "postgresql+psycopg://brave:brave@localhost:5432/norteia_brave",
    )
    from brave.api.main import app

    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# DashboardConfig — reads BRAVE_DASHBOARD_BEARER_TOKEN, no alias (CR-02)
# ---------------------------------------------------------------------------


def test_dashboard_config_reads_env(bearer_token):
    """DashboardConfig.bearer_token resolves from BRAVE_DASHBOARD_BEARER_TOKEN."""
    cfg = DashboardConfig()
    assert cfg.bearer_token == BEARER_TOKEN


def test_dashboard_config_fail_closed_default(monkeypatch):
    """With the env var unset, bearer_token is empty (fail-closed default)."""
    monkeypatch.delenv("BRAVE_DASHBOARD_BEARER_TOKEN", raising=False)
    cfg = DashboardConfig()
    assert cfg.bearer_token == ""


# ---------------------------------------------------------------------------
# require_bearer — constant-time, fail-closed, never-logged Bearer gate
# (Called directly: proves the 401 fires before any DB work.)
# ---------------------------------------------------------------------------


def test_require_bearer_missing_header_returns_401(bearer_token):
    """No Authorization header → 401 (before any DB work)."""
    from brave.api.deps import require_bearer

    with pytest.raises(HTTPException) as exc:
        require_bearer(authorization=None, dashboard_config=DashboardConfig())
    assert exc.value.status_code == 401


def test_require_bearer_wrong_token_returns_401(bearer_token):
    """Authorization: Bearer wrong → 401."""
    from brave.api.deps import require_bearer

    with pytest.raises(HTTPException) as exc:
        require_bearer(
            authorization="Bearer definitely-wrong",
            dashboard_config=DashboardConfig(),
        )
    assert exc.value.status_code == 401


def test_require_bearer_valid_token_passes(bearer_token):
    """Authorization: Bearer <correct> → passes (returns None)."""
    from brave.api.deps import require_bearer

    result = require_bearer(
        authorization=f"Bearer {BEARER_TOKEN}",
        dashboard_config=DashboardConfig(),
    )
    assert result is None


def test_require_bearer_fail_closed_when_token_unset(monkeypatch):
    """With BRAVE_DASHBOARD_BEARER_TOKEN unset, every token is rejected 401."""
    monkeypatch.delenv("BRAVE_DASHBOARD_BEARER_TOKEN", raising=False)
    from brave.api.deps import require_bearer

    # Even a "Bearer " with empty expected must fail closed.
    with pytest.raises(HTTPException) as exc:
        require_bearer(
            authorization="Bearer anything",
            dashboard_config=DashboardConfig(),
        )
    assert exc.value.status_code == 401


def test_require_bearer_uses_constant_time_compare():
    """The require_bearer code path uses hmac.compare_digest (constant-time).

    Asserted by reading the code path, not by timing (per plan behavior spec).
    """
    import inspect

    import brave.api.deps as deps

    source = inspect.getsource(deps.require_bearer)
    assert "hmac.compare_digest" in source


def test_require_bearer_never_logs_secret():
    """require_bearer must not log/print the token or expected secret."""
    import inspect

    import brave.api.deps as deps

    source = inspect.getsource(deps.require_bearer)
    # No logging/printing of the secret material in the auth path.
    assert "logger" not in source
    assert "print(" not in source


# ---------------------------------------------------------------------------
# require_steward_or_bearer — either-or auth (RESEARCH §3 R4, T-04-02)
# Called directly: proves the gate logic offline, no DB.
# ---------------------------------------------------------------------------


def _call_either_or(x_steward_secret=None, authorization=None):
    """Invoke require_steward_or_bearer with fresh configs from current env."""
    from brave.api.deps import require_steward_or_bearer
    from brave.config.settings import DashboardConfig, StewardConfig

    return require_steward_or_bearer(
        x_steward_secret=x_steward_secret,
        authorization=authorization,
        steward_config=StewardConfig(),
        dashboard_config=DashboardConfig(),
    )


def test_either_or_steward_only_passes(either_or_secrets):
    """A valid X-Steward-Secret and no Bearer still passes (Phase 2/3 back-compat)."""
    _, steward = either_or_secrets
    assert _call_either_or(x_steward_secret=steward) is None


def test_either_or_bearer_only_passes(either_or_secrets):
    """A valid Authorization: Bearer and no steward header passes."""
    bearer, _ = either_or_secrets
    assert _call_either_or(authorization=f"Bearer {bearer}") is None


def test_either_or_neither_returns_401(either_or_secrets):
    """Neither header → 401."""
    with pytest.raises(HTTPException) as exc:
        _call_either_or()
    assert exc.value.status_code == 401


def test_either_or_both_wrong_returns_401(either_or_secrets):
    """Both headers wrong → 401."""
    with pytest.raises(HTTPException) as exc:
        _call_either_or(
            x_steward_secret="wrong-steward",
            authorization="Bearer wrong-bearer",
        )
    assert exc.value.status_code == 401


def test_either_or_bearer_unset_does_not_grant(monkeypatch):
    """An unset Bearer token must NOT let a Bearer-presented request pass (T-04-02).

    With BRAVE_DASHBOARD_BEARER_TOKEN unset and BRAVE_STEWARD_SECRET set, a
    Bearer-only request is rejected — the either-or still requires ONE valid secret.
    """
    monkeypatch.delenv("BRAVE_DASHBOARD_BEARER_TOKEN", raising=False)
    monkeypatch.setenv("BRAVE_STEWARD_SECRET", STEWARD_SECRET)
    with pytest.raises(HTTPException) as exc:
        _call_either_or(authorization="Bearer anything")
    assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# Route-level: the mutation endpoints use the either-or guard.
# The 401-no-header case fires BEFORE any DB work (no DB required).
# ---------------------------------------------------------------------------


def test_dlq_validate_no_auth_returns_401(client, either_or_secrets):
    """PATCH /api/v1/dlq/{id}/validate with no auth header → 401 before DB."""
    r = client.patch(f"/api/v1/dlq/{uuid.uuid4()}/validate")
    assert r.status_code == 401


def test_gate_approve_no_auth_returns_401(client, either_or_secrets):
    """PATCH /api/v1/atrativos/gate/{id}/approve with no auth header → 401 before DB."""
    r = client.patch(f"/api/v1/atrativos/gate/{uuid.uuid4()}/approve")
    assert r.status_code == 401


def test_dlq_validate_wrong_both_returns_401(client, either_or_secrets):
    """PATCH validate with both headers wrong → 401 before DB."""
    r = client.patch(
        f"/api/v1/dlq/{uuid.uuid4()}/validate",
        headers={
            "X-Steward-Secret": "wrong",
            "Authorization": "Bearer wrong",
        },
    )
    assert r.status_code == 401


@pytest.mark.integration
def test_dlq_validate_bearer_only_passes_auth(client, either_or_secrets):
    """PATCH validate with valid Bearer (no steward) passes auth → 404 (not 401)."""
    bearer, _ = either_or_secrets
    r = client.patch(
        f"/api/v1/dlq/{uuid.uuid4()}/validate",
        headers={"Authorization": f"Bearer {bearer}"},
    )
    assert r.status_code != 401
    assert r.status_code == 404


@pytest.mark.integration
def test_dlq_validate_steward_only_passes_auth(client, either_or_secrets):
    """PATCH validate with valid steward (no Bearer) still passes auth → 404."""
    _, steward = either_or_secrets
    r = client.patch(
        f"/api/v1/dlq/{uuid.uuid4()}/validate",
        headers={"X-Steward-Secret": steward},
    )
    assert r.status_code != 401
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/v1/dlq/{rio_id} — DLQ detail endpoint (DASH-01, D-01)
#
# The detail endpoint surfaces what the list deliberately omits: score_breakdown,
# normalized, Nascente payload, signals, and the per-record WhatsApp/steward log.
# The no-Bearer 401 fires before any DB work (no DB fixture needed); the 200-shape
# and 404 cases hit the real DB and are @pytest.mark.integration.
# ---------------------------------------------------------------------------


@pytest.fixture
def authed_client():
    """TestClient pre-set with a valid dashboard Bearer header (DB-backed tests)."""
    os.environ["BRAVE_DASHBOARD_BEARER_TOKEN"] = BEARER_TOKEN
    os.environ.setdefault(
        "BRAVE_DB_URL",
        "postgresql+psycopg://brave:brave@localhost:5432/norteia_brave",
    )
    from brave.api.main import app

    return TestClient(
        app,
        raise_server_exceptions=False,
        headers={"Authorization": f"Bearer {BEARER_TOKEN}"},
    )


def _make_dlq_record(db_session: Session):
    """Seed a Nascente + Rio (routing='dlq') record with a §7.6 score_breakdown.

    Returns the RioRecord. Mirrors the gate test seed helper — we only need a
    record in the right shape, not a full pipeline run.
    """
    from brave.core.models import NascenteRecord, RioRecord

    src_ref = f"places:BA:{uuid.uuid4().hex}"
    nascente = NascenteRecord(
        id=uuid.uuid4(),
        source="places_discovery",
        source_ref=src_ref,
        entity_type="destination",
        uf="BA",
        payload={"name": f"Destino {src_ref}", "place_id": src_ref, "signals": {"reviews": 12}},
        content_hash=f"hash:{src_ref}",
        version=1,
    )
    db_session.add(nascente)
    db_session.flush()

    rio = RioRecord(
        id=uuid.uuid4(),
        nascente_id=nascente.id,
        entity_type="destination",
        uf="BA",
        routing="dlq",
        dlq_reason="score_below_threshold",
        score=72.5,
        score_version="v1",
        score_breakdown={
            "origem": 80.0,
            "completude": 70.0,
            "corroboracao": 60.0,
            "atualidade": 75.0,
            "validacao_humana": 0.0,
        },
        normalized={"name": f"Destino {src_ref}", "signals": {"reviews": 12}},
    )
    db_session.add(rio)
    db_session.flush()
    return rio


def test_dlq_detail_no_bearer_returns_401(client, bearer_token):
    """GET /api/v1/dlq/{id} with no Authorization header → 401 before any DB work."""
    r = client.get(f"/api/v1/dlq/{uuid.uuid4()}")
    assert r.status_code == 401


@pytest.mark.integration
def test_dlq_detail_unknown_id_returns_404(authed_client):
    """GET /api/v1/dlq/{random} with valid Bearer → 404 (auth passed, no record)."""
    r = authed_client.get(f"/api/v1/dlq/{uuid.uuid4()}")
    assert r.status_code != 401
    assert r.status_code == 404


@pytest.mark.integration
def test_dlq_detail_returns_full_shape(authed_client, db_session: Session):
    """GET /api/v1/dlq/{id} returns score_breakdown + normalized + payload + log."""
    rio = _make_dlq_record(db_session)
    db_session.commit()

    r = authed_client.get(f"/api/v1/dlq/{rio.id}")
    assert r.status_code == 200, f"Unexpected status: {r.status_code} — {r.text}"
    body = r.json()

    for key in (
        "id",
        "routing",
        "sub_state",
        "dlq_reason",
        "score",
        "score_version",
        "score_breakdown",
        "normalized",
        "nascente_payload",
        "signals",
        "whatsapp_log",
    ):
        assert key in body, f"Missing key {key!r} in detail response: {body.keys()}"

    assert body["id"] == str(rio.id)
    assert body["routing"] == "dlq"
    assert body["dlq_reason"] == "score_below_threshold"
    assert isinstance(body["score_breakdown"], dict)
    assert isinstance(body["normalized"], dict)
    assert isinstance(body["nascente_payload"], dict)
    assert body["nascente_payload"].get("place_id") is not None
    assert isinstance(body["whatsapp_log"], list)


@pytest.mark.integration
def test_dlq_detail_score_breakdown_has_criteria(authed_client, db_session: Session):
    """score_breakdown surfaces the §7.6 per-criterion keys when present on the Rio record."""
    rio = _make_dlq_record(db_session)
    db_session.commit()

    body = authed_client.get(f"/api/v1/dlq/{rio.id}").json()
    breakdown = body["score_breakdown"]
    for criterion in ("origem", "completude", "corroboracao", "atualidade", "validacao_humana"):
        assert criterion in breakdown, f"Missing §7.6 criterion {criterion!r}: {breakdown}"


@pytest.mark.integration
def test_dlq_detail_whatsapp_log_ordered_by_created_at(authed_client, db_session: Session):
    """whatsapp_log returns this rio's AuditLog rows ordered by created_at ascending."""
    from datetime import datetime, timedelta

    from brave.core.models import AuditLog

    rio = _make_dlq_record(db_session)
    base = datetime(2026, 6, 16, 12, 0, 0, tzinfo=UTC)
    db_session.add(
        AuditLog(
            id=uuid.uuid4(),
            action="dlq_reprocessed",
            entity_type="destination",
            record_id=rio.id,
            actor="steward",
            created_at=base + timedelta(minutes=2),
        )
    )
    db_session.add(
        AuditLog(
            id=uuid.uuid4(),
            action="dlq_validated",
            entity_type="destination",
            record_id=rio.id,
            actor="steward",
            created_at=base + timedelta(minutes=1),
        )
    )
    # An unrelated audit row must NOT leak into this rio's log.
    db_session.add(
        AuditLog(
            id=uuid.uuid4(),
            action="dlq_rejected",
            entity_type="destination",
            record_id=uuid.uuid4(),
            actor="steward",
            created_at=base,
        )
    )
    db_session.commit()

    log = authed_client.get(f"/api/v1/dlq/{rio.id}").json()["whatsapp_log"]
    assert len(log) == 2, f"Expected only this rio's 2 rows, got {len(log)}: {log}"
    actions = [row["action"] for row in log]
    assert actions == ["dlq_validated", "dlq_reprocessed"], (
        f"Expected created_at-ascending order, got {actions}"
    )


# ---------------------------------------------------------------------------
# GET /api/v1/monitor — Brave monitor endpoint (DASH-02, §15.7, D-01)
#
# Returns volume (per-layer counts) + rates (AuditLog-derived approval/rejection/
# DLQ proportions over a window — THIS is the DASH-02 audit coverage) + throughput
# (RioRecord.processed_at over the window) + alerts (PoisonQuarantine count, RED
# WhatsApp quality flag). Read-only, Bearer-guarded; the no-Bearer 401 fires before
# any DB work. The RED quality flag is read via get_redis, overridden in tests with
# a fakeredis instance so the alert can be exercised offline.
# ---------------------------------------------------------------------------


@pytest.fixture
def monitor_redis(monkeypatch):
    """Override get_redis with a fresh fakeredis so the RED quality flag is testable.

    Returns the fakeredis instance + a teardown that clears the override. The
    fixture sets the dashboard Bearer env so the authed_client passes the gate.
    """
    import fakeredis

    from brave.api import deps
    from brave.api.main import app

    os.environ["BRAVE_DASHBOARD_BEARER_TOKEN"] = BEARER_TOKEN
    fake = fakeredis.FakeRedis()
    app.dependency_overrides[deps.get_redis] = lambda: fake
    try:
        yield fake
    finally:
        app.dependency_overrides.pop(deps.get_redis, None)


def test_monitor_no_bearer_returns_401(client, bearer_token):
    """GET /api/v1/monitor with no Authorization header → 401 before any DB work."""
    r = client.get("/api/v1/monitor")
    assert r.status_code == 401


@pytest.mark.integration
def test_monitor_returns_full_shape(authed_client, monitor_redis):
    """GET /api/v1/monitor returns volume + rates + throughput + alerts keys."""
    r = authed_client.get("/api/v1/monitor")
    assert r.status_code == 200, f"Unexpected status: {r.status_code} — {r.text}"
    body = r.json()

    for key in ("volume", "rates", "throughput", "alerts"):
        assert key in body, f"Missing key {key!r} in monitor response: {body.keys()}"

    # volume mirrors the metrics.py per-layer shape (pre-seeded, never missing keys)
    assert "nascente_count" in body["volume"]
    assert "rio_count" in body["volume"]
    for routing in ("in_progress", "mar", "dlq", "descarte"):
        assert routing in body["volume"]["rio_count"]
    assert "mar_count" in body["volume"]

    # rates pre-seed all three audit-derived actions, never missing
    for rate in ("dlq_validated", "dlq_rejected", "dlq_reprocessed"):
        assert rate in body["rates"], f"Missing audit-derived rate {rate!r}: {body['rates']}"

    # alerts carry the failure count + the RED quality flag
    assert "failures" in body["alerts"]
    assert "quality" in body["alerts"]
    assert isinstance(body["alerts"]["failures"], int)


@pytest.mark.integration
def test_monitor_empty_db_preseeds_zero(authed_client, monitor_redis):
    """An empty-window DB still returns 200 with pre-seeded zero counts/rates."""
    # Use a future window so no rows fall in it → everything pre-seeds to 0.
    r = authed_client.get("/api/v1/monitor?since_hours=0")
    assert r.status_code == 200
    body = r.json()
    for routing in ("in_progress", "mar", "dlq", "descarte"):
        assert isinstance(body["volume"]["rio_count"][routing], int)
    for rate in ("dlq_validated", "dlq_rejected", "dlq_reprocessed"):
        # rates pre-seeded to 0.0 with no audit rows in the window
        assert body["rates"][rate] == 0.0
    assert isinstance(body["throughput"], int)


@pytest.mark.integration
def test_monitor_rates_derive_from_auditlog(authed_client, db_session: Session, monitor_redis):
    """rates reflect AuditLog action counts (dlq_validated/dlq_rejected/dlq_reprocessed)."""
    from datetime import datetime, timedelta

    from brave.core.models import AuditLog

    now = datetime.now(UTC)
    # Seed 3 validated + 1 rejected within the default window.
    for _ in range(3):
        db_session.add(
            AuditLog(
                id=uuid.uuid4(),
                action="dlq_validated",
                entity_type="destination",
                record_id=uuid.uuid4(),
                actor="steward",
                created_at=now - timedelta(minutes=5),
            )
        )
    db_session.add(
        AuditLog(
            id=uuid.uuid4(),
            action="dlq_rejected",
            entity_type="destination",
            record_id=uuid.uuid4(),
            actor="steward",
            created_at=now - timedelta(minutes=5),
        )
    )
    db_session.commit()

    body = authed_client.get("/api/v1/monitor?since_hours=24").json()
    rates = body["rates"]
    # validated should outweigh rejected (3 vs 1) → proportion higher
    assert rates["dlq_validated"] > rates["dlq_rejected"]
    # all proportions are in [0, 1]
    for v in rates.values():
        assert 0.0 <= v <= 1.0


@pytest.mark.integration
def test_monitor_alerts_failures_reflects_poison_quarantine(
    authed_client, db_session: Session, monitor_redis
):
    """alerts.failures equals the PoisonQuarantine row count."""
    from brave.core.models import PoisonQuarantine

    before = authed_client.get("/api/v1/monitor").json()["alerts"]["failures"]

    db_session.add(
        PoisonQuarantine(
            id=uuid.uuid4(),
            nascente_id=uuid.uuid4(),
            task_name="brave.process_nascente",
            error_message="boom",
        )
    )
    db_session.commit()

    after = authed_client.get("/api/v1/monitor").json()["alerts"]["failures"]
    assert after == before + 1


@pytest.mark.integration
def test_monitor_alerts_quality_reflects_red_flag(authed_client, monitor_redis):
    """alerts.quality is True when the wa:quality_red flag is set, False otherwise."""
    from brave.compliance.quality_rating import QUALITY_RED_KEY

    # Flag absent → quality not RED.
    monitor_redis.delete(QUALITY_RED_KEY)
    body = authed_client.get("/api/v1/monitor").json()
    assert body["alerts"]["quality"] is False

    # Flag set → quality RED (alert).
    monitor_redis.set(QUALITY_RED_KEY, "1")
    body = authed_client.get("/api/v1/monitor").json()
    assert body["alerts"]["quality"] is True
