"""Push tasks skip the POST while the payload hash matches, and re-push when it changes."""

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from brave.tasks import pipeline


class _FakeApi:
    """Stands in for NorteiaApiClient (isinstance target) and counts POSTs."""

    calls: list[dict] = []
    fail = False

    def __init__(self, **_: object) -> None:
        pass

    async def __aenter__(self) -> "_FakeApi":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def push_attraction(self, payload: dict) -> dict:
        if _FakeApi.fail:
            raise pipeline.PermanentError("422")
        _FakeApi.calls.append(payload)
        return {}


def test_push_skipped_on_same_hash_and_redone_on_new_hash():
    _FakeApi.calls, _FakeApi.fail = [], False
    rio = SimpleNamespace(routing="mar", entity_type="attraction")
    mar = SimpleNamespace(push_hash=None, pushed_at=None)
    session = MagicMock()
    session.get.return_value = rio
    payload = {"source_ref": "ta:1", "name": "Farol"}

    with (
        patch.object(pipeline, "_get_session", return_value=(session, None)),
        patch.object(pipeline, "AppConfig", return_value=SimpleNamespace(run_real_externals=True)),
        patch.object(pipeline, "NorteiaApiClient", _FakeApi),
        patch.object(pipeline, "_build_push_payload", side_effect=lambda *_: dict(payload)),
        patch("brave.core.mar.service.promote_to_mar", return_value=mar),
    ):
        run = lambda: pipeline.push_attraction_task.run(str(uuid.uuid4()))  # noqa: E731

        # A failed push must not stamp the hash.
        _FakeApi.fail = True
        run()
        assert mar.push_hash is None and _FakeApi.calls == []

        _FakeApi.fail = False
        run()
        assert len(_FakeApi.calls) == 1
        assert mar.push_hash == pipeline._push_hash(payload) and mar.pushed_at is not None

        run()  # same payload -> no POST
        assert len(_FakeApi.calls) == 1

        payload["name"] = "Farol da Barra"
        run()  # changed payload -> POST again, hash moves
        assert len(_FakeApi.calls) == 2
        assert mar.push_hash == pipeline._push_hash(payload)

        mar.push_hash = None  # the documented force path
        run()
        assert len(_FakeApi.calls) == 3
