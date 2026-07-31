"""Unit tests for the batched atrativo-description lane (Message Batches API).

100% offline: the Anthropic batches client is a local fake (create/retrieve/results), the
session is a hand-rolled fake that keeps real objects so state is actually PERSISTED (the
whole point of the batch stamps), Redis is fakeredis. No network, no DB, no Anthropic.

Covers the things that cost money or silently strand records:
  - build_request: custom_id contract + no batch-forbidden params + byte-identical params
    to what the INLINE copywriter sends (otherwise the two spends are not comparable);
  - claim-before-spend ordering: the acks_late redelivery of a killed worker must not be
    able to bill the same records twice;
  - budget: reserved at submit, reconciled (delta only) at collect, batch DOWN-SIZED to the
    remaining budget instead of raising;
  - idempotency: re-collecting an already-applied batch writes nothing at all;
  - per-batch isolation (a poison id does not stop the others) and the reaper;
  - result mapping: errored burns an attempt, expired/canceled burn a BOUNDED expiry counter
    instead, pause_turn is a billed failed attempt;
  - collect dispatches NO push task — the steward gate stays the only way into norteia-api.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import fakeredis
import httpx
import pytest
from anthropic import (
    APIConnectionError,
    APITimeoutError,
    BadRequestError,
    InternalServerError,
    NotFoundError,
)
from sqlalchemy.dialects import postgresql

from brave.config.settings import LLMConfig, ScoreConfig
from brave.core.models import LLMGeneration, RioRecord
from brave.lanes.atrativos.copy_batch import (
    _CUSTOM_ID_RE,
    _EST_USD_PER_DESCRIPTION,
    _EXPIRIES_KEY,
    _MAX_BATCH_EXPIRIES,
    AMBIGUOUS_BATCH_ID,
    CLAIM_BATCH_ID,
    DESCRIPTION_BATCH_SIZE,
    apply_result,
    build_request,
    candidates_select,
    collect_batches,
    inflight_batch_ids,
    reap_stale_claims,
    submit_batch,
)
from brave.lanes.atrativos.copywriter import WEB_SEARCH_TOOL, TourismCopywriter
from brave.observability.cost_guard import _daily_key
from tests.fakes.fake_llm import FakeLLMClient

_MODEL = "claude-sonnet-4-5"

_REQUEST = httpx.Request("POST", "https://api.anthropic.com/v1/messages/batches")


def _api_error(cls, status_code: int):  # noqa: ANN001, ANN202
    """Build a real SDK status error — the classification switches on the SDK hierarchy."""
    return cls("boom", response=httpx.Response(status_code, request=_REQUEST), body=None)


def _make_rio(**extra) -> RioRecord:
    """A real (transient) RioRecord — flag_modified and the stamp columns must behave."""
    return RioRecord(
        id=uuid.uuid4(),
        uf="BA",
        routing="dlq",
        entity_type="attraction",
        normalized={
            "name": "Igreja Matriz",
            "municipio": "Porto Seguro",
            "address": "Praça Pero Campos, Porto Seguro - BA",
            "completude_value": 75.0,
            **extra,
        },
    )


class _FakeSession:
    """Session stand-in that PERSISTS: records live as real objects, mutations stick.

    ``execute`` returns queued row-lists in call order (the lane runs at most two selects
    per entry point, so ordering is unambiguous) and keeps the statements for SQL asserts.
    Every commit is appended to ``events`` so tests can assert ORDERING against the fake
    Anthropic client, which appends its own marks to the same list.
    """

    def __init__(
        self,
        *selects: list,
        events: list[str] | None = None,
        on_execute: Any = None,
        commit_error: BaseException | None = None,
    ) -> None:
        self._selects = list(selects)
        self._on_execute = on_execute
        self._commit_error = commit_error
        self.records: dict[uuid.UUID, RioRecord] = {}
        self.executed: list = []
        self.added: list = []
        self.events = events if events is not None else []
        self.rollbacks = 0
        self.locked_gets: list[bool] = []
        self.populated_gets: list[bool] = []

    def register(self, *rios: RioRecord) -> None:
        for rio in rios:
            self.records[rio.id] = rio

    def execute(self, stmt):  # noqa: ANN001
        self.executed.append(stmt)
        if self._on_execute is not None:
            self._on_execute()
        rows = self._selects.pop(0) if self._selects else []
        # Honour LIMIT like a real DB would — the batch is sized by it, and a fake that
        # ignores it hides an over-reservation.
        limit = getattr(stmt, "_limit", None)
        if limit is not None:
            rows = rows[:limit]
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: rows))

    def get(  # noqa: ANN001
        self, _model, rid, with_for_update: bool = False, populate_existing: bool = False
    ):
        self.locked_gets.append(with_for_update)
        self.populated_gets.append(populate_existing)
        return self.records.get(rid)

    def add(self, obj) -> None:  # noqa: ANN001
        self.added.append(obj)

    def flush(self) -> None:
        pass

    def commit(self) -> None:
        if self._commit_error is not None:
            raise self._commit_error
        self.events.append("commit")

    def rollback(self) -> None:
        self.rollbacks += 1

    @property
    def generations(self) -> list[LLMGeneration]:
        return [o for o in self.added if isinstance(o, LLMGeneration)]


def _message(
    text: str = "A igreja domina a praça.",
    stop_reason: str = "end_turn",
    input_tokens: int = 18_984,
    output_tokens: int = 977,
    searches: int = 2,
    cache_read_tokens: int | None = None,
    cache_write_tokens: int | None = None,
) -> SimpleNamespace:
    usage = SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        server_tool_use=SimpleNamespace(web_search_requests=searches),
    )
    # Set ONLY when caching is on: Anthropic omits both counters entirely otherwise, and the
    # default message is what keeps the defensive read covered.
    if cache_read_tokens is not None:
        usage.cache_read_input_tokens = cache_read_tokens
    if cache_write_tokens is not None:
        usage.cache_creation_input_tokens = cache_write_tokens
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason=stop_reason,
        usage=usage,
    )


def _result(rtype: str, message: SimpleNamespace | None = None) -> SimpleNamespace:
    return SimpleNamespace(type=rtype, message=message)


def _entry(rio: RioRecord, result: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(custom_id=str(rio.id), result=result)


class _FakeBatches:
    """Stand-in for client.messages.batches (create / retrieve / results).

    ``results_by_id`` lets one client serve several batch ids; ``poison`` ids raise on
    retrieve, standing in for a 404 past the 29-day retention window.
    """

    def __init__(
        self,
        status: str = "ended",
        results: list | None = None,
        results_by_id: dict[str, list] | None = None,
        poison: set[str] | None = None,
        missing: set[str] | None = None,
        create_error: BaseException | None = None,
        events: list[str] | None = None,
        batch_id: str = "msgbatch_01",
    ) -> None:
        self.status = status
        self._results = results or []
        self._by_id = results_by_id or {}
        self._poison = poison or set()
        self._missing = missing or set()  # 404 — the batch is genuinely gone
        self._create_error = create_error
        self.events = events if events is not None else []
        self.batch_id = batch_id
        self.created: list[list[dict]] = []
        self.retrieved: list[str] = []

    def create(self, requests):  # noqa: ANN001
        self.events.append("create")
        if self._create_error is not None:
            raise self._create_error
        self.created.append(list(requests))
        return SimpleNamespace(id=self.batch_id)

    def retrieve(self, batch_id):  # noqa: ANN001
        self.retrieved.append(batch_id)
        if batch_id in self._missing:
            raise _api_error(NotFoundError, 404)
        if batch_id in self._poison:
            raise RuntimeError(f"unreadable batch {batch_id}")
        return SimpleNamespace(id=batch_id, processing_status=self.status)

    def results(self, batch_id):  # noqa: ANN001
        return iter(self._by_id.get(batch_id, self._results))


def _fake_client(batches: _FakeBatches) -> SimpleNamespace:
    return SimpleNamespace(messages=SimpleNamespace(batches=batches))


def _redis(spent: float = 0.0) -> fakeredis.FakeStrictRedis:
    r = fakeredis.FakeStrictRedis()
    if spent:
        r.set(_daily_key(), str(spent))
    return r


def _spent(r) -> float:  # noqa: ANN001
    raw = r.get(_daily_key())
    return float(raw) if raw is not None else 0.0


# ---------------------------------------------------------------------------
# build_request
# ---------------------------------------------------------------------------


def test_custom_id_matches_anthropic_contract() -> None:
    req = build_request(_make_rio(), model=_MODEL)
    assert _CUSTOM_ID_RE.match(req["custom_id"])


def test_params_carry_no_batch_forbidden_key() -> None:
    """The Batches API rejects streaming/routing params that the Messages API accepts."""
    params = build_request(_make_rio(), model=_MODEL)["params"]
    forbidden = {"stream", "speed", "store", "cache_hint", "context_hint"}
    assert not forbidden & set(params)
    assert params["tools"] == [WEB_SEARCH_TOOL]
    assert params["max_tokens"] == 2048


@pytest.mark.asyncio
async def test_params_are_identical_to_the_inline_copywriter_call() -> None:
    """Batch and inline must send the same prompt, or the two costs are not comparable."""
    rio = _make_rio()
    params = build_request(rio, model=_MODEL)["params"]

    fake = FakeLLMClient(generate_result="x")
    await TourismCopywriter(fake, model=_MODEL).write(
        "Igreja Matriz",
        "Porto Seguro",
        "BA",
        {"formatted_address": rio.normalized["address"]},
    )
    inline = fake.generate_calls[-1]
    assert params["model"] == inline["model"]
    assert params["system"] == inline["system"]
    assert params["tools"] == inline["tools"]
    assert params["messages"] == inline["messages"]


# ---------------------------------------------------------------------------
# Selection: the guard is a COLUMN, and it is locked
# ---------------------------------------------------------------------------


def _sql(stmt) -> str:  # noqa: ANN001
    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


def test_eligibility_query_guards_on_the_column_and_locks_the_rows() -> None:
    """A JSONB stamp is erasable by PlacesEnrichmentAgent's whole-column write; a column
    is not. SKIP LOCKED is what stops two concurrent submits billing the same rows."""
    sql = _sql(candidates_select(10))
    assert "rio_records.descricao_batch_id IS NULL" in sql
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "descricao_editorial" in sql
    assert "descricao_attempts" in sql
    assert _EXPIRIES_KEY in sql


def test_inflight_ids_come_from_the_db_and_skip_the_claim_placeholder() -> None:
    """No Redis worklist to flush: the DB is the single source of truth. The claim
    placeholder is not an Anthropic id — retrieving it would 404 and poison the loop."""
    session = _FakeSession(["msgbatch_01", CLAIM_BATCH_ID, "msgbatch_02", None])
    assert inflight_batch_ids(session) == ["msgbatch_01", "msgbatch_02"]
    assert "DISTINCT" in _sql(session.executed[0])


# ---------------------------------------------------------------------------
# submit — claim BEFORE spend, reserve, down-size
# ---------------------------------------------------------------------------


def test_claim_is_committed_before_the_money_is_spent() -> None:
    """acks_late + reject_on_worker_lost means a worker kill GUARANTEES redelivery. If
    batches.create ran before the claim commit, that redelivery re-bills the same records."""
    events: list[str] = []
    rio = _make_rio()
    session = _FakeSession([rio], events=events)
    batches = _FakeBatches(events=events)

    batch_id = submit_batch(
        session,
        _fake_client(batches),
        redis_client=_redis(),
        llm_config=LLMConfig(),
        model=_MODEL,
    )

    assert batch_id == "msgbatch_01"
    # commit (claim) → create (spend) → commit (real id). Never create first.
    assert events == ["commit", "create", "commit"]
    assert rio.descricao_batch_id == "msgbatch_01"
    assert rio.descricao_batch_submitted_at is not None


def test_a_redelivered_submit_selects_nothing_and_bills_nothing() -> None:
    """The stamped rows are excluded by candidates_select, so the redelivery is a no-op."""
    redis_client = _redis()
    rio = _make_rio()
    first = _FakeSession([rio])
    batches = _FakeBatches()
    submit_batch(first, _fake_client(batches), redis_client=redis_client, llm_config=LLMConfig())
    reserved_after_first = _spent(redis_client)

    # Redelivery: the row now carries a stamp, so the eligibility query returns nothing.
    second = _FakeSession([])
    assert (
        submit_batch(
            second, _fake_client(batches), redis_client=redis_client, llm_config=LLMConfig()
        )
        is None
    )
    assert len(batches.created) == 1  # only ONE billed create
    assert _spent(redis_client) == pytest.approx(reserved_after_first)


def test_submit_reserves_the_projected_spend_immediately() -> None:
    """Without a reservation the hourly beat commits ~24 batches against a counter that
    stays 0.0 until collection lands up to 24h later."""
    redis_client = _redis()
    rows = [_make_rio() for _ in range(3)]
    submit_batch(
        _FakeSession(rows),
        _fake_client(_FakeBatches()),
        redis_client=redis_client,
        llm_config=LLMConfig(),
    )
    assert _spent(redis_client) == pytest.approx(3 * _EST_USD_PER_DESCRIPTION)


def test_batch_is_downsized_to_the_remaining_budget_instead_of_blocking() -> None:
    """A binary over-budget check raises on the FIRST tick (130 x $0.075 > $10 default) and
    silently stops descriptions entirely. Shrink instead."""
    # $9.80 spent of the $10.00 default → $0.20 left → floor(0.20 / 0.075) = 2 descriptions.
    session = _FakeSession([_make_rio() for _ in range(3)])
    batches = _FakeBatches()

    batch_id = submit_batch(
        session,
        _fake_client(batches),
        redis_client=_redis(9.80),
        llm_config=LLMConfig(),
    )

    assert batch_id == "msgbatch_01"
    assert "LIMIT 2" in _sql(session.executed[0])


def test_submit_skips_the_tick_when_the_budget_cannot_pay_for_one_description() -> None:
    batches = _FakeBatches()
    assert (
        submit_batch(
            _FakeSession([_make_rio()]),
            _fake_client(batches),
            redis_client=_redis(9.99),
            llm_config=LLMConfig(),
        )
        is None
    )
    assert batches.created == []  # skipped, not raised — beat retries next hour


def test_default_batch_size_fits_a_default_budget_day() -> None:
    """Otherwise turning the flag on with shipped config stops descriptions on tick one."""
    worst_case_usd = DESCRIPTION_BATCH_SIZE * _EST_USD_PER_DESCRIPTION
    assert worst_case_usd < LLMConfig().usd_daily_budget


def test_the_reservation_covers_the_measured_cost_of_a_description() -> None:
    """The reservation is what stops a batch committing past the daily budget, so it must sit
    ABOVE the measured cost, not below it.

    Measured: 5 live inline calls came in at $0.094-$0.116 all-in on the STANDARD API, each
    spending exactly 2 web searches. The $0.01/search fee is not discounted in batch, so the
    batch-rate worst case is ($0.116 - $0.02) x 0.5 + $0.02 = $0.068. Over-reserving costs
    nothing — submit hands the remainder back in the same tick.
    """
    searches_usd = 2 * 0.01
    worst_case_batch_usd = (0.116 - searches_usd) * 0.5 + searches_usd
    assert worst_case_batch_usd <= _EST_USD_PER_DESCRIPTION


def test_the_budget_slot_is_claimed_before_the_rows_are_selected() -> None:
    """FOR UPDATE SKIP LOCKED keeps two submits on disjoint ROWS, not on a disjoint BUDGET.
    Read-then-reserve lets an acks_late redelivery running beside the original read
    remaining=$10 with it and both reserve $9.90. INCRBYFLOAT is the atomic claim, so it must
    happen FIRST and the batch must be sized off what it returns."""
    redis_client = _redis()
    at_select: list[float] = []
    session = _FakeSession([_make_rio()], on_execute=lambda: at_select.append(_spent(redis_client)))

    submit_batch(
        session,
        _fake_client(_FakeBatches()),
        redis_client=redis_client,
        llm_config=LLMConfig(),
    )

    # A whole batch's worth was already booked when the candidates were picked...
    assert at_select[0] == pytest.approx(DESCRIPTION_BATCH_SIZE * _EST_USD_PER_DESCRIPTION)
    # ...and everything the tick did not use was handed straight back.
    assert _spent(redis_client) == pytest.approx(_EST_USD_PER_DESCRIPTION)


def test_two_interleaved_submits_cannot_both_reserve_the_whole_budget() -> None:
    """The acks_late redelivery scenario: a second submit runs between the first one's budget
    read and its reservation. With read-then-reserve both book $9.90 against a $10 day."""
    redis_client = _redis()
    fired: list[bool] = []

    def _redelivery() -> None:
        if fired:
            return
        fired.append(True)
        submit_batch(
            _FakeSession([_make_rio() for _ in range(DESCRIPTION_BATCH_SIZE)]),
            _fake_client(_FakeBatches(batch_id="msgbatch_02")),
            redis_client=redis_client,
            llm_config=LLMConfig(),
        )

    submit_batch(
        _FakeSession([_make_rio() for _ in range(DESCRIPTION_BATCH_SIZE)], on_execute=_redelivery),
        _fake_client(_FakeBatches()),
        redis_client=redis_client,
        llm_config=LLMConfig(),
    )

    assert fired  # the two really did interleave
    assert _spent(redis_client) <= LLMConfig().usd_daily_budget


def test_submit_releases_the_whole_reservation_when_nothing_is_eligible() -> None:
    """An empty tick that keeps its reservation kills the lane for the rest of the day."""
    redis_client = _redis()
    assert (
        submit_batch(
            _FakeSession([]),
            _fake_client(_FakeBatches()),
            redis_client=redis_client,
            llm_config=LLMConfig(),
        )
        is None
    )
    assert _spent(redis_client) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# submit — a failed create must not leak the reservation OR the claims
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "exc",
    [
        APIConnectionError(request=_REQUEST),  # never left the box
        _api_error(BadRequestError, 400),  # rejected before the model saw it
    ],
    ids=["connection_error", "400"],
)
def test_a_clean_create_failure_refunds_and_frees_the_rows(exc: BaseException) -> None:
    """Nothing was submitted, so keeping $9.90 of a $10 day reserved sizes the next tick to
    1 and the one after to 0 — the lane is dead until the UTC day key expires — while the
    claimed rows stay frozen until the 25h reaper."""
    redis_client = _redis()
    rows = [_make_rio() for _ in range(3)]
    session = _FakeSession(rows)

    with pytest.raises(type(exc)):
        submit_batch(
            session,
            _fake_client(_FakeBatches(create_error=exc)),
            redis_client=redis_client,
            llm_config=LLMConfig(),
        )

    assert _spent(redis_client) == pytest.approx(0.0)
    assert [r.descricao_batch_id for r in rows] == [None, None, None]
    assert [r.descricao_batch_submitted_at for r in rows] == [None, None, None]


@pytest.mark.parametrize(
    "exc",
    [
        APITimeoutError(request=_REQUEST),  # sent, answer never arrived
        _api_error(InternalServerError, 529),  # overloaded AFTER receiving it
    ],
    ids=["timeout", "529"],
)
def test_an_ambiguous_create_failure_keeps_the_reservation_and_the_claims(
    exc: BaseException,
) -> None:
    """The batch may genuinely exist and be billing: refunding would hand out budget
    Anthropic charged, and freeing the rows would resubmit every request. So the reservation
    stands and the rows are re-stamped to AMBIGUOUS_BATCH_ID — a marker the 25h reaper
    refuses to free, unlike the plain placeholder (APITimeoutError subclasses
    APIConnectionError — the order of the isinstance checks is the whole fix)."""
    redis_client = _redis()
    rows = [_make_rio() for _ in range(3)]
    session = _FakeSession(rows)

    with pytest.raises(type(exc)):
        submit_batch(
            session,
            _fake_client(_FakeBatches(create_error=exc)),
            redis_client=redis_client,
            llm_config=LLMConfig(),
        )

    assert _spent(redis_client) == pytest.approx(3 * _EST_USD_PER_DESCRIPTION)
    assert [r.descricao_batch_id for r in rows] == [AMBIGUOUS_BATCH_ID] * 3


def test_submit_is_a_noop_when_nothing_is_eligible() -> None:
    batches = _FakeBatches()
    assert (
        submit_batch(
            _FakeSession([]),
            _fake_client(batches),
            redis_client=_redis(),
            llm_config=LLMConfig(),
        )
        is None
    )
    assert batches.created == []


# ---------------------------------------------------------------------------
# apply_result — attempt accounting follows the BILL, not the outcome
# ---------------------------------------------------------------------------


def _stamped(batch_id: str = "msgbatch_01", **extra) -> RioRecord:
    rio = _make_rio(**extra)
    rio.descricao_batch_id = batch_id
    rio.descricao_batch_submitted_at = datetime.now(UTC)
    return rio


def test_errored_increments_attempts_and_clears_the_stamp() -> None:
    rio = _stamped(descricao_attempts=1)
    with patch("brave.lanes.atrativos.copy_batch.route_by_score") as rbs:
        assert apply_result(
            _FakeSession(), rio, _result("errored"), ScoreConfig(), batch_id="msgbatch_01"
        )
    assert rio.normalized["descricao_attempts"] == 2
    assert rio.descricao_batch_id is None
    assert rio.descricao_batch_submitted_at is None
    rbs.assert_not_called()


@pytest.mark.parametrize("rtype", ["expired", "canceled"])
def test_expired_and_canceled_burn_a_bounded_expiry_counter_not_an_attempt(rtype: str) -> None:
    """Anthropic does not bill these, so burning an attempt would starve the record — but
    leaving nothing behind makes it instantly re-eligible and it can expire forever."""
    rio = _stamped(descricao_attempts=1)
    with patch("brave.lanes.atrativos.copy_batch.route_by_score"):
        apply_result(_FakeSession(), rio, _result(rtype), ScoreConfig(), batch_id="msgbatch_01")
    assert rio.normalized["descricao_attempts"] == 1
    assert rio.normalized[_EXPIRIES_KEY] == 1
    assert rio.descricao_batch_id is None


def test_the_expiry_bound_makes_a_record_ineligible() -> None:
    sql = _sql(candidates_select(10))
    assert f"< {_MAX_BATCH_EXPIRIES}" in sql


def test_succeeded_writes_description_and_lifts_completude() -> None:
    rio = _stamped()
    session = _FakeSession()
    with patch("brave.lanes.atrativos.copy_batch.route_by_score") as rbs:
        apply_result(
            session,
            rio,
            _result("succeeded", _message("A igreja — velha — encanta.")),
            ScoreConfig(),
            batch_id="msgbatch_01",
        )
    assert rio.normalized["descricao_editorial"] == "A igreja, velha, encanta."
    assert rio.normalized["completude_value"] == 90.0
    assert "descricao_attempts" not in rio.normalized
    assert rio.descricao_batch_id is None
    rbs.assert_called_once()


def test_pause_turn_is_a_billed_failed_attempt() -> None:
    rio = _stamped()
    session = _FakeSession()
    with patch("brave.lanes.atrativos.copy_batch.route_by_score"):
        apply_result(
            session,
            rio,
            _result("succeeded", _message(stop_reason="pause_turn")),
            ScoreConfig(),
            batch_id="msgbatch_01",
        )
    assert rio.normalized["descricao_attempts"] == 1
    assert "descricao_editorial" not in rio.normalized
    assert len(session.generations) == 1  # tokens were spent → the row is still written


def test_cost_is_batch_rates_plus_undiscounted_search_fee() -> None:
    """50% off tokens; the $10/1k web_search fee is NOT discounted in batch."""
    session = _FakeSession()
    with patch("brave.lanes.atrativos.copy_batch.route_by_score"):
        apply_result(
            session,
            _stamped(),
            _result("succeeded", _message()),
            ScoreConfig(),
            batch_id="msgbatch_01",
        )
    row = session.generations[0]
    expected = (18_984 * 3.0 * 0.5 + 977 * 15.0 * 0.5) / 1_000_000 + 2 * 0.01
    assert row.usd_cost == pytest.approx(expected)
    assert row.prompt_tokens == 18_984


def test_cache_tokens_are_priced_at_batch_rates_like_the_inline_path() -> None:
    """RealLLMClient prices cache_read (x0.1) and cache_creation (x1.25) so that turning
    prompt caching on is MEASURED rather than silently under-counted — a cache hit moves
    tokens out of input_tokens. The batched path imports the same rate constants precisely so
    the two rows cannot drift; dropping these two counters would make it under-report its bill
    to record_spend the moment cache_control lands on COPYWRITER_SYSTEM (identical across
    every request in a batch, so the obvious next step), on the one lane whose spend is
    already irrevocably committed."""
    session = _FakeSession()
    with patch("brave.lanes.atrativos.copy_batch.route_by_score"):
        apply_result(
            session,
            _stamped(),
            _result(
                "succeeded",
                _message(input_tokens=1_000, cache_read_tokens=100_000, cache_write_tokens=4_000),
            ),
            ScoreConfig(),
            batch_id="msgbatch_01",
        )
    expected = (
        1_000 * 3.0 + 4_000 * 3.0 * 1.25 + 100_000 * 3.0 * 0.1 + 977 * 15.0
    ) * 0.5 / 1_000_000 + 2 * 0.01
    assert session.generations[0].usd_cost == pytest.approx(expected)


def test_a_result_for_another_batch_is_never_applied() -> None:
    """The stamp is the applied-marker; a stale/reaped record must not be written."""
    rio = _stamped("msgbatch_99")
    session = _FakeSession()
    assert (
        apply_result(session, rio, _result("errored"), ScoreConfig(), batch_id="msgbatch_01")
        is False
    )
    assert "descricao_attempts" not in rio.normalized
    assert rio.descricao_batch_id == "msgbatch_99"


# ---------------------------------------------------------------------------
# reaper
# ---------------------------------------------------------------------------


def test_reaper_frees_a_record_stranded_past_the_expiry_window() -> None:
    """Recovery path for a crash between claim and create: the placeholder names no batch to
    probe, so the time cutoff alone must free it."""
    stranded = _make_rio()
    stranded.descricao_batch_id = CLAIM_BATCH_ID
    stranded.descricao_batch_submitted_at = datetime.now(UTC) - timedelta(hours=26)
    session = _FakeSession([stranded])
    batches = _FakeBatches()

    assert reap_stale_claims(session, _fake_client(batches)) == 1
    assert stranded.descricao_batch_id is None
    assert stranded.descricao_batch_submitted_at is None
    assert session.events == ["commit"]
    assert batches.retrieved == []  # nothing to probe — never invent an Anthropic call


def test_reaper_uses_a_25h_cutoff_and_commits_nothing_when_clean() -> None:
    session = _FakeSession([])
    now = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)
    assert reap_stale_claims(session, _fake_client(_FakeBatches()), now=now) == 0
    assert session.events == []
    assert "2026-07-29 11:00:00" in _sql(session.executed[0])


def test_reaper_never_frees_an_ambiguous_claim() -> None:
    """An ambiguous batches.create failure (a 529 Anthropic may have accepted) re-stamps its
    rows to AMBIGUOUS_BATCH_ID. Time alone must NOT free those: unlike the plain placeholder —
    where a crash before create proves no batch exists — here Anthropic may hold a live,
    billing batch whose paid results are already unreachable. Freeing them re-pays for it."""
    stuck = _make_rio()
    stuck.descricao_batch_id = AMBIGUOUS_BATCH_ID
    stuck.descricao_batch_submitted_at = datetime.now(UTC) - timedelta(hours=26)
    session = _FakeSession([stuck])
    batches = _FakeBatches()

    assert reap_stale_claims(session, _fake_client(batches)) == 0
    assert stuck.descricao_batch_id == AMBIGUOUS_BATCH_ID
    assert session.events == []  # nothing freed → nothing committed
    assert batches.retrieved == []  # it is not a real id — never probe it
    # ...and it stays out of the collect worklist, so no one tries to retrieve it either.
    assert inflight_batch_ids(_FakeSession([AMBIGUOUS_BATCH_ID])) == []


def test_reaper_never_orphans_a_batch_that_still_retrieves() -> None:
    """Freeing the last row carrying a batch id makes that batch invisible forever
    (inflight_batch_ids derives the worklist from the stamps) — its PAID results are
    discarded, its reservations never settled, and its rows resubmitted and billed again.
    Results that merely failed to retrieve for the 45 min before the cutoff are exactly that
    case, so time alone must not free a real id."""
    alive = _make_rio()
    alive.descricao_batch_id = "msgbatch_alive"
    alive.descricao_batch_submitted_at = datetime.now(UTC) - timedelta(hours=26)
    session = _FakeSession([alive])

    assert reap_stale_claims(session, _fake_client(_FakeBatches())) == 0
    assert alive.descricao_batch_id == "msgbatch_alive"
    assert session.events == []  # nothing freed → nothing committed


def test_reaper_frees_a_batch_that_anthropic_no_longer_has() -> None:
    """A 404 (rotated key, deleted batch, past the 29-day retention) is the one proof that
    the results are genuinely gone — and an unreadable batch (any other error) is NOT."""
    gone = _make_rio()
    gone.descricao_batch_id = "msgbatch_gone"
    gone.descricao_batch_submitted_at = datetime.now(UTC) - timedelta(hours=26)
    unreadable = _make_rio()
    unreadable.descricao_batch_id = "msgbatch_unreadable"
    unreadable.descricao_batch_submitted_at = datetime.now(UTC) - timedelta(hours=26)
    session = _FakeSession([gone, unreadable])
    batches = _FakeBatches(missing={"msgbatch_gone"}, poison={"msgbatch_unreadable"})

    assert reap_stale_claims(session, _fake_client(batches)) == 1
    assert gone.descricao_batch_id is None
    assert unreadable.descricao_batch_id == "msgbatch_unreadable"


# ---------------------------------------------------------------------------
# collect — idempotency, isolation, budget reconciliation, NO push
# ---------------------------------------------------------------------------


def _collect(session: _FakeSession, batches: _FakeBatches, redis_client) -> int:  # noqa: ANN001
    with patch("brave.lanes.atrativos.copy_batch.route_by_score"):
        return collect_batches(
            session,
            _fake_client(batches),
            ScoreConfig(),
            redis_client=redis_client,
            model=_MODEL,
        )


def test_collect_skips_a_batch_that_has_not_ended() -> None:
    rio = _stamped()
    session = _FakeSession([], ["msgbatch_01"])
    session.register(rio)

    assert _collect(session, _FakeBatches(status="in_progress"), _redis()) == 0
    assert rio.descricao_batch_id == "msgbatch_01"


def test_collect_settles_the_reservation_instead_of_double_charging() -> None:
    """Submit reserved the projection; collect must record actual - projected, or the day
    counter is double-charged and the guard trips for everyone."""
    redis_client = _redis()
    rio = _make_rio()
    submit_batch(
        _FakeSession([rio]),
        _fake_client(_FakeBatches()),
        redis_client=redis_client,
        llm_config=LLMConfig(),
    )
    assert _spent(redis_client) == pytest.approx(_EST_USD_PER_DESCRIPTION)

    session = _FakeSession([], ["msgbatch_01"])
    session.register(rio)
    batches = _FakeBatches(results=[_entry(rio, _result("succeeded", _message("Prosa boa.")))])

    assert _collect(session, batches, redis_client) == 1
    assert rio.normalized["descricao_editorial"] == "Prosa boa."
    assert rio.descricao_batch_id is None
    actual = (18_984 * 3.0 * 0.5 + 977 * 15.0 * 0.5) / 1_000_000 + 2 * 0.01
    assert _spent(redis_client) == pytest.approx(actual)  # NOT projected + actual


def test_recollecting_an_already_applied_batch_writes_nothing() -> None:
    """A crash mid-stream re-reads the whole result set on the next tick. Without the stamp
    guard that is a second LLMGeneration row, phantom spend, and a double-burned attempt."""
    redis_client = _redis()
    rio = _stamped()
    entry = _entry(rio, _result("succeeded", _message(text="")))  # empty prose → burns 1 attempt

    first = _FakeSession([], ["msgbatch_01"])
    first.register(rio)
    assert _collect(first, _FakeBatches(results=[entry]), redis_client) == 1
    assert rio.normalized["descricao_attempts"] == 1
    spend_after_first = _spent(redis_client)
    assert len(first.generations) == 1

    # Same stream again — the record no longer carries the batch id.
    second = _FakeSession([], ["msgbatch_01"])
    second.register(rio)
    assert _collect(second, _FakeBatches(results=[entry]), redis_client) == 0
    assert rio.normalized["descricao_attempts"] == 1
    assert second.generations == []
    assert _spent(redis_client) == pytest.approx(spend_after_first)


def test_collect_locks_the_row_before_it_reads_the_stamp() -> None:
    """The stamp IS the applied-marker, and it is read outside any lock: two overlapping
    collect runs both see batch_id and both apply — duplicate LLMGeneration rows, a double
    refund, and two of the three descricao_attempts burned."""
    rio = _stamped()
    session = _FakeSession([], ["msgbatch_01"])
    session.register(rio)
    batches = _FakeBatches(results=[_entry(rio, _result("succeeded", _message("Prosa boa.")))])

    assert _collect(session, batches, _redis()) == 1
    assert session.locked_gets == [True]


def test_collect_re_reads_the_locked_row_instead_of_trusting_the_identity_map() -> None:
    """FOR UPDATE alone does not re-read: session.get() returns an instance already in the
    identity map WITHOUT refreshing it, so the loser of the race takes the lock and then reads
    the stamp it loaded BEFORE the winner cleared it — and applies a second time. This is not
    hypothetical: collect calls reap_stale_claims first, which SELECTs RioRecord objects and
    commits (expiring them) only when it actually frees something."""
    rio = _stamped()
    session = _FakeSession([], ["msgbatch_01"])
    session.register(rio)
    batches = _FakeBatches(results=[_entry(rio, _result("succeeded", _message("Prosa boa.")))])

    assert _collect(session, batches, _redis()) == 1
    assert session.populated_gets == [True]


def test_the_reservation_is_settled_only_after_the_writes_are_durable() -> None:
    """The Redis settle cannot be rolled back with the transaction it settles. Firing it
    BEFORE the commit means a failed commit (deadlock, dropped connection, statement timeout)
    restores the stamp, the next 15-minute tick re-retrieves the same ended batch, the guard
    passes again — and the reservation is released a SECOND time for a record that only ever
    reserved once. 130 of those per batch is free budget handed to every lane on the counter.
    """
    redis_client = _redis()
    rio = _make_rio()
    submit_batch(
        _FakeSession([rio]),
        _fake_client(_FakeBatches()),
        redis_client=redis_client,
        llm_config=LLMConfig(),
    )
    reserved = _spent(redis_client)
    assert reserved == pytest.approx(_EST_USD_PER_DESCRIPTION)

    session = _FakeSession([], ["msgbatch_01"], commit_error=RuntimeError("deadlock detected"))
    session.register(rio)
    # expired = unbilled, so a premature settle releases the WHOLE reservation.
    batches = _FakeBatches(results=[_entry(rio, _result("expired"))])

    assert _collect(session, batches, redis_client) == 0
    assert session.rollbacks == 1
    assert _spent(redis_client) == pytest.approx(reserved)


def test_reconcile_is_told_which_day_the_reservation_was_booked_on() -> None:
    """Counters are per-UTC-day keys and a batch takes up to 24h, so a refund settled on the
    collect day would subtract from spend other lanes really incurred TODAY. The record
    already carries the submit timestamp — pass it, or the guard cannot tell."""
    seen: dict = {}

    def _fake(redis_client, reserved_usd, actual_usd, reserved_at=None):  # noqa: ANN001, ANN202
        seen.update(reserved=reserved_usd, actual=actual_usd, day=reserved_at)

    rio = _stamped()
    booked_on = rio.descricao_batch_submitted_at
    session = _FakeSession([], ["msgbatch_01"])
    session.register(rio)
    with patch("brave.lanes.atrativos.copy_batch.reconcile_spend", _fake):
        _collect(
            session,
            _FakeBatches(results=[_entry(rio, _result("expired"))]),
            _redis(),
        )

    assert seen["day"] == booked_on
    assert seen["actual"] == 0.0  # unbilled outcome releases the whole reservation


def test_a_poison_batch_id_does_not_stop_the_other_batches() -> None:
    """One un-retrievable id (404 past the 29-day retention, rotated key) used to abort
    collection for every other batch."""
    good = _stamped("msgbatch_good")
    session = _FakeSession([], ["msgbatch_poison", "msgbatch_good"])
    session.register(good)
    batches = _FakeBatches(
        results_by_id={
            "msgbatch_good": [_entry(good, _result("succeeded", _message("Prosa boa.")))]
        },
        poison={"msgbatch_poison"},
    )

    assert _collect(session, batches, _redis()) == 1
    assert good.normalized["descricao_editorial"] == "Prosa boa."
    assert session.rollbacks == 1  # the poison batch rolled back, alone


def test_collect_reaps_before_it_reads_the_worklist() -> None:
    stranded = _make_rio()
    stranded.descricao_batch_id = CLAIM_BATCH_ID
    stranded.descricao_batch_submitted_at = datetime.now(UTC) - timedelta(hours=30)
    session = _FakeSession([stranded], [])

    assert _collect(session, _FakeBatches(), _redis()) == 0
    assert stranded.descricao_batch_id is None


# ---------------------------------------------------------------------------
# D7 — the collect task must NOT publish to norteia-api
# ---------------------------------------------------------------------------


def test_collect_task_dispatches_no_push() -> None:
    """A new description can lift a record over the threshold. Pushing it here would
    publish an attraction to norteia-api with ZERO human validation — the DLQ/steward gate
    is the canonical gate, and the only push dispatchers are /approve and WhatsApp finalize.
    """
    from brave.tasks.pipeline import collect_description_batches_task  # noqa: PLC0415

    raw_fn = collect_description_batches_task.__wrapped__.__func__
    session = MagicMock()

    with (
        patch("brave.tasks.pipeline._get_session", return_value=(session, MagicMock())),
        patch("brave.tasks.pipeline.AppConfig") as app_config,
        patch("brave.tasks.pipeline.load_effective_config"),
        patch("brave.tasks.pipeline._batch_deps", return_value=(MagicMock(), MagicMock())),
        patch("brave.tasks.pipeline.push_attraction_task") as push,
        patch("brave.lanes.atrativos.copy_batch.collect_batches", return_value=3) as collect,
    ):
        app_config.return_value.run_real_externals = True
        raw_fn(SimpleNamespace())

    collect.assert_called_once()
    push.delay.assert_not_called()
    push.apply_async.assert_not_called()


# ---------------------------------------------------------------------------
# beat wiring
# ---------------------------------------------------------------------------


def test_the_batch_id_index_is_partial() -> None:
    """descricao_batch_id is NULL for essentially every row: a plain B-tree would carry one
    entry per rio_record and still not serve submit's `IS NULL` predicate (the planner
    seq-scans anyway). The partial index is a few hundred entries and is what collect's
    DISTINCT actually reads. (It is not cheaper to BUILD — both scan the whole table under a
    SHARE lock; see migration 0012 for that deploy cost.)"""
    from sqlalchemy.schema import CreateIndex  # noqa: PLC0415

    index = next(
        i for i in RioRecord.__table__.indexes if i.name == "ix_rio_records_descricao_batch_id"
    )
    ddl = str(CreateIndex(index).compile(dialect=postgresql.dialect()))
    assert "WHERE descricao_batch_id IS NOT NULL" in ddl


def test_beat_entries_are_registered_without_a_queue_option() -> None:
    from brave.tasks.beat_schedule import maintenance_beat_entries  # noqa: PLC0415

    entries = maintenance_beat_entries()
    for name in ("submit-description-batch-hourly", "collect-description-batches-15min"):
        assert name in entries
        assert "options" not in entries[name]
