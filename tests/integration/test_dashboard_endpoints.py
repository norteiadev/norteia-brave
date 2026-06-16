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

import pytest
from fastapi import HTTPException

from brave.config.settings import DashboardConfig

BEARER_TOKEN = "test-dashboard-bearer-token-abc123"


@pytest.fixture
def bearer_token(monkeypatch):
    """Set and return a test dashboard Bearer token (offline)."""
    monkeypatch.setenv("BRAVE_DASHBOARD_BEARER_TOKEN", BEARER_TOKEN)
    return BEARER_TOKEN


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
