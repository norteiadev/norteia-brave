"""Interface tests for brave.core.mar.publication (promote / publish / republish_pending).

promote() commits, so these rows persist in norteia_brave_test (same as the other
integration tests that commit); every row carries a unique source_ref.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from brave.clients.null_norteia_api import NullNorteiaApiClient
from brave.config.settings import ScoreConfig
from brave.core.mar.publication import (
    Promotion,
    promote,
    publish,
    republish_pending,
)
from brave.core.models import AuditLog, MarRecord, RioRecord
from brave.shared.exceptions import ApiDown

pytestmark = pytest.mark.integration

CONFIG = ScoreConfig()


class _FakeApi:
    def __init__(self, result: bool = True, exc: Exception | None = None) -> None:
        self.result = result
        self.exc = exc
        self.calls: list[tuple[str, dict]] = []

    async def push(self, entity_type: str, payload: dict) -> bool:
        if self.exc is not None:
            raise self.exc
        self.calls.append((entity_type, payload))
        return self.result


def _make_rio(
    session: Session,
    *,
    entity_type: str = "destination",
    corroboracao: float = 50.0,
    low: bool = False,
) -> RioRecord:
    from brave.core.nascente.service import store_raw
    from brave.core.rio.routing import process_nascente_record

    tag = uuid.uuid4().hex[:8]
    value = 0.0 if low else 100.0
    nascente = store_raw(
        session=session,
        source="mtur",
        source_ref=f"mtur:BA:pub-{tag}",
        entity_type=entity_type,
        uf="BA",
        payload={
            "name": f"Publication Test {tag}",
            "municipio_id": f"292{tag[:4]}",
            "uf": "BA",
            "origem_value": value,
            "completude_value": value,
            "corroboracao_value": 0.0 if low else corroboracao,
            "atualidade_value": value,
            "validacao_humana_value": 0.0,
        },
    )
    session.flush()
    rio = process_nascente_record(session, nascente, CONFIG)
    rio.routing = "dlq"
    rio.dlq_reason = "score=below_threshold"
    session.flush()
    session.commit()
    return rio


def _audit_rows(session: Session, rio_id: uuid.UUID) -> list[AuditLog]:
    return list(session.scalars(select(AuditLog).where(AuditLog.record_id == rio_id)))


def _active_mar(session: Session, rio_id: uuid.UUID) -> MarRecord | None:
    return session.scalars(
        select(MarRecord).where(MarRecord.rio_id == rio_id, MarRecord.superseded_by_id.is_(None))
    ).first()


def test_promote_lands_in_mar_audits_commits_then_enqueues(db_session: Session) -> None:
    rio = _make_rio(db_session)
    queued: list[str] = []

    p = promote(db_session, rio, actor="steward", enqueue=queued.append, config=CONFIG)

    assert p == Promotion(routing="mar", mar_id=p.mar_id, held_reason=None, push_queued=True)
    assert p.mar_id is not None
    assert queued == [str(rio.id)]
    audits = _audit_rows(db_session, rio.id)
    assert [(a.action, a.actor) for a in audits] == [("dlq_validated", "steward")]
    db_session.expire_all()
    assert _active_mar(db_session, rio.id) is not None


def test_promote_held_by_backstop(db_session: Session) -> None:
    rio = _make_rio(db_session, entity_type="attraction")
    queued: list[str] = []

    p = promote(
        db_session, rio, actor="steward", enqueue=queued.append,
        action="transition_mar", held_action="promote_held", config=CONFIG,
    )

    assert p.routing == "dlq"
    assert p.held_reason == "no_recent_reviews"
    assert p.mar_id is None
    assert p.push_queued is False
    assert queued == []
    assert [a.action for a in _audit_rows(db_session, rio.id)] == ["promote_held"]


def test_promote_held_by_score(db_session: Session) -> None:
    rio = _make_rio(db_session, low=True)
    queued: list[str] = []

    p = promote(db_session, rio, actor="steward", enqueue=queued.append, config=CONFIG)

    assert p.routing != "mar"
    assert p.push_queued is False
    assert queued == []


def test_promote_dispatch_failure_never_raises(db_session: Session) -> None:
    rio = _make_rio(db_session)

    def _broken(_rid: str) -> None:
        raise RuntimeError("broker down")

    p = promote(db_session, rio, actor="steward", enqueue=_broken, config=CONFIG)

    assert p.routing == "mar"
    assert p.push_queued is False
    db_session.expire_all()
    mar = _active_mar(db_session, rio.id)
    assert mar is not None and mar.pushed_at is None


def _in_mar(session: Session) -> RioRecord:
    rio = _make_rio(session)
    promote(session, rio, actor="steward", enqueue=lambda _rid: None, config=CONFIG)
    return rio


def test_publish_pushes_and_stamps(db_session: Session) -> None:
    rio = _in_mar(db_session)
    api = _FakeApi()

    assert publish(db_session, rio.id, api).status == "pushed"

    mar = _active_mar(db_session, rio.id)
    assert mar.push_hash and mar.pushed_at is not None
    assert len(api.calls) == 1 and api.calls[0][0] == "destination"
    assert api.calls[0][1]["source_ref"] == mar.source_ref


def test_publish_same_hash_skips_post(db_session: Session) -> None:
    rio = _in_mar(db_session)
    api = _FakeApi()

    publish(db_session, rio.id, api)
    assert publish(db_session, rio.id, api).status == "unchanged"
    assert len(api.calls) == 1


def test_publish_null_adapter_does_not_stamp(db_session: Session) -> None:
    rio = _in_mar(db_session)

    assert publish(db_session, rio.id, NullNorteiaApiClient()).status == "not_sent"

    mar = _active_mar(db_session, rio.id)
    assert mar.push_hash is None and mar.pushed_at is None


def test_publish_api_down_stays_pending(db_session: Session) -> None:
    rio = _in_mar(db_session)

    assert publish(db_session, rio.id, _FakeApi(exc=ApiDown("down"))).status == "api_down"

    assert _active_mar(db_session, rio.id).pushed_at is None


def test_publish_never_promotes(db_session: Session) -> None:
    rio = _make_rio(db_session)
    api = _FakeApi()

    assert publish(db_session, rio.id, api).status == "not_in_mar"
    assert api.calls == []
    assert _active_mar(db_session, rio.id) is None


def test_republish_pending_enqueues_every_row() -> None:
    a, d = uuid.uuid4(), uuid.uuid4()
    session = MagicMock()
    session.execute.return_value.all.return_value = [(a, "attraction"), (d, "destination")]
    queued: list[str] = []

    assert republish_pending(session, queued.append) == 2
    assert queued == [str(a), str(d)]
