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
from sqlalchemy import func, select
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


# ---------------------------------------------------------------------------
# GET /api/v1/cost — cost-by-lane/model aggregation over llm_generations
# (DASH-04, D-01). A straight GROUP BY: sum(usd_cost), sum(prompt+completion
# tokens), count(id), grouped by lane or model_slug, optionally windowed by a
# `since` timestamp on created_at. Read-only, Bearer-guarded; the no-Bearer 401
# fires before any DB work. Returns aggregate USD/token sums only — no PII, no
# per-record content (threat T-04-22 accept).
# ---------------------------------------------------------------------------


def _make_llm_generation(
    db_session: Session,
    *,
    lane: str,
    model_slug: str,
    usd_cost: float,
    prompt_tokens: int = 100,
    completion_tokens: int = 50,
    created_at=None,
):
    """Seed a single llm_generations row for cost-aggregation tests."""
    from brave.core.models import LLMGeneration

    row = LLMGeneration(
        id=uuid.uuid4(),
        lane=lane,
        model_slug=model_slug,
        resolved_provider="openrouter",
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        usd_cost=usd_cost,
    )
    if created_at is not None:
        row.created_at = created_at
    db_session.add(row)
    db_session.flush()
    return row


def test_cost_no_bearer_returns_401(client, bearer_token):
    """GET /api/v1/cost with no Authorization header → 401 before any DB work."""
    r = client.get("/api/v1/cost?group_by=lane")
    assert r.status_code == 401


@pytest.mark.integration
def test_cost_group_by_lane_row_shape(authed_client, db_session: Session):
    """group_by=lane returns rows [{key, usd_cost, tokens, count}] from llm_generations."""
    lane = f"destinos-{uuid.uuid4().hex[:8]}"
    _make_llm_generation(
        db_session, lane=lane, model_slug="deepseek/deepseek-chat", usd_cost=0.10
    )
    _make_llm_generation(
        db_session, lane=lane, model_slug="deepseek/deepseek-chat", usd_cost=0.20
    )
    db_session.commit()

    r = authed_client.get("/api/v1/cost?group_by=lane")
    assert r.status_code == 200, f"Unexpected status: {r.status_code} — {r.text}"
    body = r.json()
    assert body["group_by"] == "lane"
    assert isinstance(body["rows"], list)

    row = next((r for r in body["rows"] if r["key"] == lane), None)
    assert row is not None, f"lane {lane!r} not in rows: {body['rows']}"
    for key in ("key", "usd_cost", "tokens", "count"):
        assert key in row, f"Missing key {key!r} in cost row: {row}"
    # two rows of 0.10 + 0.20 → 0.30 spend, 2 calls, (100+50)*2 = 300 tokens
    assert abs(row["usd_cost"] - 0.30) < 1e-6
    assert row["count"] == 2
    assert row["tokens"] == 300
    assert isinstance(row["usd_cost"], float)


@pytest.mark.integration
def test_cost_group_by_model_groups_by_model_slug(authed_client, db_session: Session):
    """group_by=model groups by model_slug (not lane)."""
    lane = f"atrativos-{uuid.uuid4().hex[:8]}"
    model = f"deepseek/v4-{uuid.uuid4().hex[:8]}"
    _make_llm_generation(db_session, lane=lane, model_slug=model, usd_cost=0.05)
    _make_llm_generation(db_session, lane=lane, model_slug=model, usd_cost=0.07)
    db_session.commit()

    body = authed_client.get("/api/v1/cost?group_by=model").json()
    assert body["group_by"] == "model"
    row = next((r for r in body["rows"] if r["key"] == model), None)
    assert row is not None, f"model {model!r} not in rows: {body['rows']}"
    assert abs(row["usd_cost"] - 0.12) < 1e-6
    assert row["count"] == 2


@pytest.mark.integration
def test_cost_since_filters_the_window(authed_client, db_session: Session):
    """A `since` filter restricts aggregation to rows whose created_at >= since."""
    from datetime import datetime, timedelta

    now = datetime.now(UTC)
    lane = f"destinos-win-{uuid.uuid4().hex[:8]}"
    # One old row (outside window), one recent row (inside window).
    _make_llm_generation(
        db_session,
        lane=lane,
        model_slug="deepseek/deepseek-chat",
        usd_cost=9.99,
        created_at=now - timedelta(days=10),
    )
    _make_llm_generation(
        db_session,
        lane=lane,
        model_slug="deepseek/deepseek-chat",
        usd_cost=0.01,
        created_at=now - timedelta(minutes=5),
    )
    db_session.commit()

    since = (now - timedelta(days=1)).isoformat()
    body = authed_client.get(
        "/api/v1/cost", params={"group_by": "lane", "since": since}
    ).json()
    row = next((r for r in body["rows"] if r["key"] == lane), None)
    assert row is not None, f"lane {lane!r} not in rows: {body['rows']}"
    # Only the recent 0.01 row falls in the window; the old 9.99 is excluded.
    assert abs(row["usd_cost"] - 0.01) < 1e-6
    assert row["count"] == 1


@pytest.mark.integration
def test_cost_empty_window_returns_empty_rows(authed_client):
    """A future `since` window with no matching rows → rows == [], 200 (no crash)."""
    from datetime import datetime, timedelta

    future = (datetime.now(UTC) + timedelta(days=365)).isoformat()
    r = authed_client.get(
        "/api/v1/cost", params={"group_by": "lane", "since": future}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["group_by"] == "lane"
    assert body["rows"] == []


# ===========================================================================
# Plan 04-08 / DASH-05 — conversation_message log (R2 Option B) + funnels +
# conversations endpoints.
# ===========================================================================

# The raw E.164 number used across these tests. The conversation_message log and
# every conversation endpoint MUST mask this — the full number must never appear
# in a persisted row or an API response (R3, T-04-24).
RAW_PHONE = "+5571999998888"


def _make_atrativo_in_progress(db_session: Session, uf: str = "BA"):
    """Seed a NascenteRecord + RioRecord(sub_state='whatsapp_in_progress') with a contact phone.

    The contact phone lives at normalized["contacts"]["phone_e164"] — the canonical
    ContactFinder location read by _extract_contact_phone (CR-03).
    """
    from brave.core.models import NascenteRecord, RioRecord

    src_ref = f"places:{uf}:{uuid.uuid4().hex}"
    nascente = NascenteRecord(
        id=uuid.uuid4(),
        source="places_discovery",
        source_ref=src_ref,
        entity_type="attraction",
        uf=uf,
        payload={"name": f"Atrativo {src_ref}", "place_id": src_ref},
        content_hash=f"hash:{src_ref}",
        version=1,
    )
    db_session.add(nascente)
    db_session.flush()

    rio = RioRecord(
        id=uuid.uuid4(),
        nascente_id=nascente.id,
        entity_type="attraction",
        uf=uf,
        routing="in_progress",
        sub_state="whatsapp_in_progress",
        normalized={
            "name": f"Atrativo {src_ref}",
            "window_open": True,
            "contacts": {"phone_e164": RAW_PHONE},
        },
    )
    db_session.add(rio)
    db_session.flush()
    return rio


# ---------------------------------------------------------------------------
# ConversationMessage model + mask_phone (offline unit)
# ---------------------------------------------------------------------------


def test_conversation_message_mask_phone_minimizes_pii():
    """mask_phone keeps only a prefix + 2-digit suffix — never the full E.164 (R3)."""
    from brave.core.models import mask_phone

    masked = mask_phone(RAW_PHONE)
    assert RAW_PHONE not in masked
    assert masked != RAW_PHONE
    # The middle digits are masked; nothing past the prefix's 5 chars is revealed raw.
    assert "99999" not in masked
    assert mask_phone("") == "***"
    assert mask_phone(None) == "***"


@pytest.mark.integration
def test_conversation_message_row_inserts(db_session: Session):
    """A ConversationMessage row inserts with the full append-only column set."""
    from brave.core.models import ConversationMessage, mask_phone

    rio = _make_atrativo_in_progress(db_session)
    msg = ConversationMessage(
        rio_id=rio.id,
        phone_masked=mask_phone(RAW_PHONE),
        direction="outbound",
        role="assistant",
        content="Olá! Sou da Norteia.",
        extracted=None,
    )
    db_session.add(msg)
    db_session.flush()

    fetched = db_session.get(ConversationMessage, msg.id)
    assert fetched is not None
    assert fetched.rio_id == rio.id
    assert fetched.direction == "outbound"
    assert fetched.created_at is not None
    # Masked phone only — the raw E.164 is never persisted (R3, T-04-24).
    assert RAW_PHONE not in fetched.phone_masked


# ---------------------------------------------------------------------------
# Migration 0005 — upgrade creates conversation_message, downgrade drops it
# ---------------------------------------------------------------------------


def _load_migration_0005():
    """Load the 0005 migration module by file path (alembic/versions is not a package)."""
    import importlib.util
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "0005_conversation_message.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0005", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_migration_0005_chains_to_0004():
    """Migration 0005 declares revision '0005' down_revision '0004' (chained)."""
    mod = _load_migration_0005()
    assert mod.revision == "0005"
    assert mod.down_revision == "0004"


@pytest.mark.integration
def test_migration_0005_upgrade_downgrade_roundtrip(db_engine):
    """upgrade() creates conversation_message (+ rio_id index); downgrade() drops it."""
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import inspect, text

    mod = _load_migration_0005()

    with db_engine.begin() as conn:
        # Ensure a clean slate (table may exist from a prior run / Base.metadata).
        conn.execute(text("DROP TABLE IF EXISTS conversation_message CASCADE"))

    with db_engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        # Operations.context binds alembic.op's module proxy to this Operations for
        # the duration of the block — so mod.upgrade()/downgrade() (which call op.*)
        # run against this connection.
        with Operations.context(ctx):
            mod.upgrade()
            insp = inspect(conn)
            cols = {c["name"] for c in insp.get_columns("conversation_message")}
            assert {
                "id",
                "rio_id",
                "phone_masked",
                "direction",
                "role",
                "content",
                "extracted",
                "created_at",
            } <= cols
            idx_names = {i["name"] for i in insp.get_indexes("conversation_message")}
            assert "ix_conversation_message_rio_id" in idx_names
            mod.downgrade()
            assert not inspect(conn).has_table("conversation_message")
            # Restore the table so the rest of the (shared-DB) suite still sees it —
            # the test DB carries the migration head; we must not leave it half-dropped.
            mod.upgrade()
            assert inspect(conn).has_table("conversation_message")


# ---------------------------------------------------------------------------
# Pipeline write-points: outreach (outbound) + resume (inbound + follow-up)
# ---------------------------------------------------------------------------


class _FakeGraph:
    """A stand-in compiled graph whose ainvoke returns a fixed final state."""

    def __init__(self, final_state: dict):
        self._final_state = final_state

    async def ainvoke(self, state, config=None):
        return self._final_state


class _FakeSaver:
    """No-op AsyncPostgresSaver replacement (no real checkpoint tables)."""

    @classmethod
    async def from_conn_string(cls, dsn):
        return cls()

    async def setup(self):
        return None


def _patch_graph(monkeypatch, final_state: dict):
    """Patch build_graph + AsyncPostgresSaver so the task runs offline deterministically."""
    import brave.lanes.atrativos.whatsapp_agent as agent_mod

    monkeypatch.setattr(
        agent_mod, "build_graph", lambda **kw: _FakeGraph(final_state)
    )
    import langgraph.checkpoint.postgres.aio as saver_mod

    monkeypatch.setattr(saver_mod, "AsyncPostgresSaver", _FakeSaver)


@pytest.mark.integration
def test_outreach_task_appends_outbound_message(db_session: Session, monkeypatch):
    """After outreach_task, an OUTBOUND ConversationMessage row exists for the rio_id.

    Content is sourced from the graph's FINAL state (the produced ask), NOT the empty
    message_text="" literal. Proves the outreach write-point (no boundary dropped).
    """
    from brave.core.models import ConversationMessage
    from brave.tasks import pipeline

    rio = _make_atrativo_in_progress(db_session)
    db_session.commit()

    ask = "Olá! Sou da Norteia, poderia confirmar se seu negócio está em funcionamento?"
    _patch_graph(
        monkeypatch,
        {"messages": [{"role": "assistant", "content": ask}], "extraction": None},
    )

    pipeline.outreach_task(str(rio.id))

    rows = list(
        db_session.scalars(
            select(ConversationMessage).where(ConversationMessage.rio_id == rio.id)
        ).all()
    )
    assert len(rows) == 1, f"expected one outbound row, got {len(rows)}"
    assert rows[0].direction == "outbound"
    assert rows[0].content == ask
    assert rows[0].content != ""
    # Masked phone only.
    assert RAW_PHONE not in rows[0].phone_masked


@pytest.mark.integration
def test_resume_task_appends_inbound_and_followup(db_session: Session, monkeypatch):
    """After resume_conversation_task, BOTH an INBOUND row (reply_text) AND a follow-up
    OUTBOUND row + extraction snapshot exist — both boundaries captured.
    """
    from brave.core.models import ConversationMessage
    from brave.tasks import pipeline

    rio = _make_atrativo_in_progress(db_session)
    db_session.commit()

    reply = "Sim, estamos abertos de terça a domingo das 9h às 18h."
    followup = "Obrigado! Pode confirmar o telefone de contato?"
    extraction = {"existe": True, "funcionando": True, "horarios": "ter-dom 9-18h"}
    # The graph's final state carries the full turn history: prior opening (outbound),
    # the owner's reply (inbound), and a new follow-up (outbound) + extraction.
    _patch_graph(
        monkeypatch,
        {
            "messages": [
                {"role": "user", "content": reply},
                {"role": "assistant", "content": followup},
            ],
            "extraction": extraction,
        },
    )

    pipeline.resume_conversation_task(str(rio.id), reply)

    rows = list(
        db_session.scalars(
            select(ConversationMessage)
            .where(ConversationMessage.rio_id == rio.id)
            .order_by(ConversationMessage.created_at.asc())
        ).all()
    )
    directions = [r.direction for r in rows]
    assert "inbound" in directions, f"no inbound row captured: {directions}"
    assert "outbound" in directions, f"no follow-up outbound row captured: {directions}"

    inbound_rows = [r for r in rows if r.direction == "inbound"]
    assert any(r.content == reply for r in inbound_rows), "reply_text not logged inbound"

    outbound_rows = [r for r in rows if r.direction == "outbound"]
    assert any(r.extracted == extraction for r in outbound_rows), (
        "extraction snapshot not attached to the follow-up outbound row"
    )
    # Masked phone only on every row.
    for r in rows:
        assert RAW_PHONE not in r.phone_masked


@pytest.mark.integration
def test_conversation_message_no_raw_phone_persisted(db_session: Session, monkeypatch):
    """No raw phone_e164 string is persisted anywhere in conversation_message (R3)."""
    from sqlalchemy import text

    from brave.tasks import pipeline

    rio = _make_atrativo_in_progress(db_session)
    db_session.commit()

    _patch_graph(
        monkeypatch,
        {"messages": [{"role": "assistant", "content": "Olá da Norteia"}], "extraction": None},
    )
    pipeline.outreach_task(str(rio.id))

    # Scan the raw stored rows — the full E.164 must not appear in phone_masked.
    hits = db_session.execute(
        text(
            "SELECT count(*) FROM conversation_message "
            "WHERE rio_id = :rid AND phone_masked = :raw"
        ),
        {"rid": str(rio.id), "raw": RAW_PHONE},
    ).scalar()
    assert hits == 0


@pytest.mark.integration
def test_outreach_append_committed_on_task_own_session(db_session: Session, monkeypatch):
    """The append is committed by the task's OWN single session — visible from a fresh read.

    db_session here is a DIFFERENT session than the one the task opens via _get_session();
    seeing the row proves the task committed it (not left uncommitted in a separate session).
    """
    from brave.core.models import ConversationMessage
    from brave.tasks import pipeline

    rio = _make_atrativo_in_progress(db_session)
    db_session.commit()

    _patch_graph(
        monkeypatch,
        {"messages": [{"role": "assistant", "content": "Mensagem da Norteia"}], "extraction": None},
    )
    pipeline.outreach_task(str(rio.id))

    db_session.expire_all()
    count = db_session.scalar(
        select(func.count(ConversationMessage.id)).where(
            ConversationMessage.rio_id == rio.id
        )
    )
    assert count == 1
