"""brave.backfill_ta_atrativos (contract C3): selection, the per-record batch, every stop,
the chain/token hand-over and the daily-cap auto-resume tick.

TripAdvisorAtrativosIngest.backfill_record (C2) is faked — this suite covers the
orchestration only. No broker (apply_async/delay are spies), fakeredis for the engine,
a MagicMock session except for the selector test, which runs the real SQL against the
test DB inside a rolled-back transaction (skipped without BRAVE_DB_URL).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import fakeredis
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from brave.config.settings import AppConfig
from brave.core import engine as collection_engine
from brave.core.models import RioRecord
from brave.shared.exceptions import (
    CostGuardError,
    ProviderBalanceError,
    SourceRateLimitError,
    SourceSchemaError,
)
from brave.tasks import pipeline


class _FakeIngest:
    """backfill_record per rio id: a status string, or an exception to raise."""

    outcomes: dict[uuid.UUID, Any] = {}
    seen: list[uuid.UUID] = []

    def __init__(self, **kw: Any) -> None:
        _FakeIngest.ctor = kw

    async def backfill_record(self, rio: Any) -> str:
        _FakeIngest.seen.append(rio.id)
        out = _FakeIngest.outcomes.get(rio.id, "ta+places")
        if isinstance(out, BaseException):
            raise out
        return out


@pytest.fixture
def env(monkeypatch):
    """Wire the task to fakes; returns a namespace with redis, session, spies, run_id."""
    from brave.clients.factory import Clients

    rc = fakeredis.FakeRedis()
    monkeypatch.setattr("redis.from_url", lambda *a, **k: rc)
    session = MagicMock()
    # The task's own session first; the lifecycle's finalize gets a separate one.
    sessions = iter([session])
    monkeypatch.setattr(pipeline, "_get_session", lambda: (next(sessions, MagicMock()), None))
    cfg = AppConfig(run_real_externals=True, ta_backfill_batch_size=3)
    monkeypatch.setattr(pipeline, "_load_config", lambda _s: cfg)
    monkeypatch.setattr(pipeline, "clients_for", lambda *a, **k: Clients(tripadvisor=MagicMock()))
    places_agent = object()
    monkeypatch.setattr(pipeline, "_inline_places_agent", lambda *a, **k: ([], places_agent))
    monkeypatch.setattr(
        "brave.domains.tripadvisor.atrativos.TripAdvisorAtrativosIngest", _FakeIngest
    )
    monkeypatch.setattr("brave.domains.tripadvisor.ibge.load_ibge_municipios", lambda s: [])
    marked: list[bool] = []
    monkeypatch.setattr(pipeline, "_mark_needs_bootstrap", lambda: marked.append(True))
    links: list[dict] = []
    monkeypatch.setattr(
        pipeline.backfill_ta_atrativos,
        "apply_async",
        lambda args=(), kwargs=None, countdown=None: links.append({**kwargs, "countdown": countdown}),
    )
    _FakeIngest.outcomes, _FakeIngest.seen = {}, []

    run_id = collection_engine.start(
        rc, None, action="backfill", depth="backfill", source="ta_backfill",
        ufs=["BR"], lane="atrativos",
    )
    claimed = collection_engine.claim_producer(rc, run_id)
    collection_engine.progress(rc, run_id, uf="BR")
    collection_engine.dispatch_finished(rc, None, run_id)

    class _Env:
        pass

    e = _Env()
    e.rc, e.session, e.cfg, e.links, e.marked, e.run_id = rc, session, cfg, links, marked, claimed
    e.places_agent = places_agent

    def select_ids(ids):
        session.scalars.return_value.all.return_value = ids
        session.get.side_effect = lambda _m, rid, **kw: MagicMock(id=rid)

    e.select_ids = select_ids
    return e


def _run(env, **kw) -> str:
    return pipeline.backfill_ta_atrativos.__wrapped__.__func__(MagicMock(), env.run_id, **kw)


def _ids(n: int) -> list[uuid.UUID]:
    return sorted(uuid.uuid4() for _ in range(n))


def _progress(rc) -> dict[str, str]:
    return {k.decode(): v.decode() for k, v in rc.hgetall(pipeline._BACKFILL_PROGRESS_KEY).items()}


def _run_status(rc) -> str:
    return collection_engine.get_status(rc)["state"]


def test_full_batch_commits_per_record_and_hands_the_token_on(env):
    ids = _ids(3)
    env.select_ids(ids)

    assert _run(env) == "chained"

    assert _FakeIngest.seen == ids
    assert env.session.commit.call_count == 3
    assert env.links == [{"run_id": env.run_id, "after_id": str(ids[-1]), "countdown": None}]
    assert _FakeIngest.ctor["places_agent"] is env.places_agent  # the per-UF sweep's agent
    assert _run_status(env.rc) == collection_engine.RUNNING  # the next link holds the token
    assert _progress(env.rc)["processed"] == "3"


def test_short_batch_ends_the_run(env):
    env.select_ids(_ids(2))

    assert _run(env) == "done"

    assert env.links == []
    assert _run_status(env.rc) == collection_engine.IDLE
    assert _progress(env.rc)["state"] == "done"


def test_record_failure_is_skipped_not_fatal(env):
    ids = _ids(2)
    env.select_ids(ids)
    _FakeIngest.outcomes = {ids[0]: RuntimeError("bad card")}

    _run(env)

    assert _FakeIngest.seen == ids
    env.session.rollback.assert_called()
    assert _progress(env.rc)["failed"] == "1" and _progress(env.rc)["processed"] == "1"


@pytest.mark.parametrize(
    ("exc", "reason"),
    [
        (ProviderBalanceError("places"), "provider_balance"),
        (CostGuardError("cap"), "daily_budget"),
    ],
)
def test_places_exhaustion_pauses_and_marks_nothing(env, exc, reason):
    """Budget/billing wall: the record in flight keeps its TA side (backfill_record undid
    only the Places savepoint → committed, no google_enriched), pause the motor with a
    reason for the backfill, end the chain — the run closes 'parcial'."""
    ids = _ids(3)
    env.select_ids(ids)
    _FakeIngest.outcomes = {ids[1]: exc}

    _run(env)

    assert _FakeIngest.seen == ids[:2]  # stopped at the wall
    assert env.session.commit.call_count == 2  # the record before it + the TA side of it
    assert env.links == []
    status = collection_engine.get_status(env.rc)
    assert status["mode"] == collection_engine.PAUSADO
    assert status["pause_reason"]["reason"] == reason
    assert status["pause_reason"]["action"] == "backfill"
    assert status["state"] == collection_engine.IDLE  # run ended; Continuar won't 409
    assert _progress(env.rc)["state"] == "paused"


def test_session_error_marks_bootstrap_and_turns_motor_off(env):
    from brave.domains.tripadvisor.client import SessionExpiredError

    ids = _ids(2)
    env.select_ids(ids)
    _FakeIngest.outcomes = {ids[0]: SessionExpiredError("403")}

    _run(env)

    assert env.marked == [True]
    assert collection_engine.get_mode(env.rc) == collection_engine.DESLIGADO
    assert env.links == []
    assert _progress(env.rc)["state"] == "stopped_needs_bootstrap"


def test_rate_limit_backs_off_on_the_same_cursor(env):
    """429: the next link redoes the throttled record after Retry-After; session kept."""
    ids = _ids(3)
    env.select_ids(ids)
    _FakeIngest.outcomes = {ids[1]: SourceRateLimitError(retry_after=42)}

    _run(env, after_id=None)

    assert env.links == [
        {"run_id": env.run_id, "after_id": str(ids[0]), "countdown": 42, "backoffs": 1}
    ]
    assert env.marked == []
    assert collection_engine.get_mode(env.rc) == collection_engine.LIGADO


def test_rate_limit_without_progress_gives_up_after_the_cap(env):
    ids = _ids(1)
    env.select_ids(ids)
    _FakeIngest.outcomes = {ids[0]: SourceRateLimitError(retry_after=5)}

    _run(env, backoffs=pipeline._MAX_RATE_LIMIT_BACKOFFS)

    assert env.links == []
    assert _progress(env.rc)["reason"] == "rate_limit"
    assert _run_status(env.rc) == collection_engine.IDLE


def test_schema_error_on_every_record_stops_the_run(env):
    """A retired query id fails every card alike: stop after the threshold in a row."""
    ids = _ids(pipeline._BACKFILL_MAX_CONSECUTIVE_SCHEMA_ERRORS)
    env.select_ids(ids)
    _FakeIngest.outcomes = {i: SourceSchemaError("PersistedQueryNotFound") for i in ids}

    _run(env)

    assert _FakeIngest.seen == ids and env.links == []
    assert _progress(env.rc)["reason"] == "schema"


def test_lone_schema_error_skips_the_record_not_the_run(env):
    """One delisted locationId (locationData null / partial ``errors``) must not block every
    later id: it is a per-record failure, the rest of the batch completes and chains on."""
    ids = _ids(3)
    env.select_ids(ids)
    _FakeIngest.outcomes = {ids[0]: SourceSchemaError("locationData is null")}

    assert _run(env) == "chained"

    assert _FakeIngest.seen == ids
    assert env.links == [{"run_id": env.run_id, "after_id": str(ids[-1]), "countdown": None}]
    assert _progress(env.rc)["failed"] == "1" and _progress(env.rc)["processed"] == "2"


def test_engine_halt_stops_before_the_next_record(env):
    env.select_ids(_ids(3))
    collection_engine.set_mode(env.rc, collection_engine.PAUSADO)

    _run(env)

    assert _FakeIngest.seen == [] and env.links == []


def test_plain_pause_leaves_a_backfill_resume_for_continuar(env):
    """A Painel pause (to promote-bulk mid-backfill) used to end the run with no reason →
    motor DESLIGADO, nothing to resume. Now Continuar (action "backfill") restarts it."""
    env.select_ids(_ids(3))
    collection_engine.set_mode(env.rc, collection_engine.PAUSADO)

    _run(env)

    status = collection_engine.get_status(env.rc)
    assert status["mode"] == collection_engine.PAUSADO
    assert status["pause_reason"]["reason"] == "manual"
    assert status["pause_reason"]["action"] == "backfill"
    assert status["state"] == collection_engine.IDLE


def test_desligar_mid_backfill_records_no_resume(env):
    env.select_ids(_ids(3))
    collection_engine.set_mode(env.rc, collection_engine.DESLIGADO)

    _run(env)

    assert collection_engine.get_pause_reason(env.rc) is None
    assert collection_engine.get_mode(env.rc) == collection_engine.DESLIGADO


def test_progress_names_the_places_key_the_worker_uses(env, monkeypatch):
    """A .env key swap reaches the worker only on a container recreate: the progress shows
    which key it actually spends so the operator sees a swap that did not take."""
    from brave.observability.cost_guard import places_key_id

    monkeypatch.setenv("BRAVE_PLACES_API_KEY", "key-worker")
    env.select_ids(_ids(1))

    _run(env)

    assert _progress(env.rc)["places_key_id"] == places_key_id("key-worker")


def test_time_budget_cut_chains_from_the_last_done_record(env, monkeypatch):
    """The cooperative budget hands the rest of the batch to the next link."""
    import time

    ids = _ids(3)
    env.select_ids(ids)
    real, jump = time.monotonic, [0.0]
    monkeypatch.setattr("time.monotonic", lambda: real() + jump[0])
    monkeypatch.setattr(pipeline, "_time_budget", lambda task: 100.0)

    class _SlowFirst(_FakeIngest):
        async def backfill_record(self, rio):
            jump[0] = 1e6  # the first record eats the whole budget
            return await super().backfill_record(rio)

    monkeypatch.setattr("brave.domains.tripadvisor.atrativos.TripAdvisorAtrativosIngest", _SlowFirst)

    _run(env)

    assert _FakeIngest.seen == ids[:1]
    assert env.links == [{"run_id": env.run_id, "after_id": str(ids[0]), "countdown": None}]


def test_offline_never_runs(env):
    env.cfg.run_real_externals = False
    env.select_ids(_ids(1))

    assert _run(env) == "disabled"
    assert _FakeIngest.seen == []


def test_places_off_keeps_the_places_clause_out_of_the_selector():
    from sqlalchemy.dialects import postgresql

    def sql(places_on: bool) -> str:
        stmt = select(RioRecord.id).where(*pipeline.backfill_pending_filter(places_on))
        return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))

    on, off = sql(True), sql(False)
    assert "google_enriched" in on and "google_enriched" not in off


# -- start_backfill_run + the beat tick ------------------------------------------------


def test_start_backfill_run_is_a_counted_engine_run(monkeypatch):
    rc = fakeredis.FakeRedis()
    sent: list[dict] = []
    monkeypatch.setattr(pipeline.backfill_ta_atrativos, "delay", lambda **kw: sent.append(kw))

    run_id = pipeline.start_backfill_run(rc, None)

    assert sent == [{"run_id": run_id}]
    assert collection_engine.get_status(rc)["state"] == collection_engine.RUNNING
    assert pipeline.start_backfill_run(rc, None) is None  # busy
    # The link pays its token back → the run completes "concluido".
    assert collection_engine.producer_done(rc, None, run_id) is True


def _tick_env(monkeypatch, *, at: datetime, reason="daily_budget", action="backfill"):
    from brave.domains.tripadvisor.client import BRAVE_TA_SESSION_KEY

    rc = fakeredis.FakeRedis()
    monkeypatch.setattr("redis.from_url", lambda *a, **k: rc)
    monkeypatch.setattr(pipeline, "_get_session", lambda: (MagicMock(), None))
    monkeypatch.setattr(pipeline, "_load_config", lambda _s: AppConfig())
    rc.setex(BRAVE_TA_SESSION_KEY, 900, "{}")
    collection_engine.pause_with_reason(rc, reason, action=action)
    raw = collection_engine.get_pause_reason(rc) | {"at": at.isoformat()}
    import json

    rc.set("brave:engine:pause_reason", json.dumps(raw))
    sent: list[dict] = []
    monkeypatch.setattr(pipeline.backfill_ta_atrativos, "delay", lambda **kw: sent.append(kw))
    return rc, sent


def test_tick_resumes_a_daily_cap_pause_once_the_day_turned(monkeypatch):
    rc, sent = _tick_env(monkeypatch, at=datetime.now(UTC) - timedelta(days=1))

    assert pipeline.backfill_ta_atrativos.__wrapped__.__func__(MagicMock()) == "resumed"
    assert len(sent) == 1
    assert collection_engine.get_mode(rc) == collection_engine.LIGADO


@pytest.mark.parametrize(
    ("kw", "expected"),
    [
        ({"at": datetime.now(UTC)}, "waiting_budget"),
        ({"at": datetime(2026, 1, 1, tzinfo=UTC), "reason": "provider_balance"}, "idle"),
        ({"at": datetime(2026, 1, 1, tzinfo=UTC), "action": "sweep"}, "idle"),
    ],
    ids=["same-day", "billing-wall-needs-operator", "other-action"],
)
def test_tick_never_starts_anything_else(monkeypatch, kw, expected):
    _rc, sent = _tick_env(monkeypatch, **kw)

    assert pipeline.backfill_ta_atrativos.__wrapped__.__func__(MagicMock()) == expected
    assert sent == []


# -- the real selector (test DB) -------------------------------------------------------


@pytest.fixture
def tx_session(db_engine):
    conn = db_engine.connect()
    outer = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        outer.rollback()
        conn.close()


@pytest.mark.integration
def test_selector_against_the_db(tx_session):
    from brave.core.nascente.service import store_raw

    def rio(normalized, *, routing="dlq", key_prefix="tripadvisor:attraction:", uf="ZZ"):
        ref = f"{key_prefix}{uuid.uuid4().int % 10**12}"
        nascente = store_raw(tx_session, "test", ref, "attraction", uf, {"name": ref})
        row = RioRecord(
            nascente_id=nascente.id, entity_type="attraction", uf=uf, routing=routing,
            canonical_key=ref, normalized=normalized,
        )
        tx_session.add(row)
        tx_session.flush()
        return row.id

    stamp = "2026-10-17T00:00:00+00:00"
    fresh = rio({"name": "x"})
    ta_done_places_pending = rio({"ta_backfilled_at": stamp})
    all_done = rio({"ta_backfilled_at": stamp, "google_enriched": True})
    discarded = rio({"name": "x"}, routing="descarte")
    other_source = rio({"name": "x"}, key_prefix="mtur:")
    other_uf = rio({"ta_backfilled_at": stamp, "ta_uf": "BA"})  # municipio_nao_confirmado
    mine = [fresh, ta_done_places_pending, all_done, discarded, other_source, other_uf]

    def selected(places_on: bool) -> set:
        stmt = select(RioRecord.id).where(
            *pipeline.backfill_pending_filter(places_on), RioRecord.id.in_(mine)
        )
        return set(tx_session.scalars(stmt).all())

    assert selected(True) == {fresh, ta_done_places_pending}
    assert selected(False) == {fresh}


def test_cli_backfill_ta_starts_or_reports_busy(monkeypatch, capsys):
    import sys

    from brave import cli

    rc = fakeredis.FakeRedis()
    monkeypatch.setattr("redis.from_url", lambda *a, **k: rc)
    monkeypatch.setattr(pipeline, "_get_session", lambda: (MagicMock(), None))
    monkeypatch.setattr(pipeline.backfill_ta_atrativos, "delay", lambda **kw: None)
    monkeypatch.setattr(sys, "argv", ["brave", "backfill-ta"])

    with pytest.raises(SystemExit) as first:
        cli.main()
    assert first.value.code == 0 and "started run_id=" in capsys.readouterr().out

    with pytest.raises(SystemExit) as second:
        cli.main()
    assert second.value.code == 2


# -- review fixes (wave 2) -------------------------------------------------------------


def _failed_producers(rc) -> int:
    return int(rc.get("brave:engine:producers_failed") or 0)


def test_consecutive_failures_stop_as_systemic_without_skipping_them(env, monkeypatch):
    """Proxy down / TA 5xx: every record fails. N in a row stop the link with the cursor
    BEFORE them (the next run retries them) and the run closes partial — it used to burn
    through all of them as 'failed' and close 'concluido'."""
    monkeypatch.setattr(pipeline, "_BACKFILL_MAX_CONSECUTIVE_FAILURES", 2)
    ids = _ids(3)
    env.select_ids(ids)
    _FakeIngest.outcomes = {i: RuntimeError("ConnectError") for i in ids}

    assert _run(env, after_id=str(ids[0])) == "errors"

    assert _FakeIngest.seen == ids[:2]
    assert env.links == []
    assert _progress(env.rc)["reason"] == "errors"
    assert _progress(env.rc)["cursor"] == str(ids[1])  # progress only; no keyset advance
    assert _failed_producers(env.rc) == 1  # → run "parcial"


def test_isolated_failure_is_still_skipped(env, monkeypatch):
    monkeypatch.setattr(pipeline, "_BACKFILL_MAX_CONSECUTIVE_FAILURES", 2)
    ids = _ids(3)
    env.select_ids(ids)
    _FakeIngest.outcomes = {ids[0]: RuntimeError("bad card")}

    assert _run(env) == "chained"
    assert env.links[0]["after_id"] == str(ids[-1])


def test_rolled_back_record_evicts_the_destino_it_cached(env, monkeypatch):
    """A destino cached by a record that then rolls back must leave the cache, or the next
    same-município record links to a parent row that no longer exists."""
    ids = _ids(2)
    env.select_ids(ids)
    parent = (uuid.uuid4(), "ibge:destination:1")

    class _CachingIngest(_FakeIngest):
        def __init__(self, **kw):
            super().__init__(**kw)
            self._destino_rio_map = {}
            _CachingIngest.instance = self

        async def backfill_record(self, rio):
            if rio.id == ids[0]:
                self._destino_rio_map["1"] = parent  # then the commit/flush fails
                raise RuntimeError("flush failed")
            seen_map.append(dict(self._destino_rio_map))
            return "ta"

    seen_map: list[dict] = []
    monkeypatch.setattr(
        "brave.domains.tripadvisor.atrativos.TripAdvisorAtrativosIngest", _CachingIngest
    )

    _run(env)

    assert seen_map == [{}]


@pytest.mark.parametrize(
    "outcome",
    [SourceSchemaError("qid"), SourceRateLimitError(retry_after=5), "handover_failed"],
    ids=["schema", "rate_limit-exhausted", "handover_failed"],
)
def test_stops_with_records_pending_close_the_run_partial(env, monkeypatch, outcome):
    ids = _ids(3)
    env.select_ids(ids)
    kw: dict[str, Any] = {}
    if outcome == "handover_failed":
        def boom(**_k):
            raise RuntimeError("broker down")

        monkeypatch.setattr(pipeline.backfill_ta_atrativos, "apply_async", boom)
    elif isinstance(outcome, SourceSchemaError):  # systemic = the whole run in a row
        _FakeIngest.outcomes = dict.fromkeys(ids, outcome)
    else:
        _FakeIngest.outcomes = {ids[0]: outcome}
        kw["backoffs"] = pipeline._MAX_RATE_LIMIT_BACKOFFS

    _run(env, **kw)

    assert _run_status(env.rc) == collection_engine.IDLE
    assert _failed_producers(env.rc) == 1  # "parcial", never "concluido"


def test_tick_ignores_a_budget_pause_the_operator_turned_off(monkeypatch):
    """Budget pause, then Desligar: next UTC day the beat must NOT restart the run."""
    rc, sent = _tick_env(monkeypatch, at=datetime.now(UTC) - timedelta(days=1))
    collection_engine.set_mode(rc, collection_engine.DESLIGADO)

    assert collection_engine.get_pause_reason(rc) is None  # the OFF supersedes the pause
    assert pipeline.backfill_ta_atrativos.__wrapped__.__func__(MagicMock()) == "idle"
    assert sent == []
    assert collection_engine.get_mode(rc) == collection_engine.DESLIGADO


def test_tick_requires_the_motor_still_paused(monkeypatch):
    """Even with a reason left behind (written by hand / older code), only PAUSADO resumes."""
    import json

    rc, sent = _tick_env(monkeypatch, at=datetime.now(UTC) - timedelta(days=1))
    raw = collection_engine.get_pause_reason(rc)
    rc.set(collection_engine._MODE_KEY, collection_engine.DESLIGADO)
    rc.set("brave:engine:pause_reason", json.dumps(raw))

    assert pipeline.backfill_ta_atrativos.__wrapped__.__func__(MagicMock()) == "idle"
    assert sent == []


def test_tick_without_ta_session_waits(monkeypatch):
    """No TA session → 'busy': resuming would hit SessionMissingError and turn the motor
    OFF (needs_bootstrap) — a pause that resolves itself would become operator work."""
    from brave.domains.tripadvisor.client import BRAVE_TA_SESSION_KEY

    rc, sent = _tick_env(monkeypatch, at=datetime.now(UTC) - timedelta(days=1))
    rc.delete(BRAVE_TA_SESSION_KEY)

    assert pipeline.backfill_ta_atrativos.__wrapped__.__func__(MagicMock()) == "busy"
    assert sent == []
    assert collection_engine.get_mode(rc) == collection_engine.PAUSADO
