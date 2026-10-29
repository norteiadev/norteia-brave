"""POST /engine/start with action="images": the per-UF image lane run.

No depth, no source, no TA session — but 409 unless the lane may really run (real
externals AND images_enrichment_enabled). The orchestrator then fans out images_uf.
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
        update={"run_real_externals": True, "images_enrichment_enabled": True, **flags}
    )
    monkeypatch.setattr(engine_router, "load_effective_config", lambda db, redis: cfg)
    import brave.tasks.pipeline as pipeline

    mock_task = MagicMock()
    monkeypatch.setattr(pipeline, "engine_sweep_run", mock_task)
    return mock_task


def test_images_starts_without_depth(monkeypatch):
    mock_task = _setup(monkeypatch)
    fake = fakeredis.FakeStrictRedis()
    db = MagicMock()

    result = engine_router.engine_start(
        redis=fake, body={"action": "images", "ufs": ["SE"], "max_atrativos_per_uf": 20}, db=db
    )

    assert result["status"] == "started" and result["action"] == "images"
    kwargs = mock_task.delay.call_args.kwargs
    assert (kwargs["action"], kwargs["ufs"], kwargs["max_per_uf"]) == ("images", ["SE"], 20)
    run = db.add.call_args.args[0]
    assert (run.depth, run.source) == ("imagens", "imagens")


@pytest.mark.parametrize(
    "flags", [{"images_enrichment_enabled": False}, {"run_real_externals": False}]
)
def test_images_off_is_409(monkeypatch, flags):
    mock_task = _setup(monkeypatch, **flags)
    fake = fakeredis.FakeStrictRedis()

    with pytest.raises(HTTPException) as exc:
        engine_router.engine_start(redis=fake, body={"action": "images"}, db=MagicMock())

    assert exc.value.status_code == 409
    assert ce.get_state(fake) == ce.IDLE
    mock_task.delay.assert_not_called()


def test_orchestrator_fans_out_images_uf(monkeypatch):
    import brave.tasks.pipeline as pipeline

    fake = fakeredis.FakeStrictRedis()
    monkeypatch.setattr("redis.from_url", lambda *_a, **_k: fake)
    monkeypatch.setenv("BRAVE_ENGINE_UF_DELAY_SECONDS", "0")
    monkeypatch.setattr(pipeline, "_record_run_cap", lambda *a: None)
    monkeypatch.setattr(pipeline, "_lifecycle", lambda *a, **k: False)
    run_id = ce.start(fake, None, action="images", depth="imagens", source="imagens",
                      ufs=["SE", "AL"], lane="both")
    producer = MagicMock()
    monkeypatch.setattr(pipeline, "images_uf", producer)

    pipeline.engine_sweep_run.run(ufs=["SE", "AL"], run_id=run_id, max_per_uf=20, action="images")

    assert [c.args for c in producer.delay.call_args_list] == [("SE", 20), ("AL", 20)]
