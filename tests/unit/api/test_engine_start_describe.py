"""POST /engine/start with action="describe": the per-UF description run.

No depth, no source, no TA session — but 409 unless the inline copywriter is really on
(real externals, description flag ON, batch lane OFF). The default action stays the
sweep, whose depth is still required (422).

Offline: fakeredis + MagicMock db + monkeypatched effective config + a captured
engine_sweep_run.delay (same pattern as test_engine_start_max_per_uf).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import fakeredis
import pytest
from fastapi import HTTPException

from brave.api.routers import engine as engine_router
from brave.config.settings import AppConfig
from brave.core import engine as ce


def _setup(monkeypatch, **flags) -> MagicMock:
    cfg = AppConfig().model_copy(
        update={
            "run_real_externals": True,
            "description_enrichment_enabled": True,
            "atrativo_description_batch_enabled": False,
            "atrativo_description_cascade_enabled": False,
            **flags,
        }
    )
    monkeypatch.setattr(engine_router, "load_effective_config", lambda db, redis: cfg)
    import brave.tasks.pipeline as pipeline

    mock_task = MagicMock()
    monkeypatch.setattr(pipeline, "engine_sweep_run", mock_task)
    return mock_task


def test_describe_without_depth_starts(monkeypatch):
    mock_task = _setup(monkeypatch)
    fake = fakeredis.FakeStrictRedis()  # no TA session key — not needed to describe
    db = MagicMock()

    result = engine_router.engine_start(
        redis=fake,
        body={"action": "describe", "ufs": ["SP"], "max_atrativos_per_uf": 3},
        db=db,
    )

    assert result["status"] == "started" and result["action"] == "describe"
    kwargs = mock_task.delay.call_args.kwargs
    assert kwargs["action"] == "describe"
    assert kwargs["ufs"] == ["SP"] and kwargs["max_per_uf"] == 3
    assert ce.get_state(fake) == ce.RUNNING
    run = db.add.call_args.args[0]
    assert (run.depth, run.source, run.lane) == ("descricao", "descricao", "atrativos")


@pytest.mark.parametrize(
    "flags",
    [
        {"description_enrichment_enabled": False},
        {"atrativo_description_batch_enabled": True},
        {"run_real_externals": False},
    ],
)
def test_describe_with_description_off_is_409(monkeypatch, flags):
    mock_task = _setup(monkeypatch, **flags)
    fake = fakeredis.FakeStrictRedis()

    with pytest.raises(HTTPException) as exc:
        engine_router.engine_start(redis=fake, body={"action": "describe"}, db=MagicMock())

    assert exc.value.status_code == 409
    assert ce.get_state(fake) == ce.IDLE
    mock_task.delay.assert_not_called()


def test_describe_with_misconfigured_cascade_is_409(monkeypatch):
    """Cascade on with a gemini writer and no key: every record would fail before the
    agent, so /start refuses instead of walking the backlog writing nothing."""
    from brave.config.settings import LLMConfig

    mock_task = _setup(monkeypatch, atrativo_description_cascade_enabled=True)
    env = AppConfig().model_copy(
        update={
            "run_real_externals": True,
            "atrativo_cascade_model": "gemini-2.5-flash",
            "llm": LLMConfig(openrouter_api_key="or", gemini_api_key=""),
        }
    )
    monkeypatch.setattr(engine_router, "AppConfig", lambda: env)
    fake = fakeredis.FakeStrictRedis()

    with pytest.raises(HTTPException) as exc:
        engine_router.engine_start(redis=fake, body={"action": "describe"}, db=MagicMock())

    assert exc.value.status_code == 409
    assert "BRAVE_LLM_GEMINI_API_KEY" in exc.value.detail
    assert ce.get_state(fake) == ce.IDLE
    mock_task.delay.assert_not_called()


def test_sweep_without_depth_is_still_422(monkeypatch):
    mock_task = _setup(monkeypatch)
    fake = fakeredis.FakeStrictRedis()

    with pytest.raises(HTTPException) as exc:
        engine_router.engine_start(redis=fake, body={"source": "default"}, db=MagicMock())

    assert exc.value.status_code == 422
    mock_task.delay.assert_not_called()


def test_unknown_action_is_422(monkeypatch):
    _setup(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        engine_router.engine_start(
            redis=fakeredis.FakeStrictRedis(), body={"action": "nuke"}, db=MagicMock()
        )
    assert exc.value.status_code == 422
