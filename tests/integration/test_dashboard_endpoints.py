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

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

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
