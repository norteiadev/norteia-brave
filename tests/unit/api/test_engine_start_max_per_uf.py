"""POST /engine/start: optional per-UF attraction cap (max_atrativos_per_uf).

The operator can throttle a test run by capping attractions ingested per UF. The
field is optional (absent/null ⇒ no cap, full sweep) and validated as a positive
integer BEFORE any engine-state mutation — a bad value 422s without flipping state,
mirroring the depth/source guards. When valid it is threaded to engine_sweep_run as
``max_per_uf``.

Fully offline: fakeredis + MagicMock db + monkeypatched effective-config load and a
captured engine_sweep_run.delay. Uses source='default' so the TA session gate (R2) is
not on the path.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import fakeredis
import pytest
from fastapi import HTTPException

# engine_start does `from brave.tasks.pipeline import engine_sweep_run` INSIDE the
# handler, so patching the module attribute with a MagicMock is what its `.delay(...)`
# call resolves to. (Patching `.delay` on the real Celery PromiseProxy does NOT stick —
# the proxy forwards the call to the real task, which then raises broker-down and is
# swallowed by the offline dispatch branch, so nothing is captured.)
from brave.api.routers import engine as engine_router
from brave.config.settings import AppConfig
from brave.core import engine as ce


def _config_with(sources: dict[str, bool]) -> AppConfig:
    return AppConfig().model_copy(update={"sources": sources})


def _patch_config(monkeypatch) -> None:
    cfg = _config_with({"default": True, "tripadvisor": True})
    monkeypatch.setattr(engine_router, "load_effective_config", lambda db, redis: cfg)


def _capture_delay(monkeypatch) -> MagicMock:
    """Swap engine_sweep_run for a MagicMock; return it so tests read .delay.call_args."""
    import brave.tasks.pipeline as pipeline

    mock_task = MagicMock()
    monkeypatch.setattr(pipeline, "engine_sweep_run", mock_task)
    return mock_task


def test_valid_max_per_uf_reaches_sweep(monkeypatch):
    """A positive integer is threaded to engine_sweep_run as max_per_uf and echoed back."""
    _patch_config(monkeypatch)
    mock_task = _capture_delay(monkeypatch)

    fake = fakeredis.FakeStrictRedis()
    result = engine_router.engine_start(
        redis=fake,
        body={"depth": "nascente", "source": "default", "max_atrativos_per_uf": 5},
        db=MagicMock(),
    )

    assert result["status"] == "started"
    assert result["max_atrativos_per_uf"] == 5
    assert mock_task.delay.call_args.kwargs["max_per_uf"] == 5


def test_absent_max_per_uf_is_uncapped(monkeypatch):
    """Omitting the field ⇒ max_per_uf=None (full sweep, unchanged behavior)."""
    _patch_config(monkeypatch)
    mock_task = _capture_delay(monkeypatch)

    fake = fakeredis.FakeStrictRedis()
    result = engine_router.engine_start(
        redis=fake,
        body={"depth": "nascente", "source": "default"},
        db=MagicMock(),
    )

    assert result["max_atrativos_per_uf"] is None
    assert mock_task.delay.call_args.kwargs["max_per_uf"] is None


@pytest.mark.parametrize("bad", [0, -1, -5, "5", 2.5, True, False])
def test_invalid_max_per_uf_is_422_and_leaves_state_idle(monkeypatch, bad):
    """Non-positive / non-int (incl. bool) ⇒ 422 before any engine-state mutation."""
    _patch_config(monkeypatch)
    mock_task = _capture_delay(monkeypatch)

    fake = fakeredis.FakeStrictRedis()
    with pytest.raises(HTTPException) as exc:
        engine_router.engine_start(
            redis=fake,
            body={
                "depth": "nascente",
                "source": "default",
                "max_atrativos_per_uf": bad,
            },
            db=MagicMock(),
        )

    assert exc.value.status_code == 422
    assert "max_atrativos_per_uf" in str(exc.value.detail)
    assert ce.get_state(fake) == ce.IDLE
    # Rejected before dispatch — the sweep was never launched.
    mock_task.delay.assert_not_called()
