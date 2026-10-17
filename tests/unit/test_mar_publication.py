"""Interface tests for brave.core.mar.publication (promote / publish / republish_pending).

promote() commits, so these rows persist in norteia_brave_test (same as the other
integration tests that commit); every row carries a unique source_ref.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from brave.clients.null_norteia_api import NullNorteiaApiClient
from brave.config.settings import ScoreConfig
from brave.core.mar import sync
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


# ---------------------------------------------------------------------------
# Outbox outcome: retraction (F47), permanent 4xx (F46/F65), refresh (F48)
# ---------------------------------------------------------------------------

_ALL = 1_000_000  # the shared test DB keeps committed rows from other runs: never cap


def _status_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://api.test/api/internal/territorial/destinations")
    return httpx.HTTPStatusError(
        "boom", request=request, response=httpx.Response(status, text="nope", request=request)
    )


def _pending_ids(session: Session, **kw) -> set:
    return {rid for rid, _ in sync.pending_push_rows(session, _ALL, **kw)}


def _later() -> datetime:
    return datetime.now(UTC) + timedelta(hours=1)


def test_rio_leaving_mar_retracts_the_published_row(db_session: Session) -> None:
    rio = _in_mar(db_session)
    api = _FakeApi()
    publish(db_session, rio.id, api)

    rio.routing, rio.dlq_reason = "dlq", "community_error_report"
    db_session.commit()
    assert rio.id in _pending_ids(db_session, now=_later())

    assert publish(db_session, rio.id, api).status == "retracted"

    assert api.calls[-1][1]["visibility"] == "hidden"
    assert api.calls[-1][1]["source_ref"] == api.calls[0][1]["source_ref"]
    mar = _active_mar(db_session, rio.id)
    assert mar.retracted_at is not None
    assert rio.id not in _pending_ids(db_session, now=_later())
    assert publish(db_session, rio.id, api).status == "not_in_mar"  # no second hide
    assert len(api.calls) == 2


def test_never_pushed_row_is_retracted_without_a_call(db_session: Session) -> None:
    rio = _in_mar(db_session)
    rio.routing = "descarte"
    db_session.commit()
    api = _FakeApi()

    assert publish(db_session, rio.id, api).status == "retracted"

    assert api.calls == []
    assert rio.id not in _pending_ids(db_session, now=_later())


def test_revalidated_retracted_row_is_shown_again(db_session: Session) -> None:
    rio = _in_mar(db_session)
    api = _FakeApi()
    publish(db_session, rio.id, api)
    rio.routing = "dlq"
    db_session.commit()
    publish(db_session, rio.id, api)  # hidden

    promote(db_session, rio, actor="steward", enqueue=lambda _rid: None, config=CONFIG)
    assert rio.id in _pending_ids(db_session, now=_later())
    assert publish(db_session, rio.id, api).status == "pushed"

    assert api.calls[-1][1]["visibility"] == "published"
    assert _active_mar(db_session, rio.id).retracted_at is None


def test_superseded_unpushed_row_still_hides_the_visible_copy(db_session: Session) -> None:
    """v1 is visible on the API; a write supersedes it into v2 (pushed_at NULL) whose POST
    fails; then the Rio leaves Mar. pushed_at NULL used to read as "never sent" → stamped
    retracted with no POST, and the closed record stayed public for good."""
    from brave.core.rio.routing import route_by_score

    rio = _in_mar(db_session)
    api = _FakeApi()
    publish(db_session, rio.id, api)
    rio.normalized = {**rio.normalized, "name": "Nome Novo"}
    route_by_score(db_session, rio, CONFIG)
    db_session.commit()
    assert publish(db_session, rio.id, _FakeApi(exc=ApiDown("down"))).status == "api_down"
    assert _active_mar(db_session, rio.id).pushed_at is None  # v2, never accepted

    rio.routing, rio.dlq_reason = "dlq", "closed_temporarily"
    db_session.commit()
    assert publish(db_session, rio.id, api).status == "retracted"

    assert api.calls[-1][1]["visibility"] == "hidden"


def test_rescore_repush_leaves_the_api_visibility_alone(db_session: Session) -> None:
    """A community hide on the API must survive Brave's next re-push of the record."""
    from brave.core.rio.routing import route_by_score

    rio = _in_mar(db_session)
    api = _FakeApi()
    publish(db_session, rio.id, api)
    rio.normalized = {**rio.normalized, "name": "Outro Nome"}
    route_by_score(db_session, rio, CONFIG)
    db_session.commit()

    assert publish(db_session, rio.id, api).status == "pushed"

    assert all("visibility" not in payload for _, payload in api.calls)


def test_retracted_row_superseded_on_revalidation_is_still_shown(db_session: Session) -> None:
    """The re-show signal (retracted_at) survives a supersession: else the new row pushed
    without visibility and the API copy stayed hidden."""
    rio = _in_mar(db_session)
    api = _FakeApi()
    publish(db_session, rio.id, api)
    rio.routing = "dlq"
    db_session.commit()
    publish(db_session, rio.id, api)  # hidden

    rio.normalized = {**rio.normalized, "name": "Nome Revisado"}  # changes the snapshot
    promote(db_session, rio, actor="steward", enqueue=lambda _rid: None, config=CONFIG)
    assert publish(db_session, rio.id, api).status == "pushed"

    assert api.calls[-1][1]["visibility"] == "published"
    assert api.calls[-1][1]["tourist_name"] == "Nome Revisado"


def test_permanent_4xx_is_recorded_not_retried_and_leaves_the_dispatch(
    db_session: Session,
) -> None:
    rio = _in_mar(db_session)

    result = publish(db_session, rio.id, _FakeApi(exc=_status_error(422)))

    assert result.status == "rejected"  # no raise → the Celery task does not retry it
    mar = _active_mar(db_session, rio.id)
    assert mar.push_error.startswith("422") and mar.pushed_at is None
    # Out of the dispatch (it would hog the cap) until the day-long retry window passes.
    assert rio.id not in _pending_ids(db_session, now=_later())
    assert rio.id in _pending_ids(db_session, now=datetime.now(UTC) + timedelta(hours=25))
    assert sync.count_rejected_pushes(db_session) >= 1


@pytest.mark.parametrize("status", [500, 429, 401])
def test_transient_http_errors_still_raise_for_the_retry(db_session: Session, status) -> None:
    rio = _in_mar(db_session)

    with pytest.raises(httpx.HTTPStatusError):
        publish(db_session, rio.id, _FakeApi(exc=_status_error(status)))

    assert _active_mar(db_session, rio.id).push_error is None


def test_attempted_row_is_not_redispatched_within_the_window(db_session: Session) -> None:
    rio = _in_mar(db_session)
    publish(db_session, rio.id, _FakeApi(exc=ApiDown("down")))  # attempted, still unpushed

    assert rio.id not in _pending_ids(db_session)  # its retry chain is still running
    assert rio.id in _pending_ids(db_session, now=_later())


def test_update_after_promotion_reaches_the_api(db_session: Session) -> None:
    from brave.core.rio.routing import route_by_score

    rio = _in_mar(db_session)
    api = _FakeApi()
    publish(db_session, rio.id, api)
    old_mar_id = _active_mar(db_session, rio.id).id

    rio.normalized = {**rio.normalized, "name": "Nome Corrigido"}
    route_by_score(db_session, rio, CONFIG)  # every enrichment writer ends here
    db_session.commit()
    assert rio.id in _pending_ids(db_session, now=_later())

    assert publish(db_session, rio.id, api).status == "pushed"

    assert api.calls[-1][1]["tourist_name"] == "Nome Corrigido"
    mar = _active_mar(db_session, rio.id)
    assert mar.id != old_mar_id and mar.pushed_at is not None  # superseded (D-03)
    assert rio.id not in _pending_ids(db_session, now=_later())


def test_rescore_without_payload_change_is_not_reposted(db_session: Session) -> None:
    from brave.core.rio.routing import route_by_score

    rio = _in_mar(db_session)
    api = _FakeApi()
    publish(db_session, rio.id, api)

    rio.normalized = {**rio.normalized, "descricao_attempts": 1}  # internal-only key
    route_by_score(db_session, rio, CONFIG)
    db_session.commit()

    assert publish(db_session, rio.id, api).status == "unchanged"
    assert len(api.calls) == 1
    assert rio.id not in _pending_ids(db_session, now=_later())


# ---------------------------------------------------------------------------
# validate_and_promote_rio guards: descarte (F50), row lock (F49)
# ---------------------------------------------------------------------------


def test_descarte_is_never_promoted(db_session: Session) -> None:
    rio = _make_rio(db_session)
    rio.routing, rio.dlq_reason = "descarte", "closed_place"
    db_session.commit()
    queued: list[str] = []

    p = promote(db_session, rio, actor="steward", enqueue=queued.append, config=CONFIG)

    assert p.routing == "descarte" and p.mar_id is None and queued == []
    assert p.held_reason == "closed_place"
    assert _active_mar(db_session, rio.id) is None
    assert "validacao_humana_value" not in rio.normalized or (
        rio.normalized["validacao_humana_value"] == 0.0
    )


def test_promote_waits_for_a_locked_writer_and_keeps_its_write(db_engine) -> None:
    """A task holding the row FOR UPDATE commits google_enriched; the concurrent promote
    must not write its stale copy of normalized over it (lost update, F49)."""
    import threading

    from sqlalchemy.orm import sessionmaker

    factory = sessionmaker(bind=db_engine)
    setup = factory()
    rio_id = _make_rio(setup).id
    setup.close()

    writer = factory()
    locked = writer.get(RioRecord, rio_id, with_for_update=True)
    promoter = factory()
    rio = promoter.get(RioRecord, rio_id)  # unlocked read, before the writer commits
    _ = rio.normalized

    done = threading.Event()

    def _promote() -> None:
        promote(promoter, rio, actor="steward", enqueue=lambda _rid: None, config=CONFIG)
        done.set()

    t = threading.Thread(target=_promote)
    t.start()
    assert not done.wait(0.5)  # blocked on the writer's row lock
    locked.normalized = {**locked.normalized, "google_enriched": True}
    writer.commit()
    t.join(10)
    writer.close()
    promoter.close()

    check = factory()
    final = check.get(RioRecord, rio_id).normalized
    check.close()
    assert final["google_enriched"] is True
    assert final["validacao_humana_value"] == 100.0
