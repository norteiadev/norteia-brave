"""Editorial extraction over the Message Batches API: fake Anthropic client, test DB
(SAVEPOINT, rolled back), fakeredis. Nothing leaves the machine.

What costs money or strands posts: the claim is committed BEFORE ``batches.create``; the
budget is reserved at submit and reconciled at collect; collecting twice writes nothing."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import fakeredis
import httpx
import pytest
from anthropic import BadRequestError, InternalServerError, NotFoundError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from brave.clients.llm import anthropic_thinking
from brave.config.settings import AppConfig, LLMConfig
from brave.core.models import EditorialClaim, EditorialDocument, LLMGeneration
from brave.domains.editorial.extract_batch import (
    AMBIGUOUS_BATCH_ID,
    CLAIM_BATCH_ID,
    TOOL_NAME,
    build_request,
    collect_batches,
    custom_id,
    reap_stale_claims,
    retry_errored,
    submit_batch,
)
from brave.domains.editorial.schema import wire_json_schema
from brave.observability.cost_guard import daily_spend

pytestmark = pytest.mark.integration

MODEL = "claude-sonnet-5-5"
# The defaults live in settings (single source); the lane reads them from there.
EST = AppConfig().editorial_est_usd_per_post
LIMIT = AppConfig().editorial_batch_size
_REQ = httpx.Request("POST", "https://api.anthropic.com/v1/messages/batches")


def _api_error(cls, status: int):  # noqa: ANN001, ANN202
    return cls("boom", response=httpx.Response(status, request=_REQ), body=None)


@pytest.fixture
def session(db_engine):
    conn = db_engine.connect()
    outer = conn.begin()
    s = Session(bind=conn, join_transaction_mode="create_savepoint")
    try:
        yield s
    finally:
        s.close()
        outer.rollback()
        conn.close()


@pytest.fixture
def redis():
    return fakeredis.FakeRedis()


def _doc(session, slug: str, uf: str = "ES", **kw) -> EditorialDocument:
    d = EditorialDocument(
        url=f"https://www.viagensecaminhos.com/{slug}/",
        source="viagensecaminhos",
        uf=uf,
        content_hash=slug.ljust(64, "0"),
        body_text=f"Texto do post {slug}.",
        kml_points=[],
        published_time=datetime(2020, 2, 17).date(),
        **kw,
    )
    session.add(d)
    session.flush()
    return d


class FakeBatches:
    def __init__(self, events: list[str], *, create_error: BaseException | None = None) -> None:
        self.events, self.create_error = events, create_error
        self.created: list[list[dict]] = []
        self.status: dict[str, str] = {}
        self.entries: dict[str, list] = {}
        self.missing: set[str] = set()

    def create(self, *, requests: list[dict]):
        self.events.append("create")
        if self.create_error:
            raise self.create_error
        self.created.append(requests)
        bid = f"msgbatch_{len(self.created)}"
        self.status[bid] = "in_progress"
        return SimpleNamespace(id=bid)

    def retrieve(self, bid: str):
        if bid in self.missing:
            raise _api_error(NotFoundError, 404)
        return SimpleNamespace(processing_status=self.status.get(bid, "in_progress"))

    def results(self, bid: str):
        return iter(self.entries.get(bid, []))


class FakeClient:
    def __init__(self, events: list[str] | None = None, **kw) -> None:
        self.events = events if events is not None else []
        self.messages = SimpleNamespace(batches=FakeBatches(self.events, **kw))

    @property
    def batches(self) -> FakeBatches:
        return self.messages.batches


def _ok(doc_url: str, entities: list[dict], *, stop: str = "tool_use", usage=None):
    block = SimpleNamespace(type="tool_use", name=TOOL_NAME, input={"entities": entities})
    return _answer(doc_url, [block], stop=stop, usage=usage)


def _answer(doc_url: str, content: list, *, stop: str, usage=None):
    msg = SimpleNamespace(
        content=content,
        stop_reason=stop,
        usage=usage or SimpleNamespace(input_tokens=10_000, output_tokens=2_000),
    )
    return SimpleNamespace(
        custom_id=custom_id(doc_url), result=SimpleNamespace(type="succeeded", message=msg)
    )


def _errored(kind="invalid_request_error", message="tool_choice forced while thinking is enabled"):
    return SimpleNamespace(
        type="errored",
        error=SimpleNamespace(type="error", error=SimpleNamespace(type=kind, message=message)),
    )


def _entity(kind="atrativo", name="Pedra do Frade", municipio="Pancas", **fields):
    """One entity as the strict tool returns it (the wire shape: a flat list of facts)."""
    return {
        "kind": kind,
        "name": name,
        "municipio": municipio,
        "facts": [{"field": k, "value": v, "evidence": "trecho"} for k, v in fields.items()],
    }


def _submit(session, client, redis, **kw):
    kw = {"llm_config": LLMConfig(), "limit": LIMIT, "est_usd": EST, **kw}
    return submit_batch(session, client, redis_client=redis, model=MODEL, **kw)


def _finish(client: FakeClient, bid: str, *entries) -> None:
    client.batches.status[bid] = "ended"
    client.batches.entries[bid] = list(entries)


def _collect(session, client, redis):
    return collect_batches(session, client, redis_client=redis, model=MODEL, est_usd=EST)


# -- build_request ------------------------------------------------------------------------


def test_build_request_contract(session) -> None:
    d = _doc(session, "anchieta-es")
    req = build_request(d, MODEL)
    assert re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", req["custom_id"])  # a URL would not fit
    p = req["params"]
    assert p["model"] == MODEL and "stream" not in p
    assert p["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}
    schema = p["tools"][0]["input_schema"]
    assert p["tools"][0]["name"] == TOOL_NAME and "entities" in schema["properties"]
    assert "discriminator" not in str(schema)  # an OpenAPI-ism the tool schema does not need
    user = p["messages"][0]["content"]
    assert d.url in user and "Texto do post anchieta-es." in user and "2020-02-17" in user
    assert "evidence" in p["system"] and "reescrev" in p["system"].lower()


MODELS = ["claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5", "claude-sonnet-4-5"]


@pytest.mark.parametrize("model", MODELS)
def test_tool_choice_is_never_forced(session, model) -> None:
    """Regression: claude-sonnet-5-5 answers 400 'tool_choice: type "tool" and "any" are not
    supported for this model.' to a forced tool_choice, with or without thinking and in
    Message Batches too — so every request of the batch errored."""
    p = build_request(_doc(session, "x-es"), model)["params"]
    assert p["tool_choice"]["type"] not in ("tool", "any")
    assert p["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}


@pytest.mark.parametrize("model", MODELS)
def test_thinking_is_the_models_own_off_switch(session, model) -> None:
    """between_tools on Sonnet 5.5 (sent alone): without it every post is billed adaptive
    thinking."""
    p = build_request(_doc(session, "x-es"), model)["params"]
    assert p.get("thinking") == anthropic_thinking(model)
    if model == "claude-sonnet-5-5":
        assert p["thinking"] == {"type": "between_tools"}


def test_the_tool_is_strict_with_the_wire_schema(session) -> None:
    p = build_request(_doc(session, "x-es"), MODEL)["params"]
    (tool,) = p["tools"]
    assert tool["strict"] is True and tool["name"] == TOOL_NAME
    assert tool["input_schema"] == wire_json_schema()


def test_the_prompt_ends_by_asking_for_exactly_one_tool_call(session) -> None:
    system = build_request(_doc(session, "x-es"), MODEL)["params"]["system"]
    last = system.strip().splitlines()[-1]
    assert TOOL_NAME in last and "exatamente uma vez" in last and "texto" in last


# -- submit -------------------------------------------------------------------------------


def test_submit_claims_the_posts_before_the_paid_create_and_stamps_the_id(session, redis) -> None:
    d = _doc(session, "a-es")
    events: list[str] = []
    client = FakeClient(events)
    seen: dict[str, Any] = {}
    orig = client.batches.create

    def spy(**kw):
        seen["claim"] = session.execute(
            select(EditorialDocument.batch_id).where(EditorialDocument.url == d.url)
        ).scalar_one()
        return orig(**kw)

    client.batches.create = spy
    bid = _submit(session, client, redis)
    assert bid == "msgbatch_1"
    assert seen["claim"] == CLAIM_BATCH_ID  # visible to a redelivered task BEFORE the spend
    session.refresh(d)
    assert d.batch_id == "msgbatch_1" and d.batch_submitted_at is not None
    assert daily_spend(redis) == pytest.approx(EST)  # reserved, one post


def test_submit_skips_extracted_errored_and_in_flight_posts_and_filters_by_uf(
    session, redis
) -> None:
    _doc(session, "done-es", extracted_at=datetime.now(UTC))
    _doc(session, "bad-es", error="x")
    _doc(session, "busy-es", batch_id="msgbatch_old")
    other = _doc(session, "mg-post", uf="MG")
    todo = _doc(session, "todo-es")
    client = FakeClient()
    _submit(session, client, redis, uf="ES")
    ids = [r["custom_id"] for r in client.batches.created[0]]
    assert ids == [custom_id(todo.url)] and custom_id(other.url) not in ids


def test_submit_without_work_creates_nothing_and_reserves_nothing(session, redis) -> None:
    client = FakeClient()
    assert _submit(session, client, redis) is None
    assert client.batches.created == [] and daily_spend(redis) == 0.0


def test_submit_is_sized_down_to_the_remaining_budget(session, redis) -> None:
    for i in range(5):
        _doc(session, f"p{i}-es")
    cfg = LLMConfig(usd_daily_budget=EST * 2.5)
    client = FakeClient()
    _submit(session, client, redis, llm_config=cfg)
    assert len(client.batches.created[0]) == 2
    assert daily_spend(redis) == pytest.approx(2 * EST)  # the rest was handed back


def test_a_refused_create_frees_the_posts_and_refunds(session, redis) -> None:
    d = _doc(session, "a-es")
    client = FakeClient(create_error=_api_error(BadRequestError, 400))
    with pytest.raises(BadRequestError):
        _submit(session, client, redis)
    session.refresh(d)
    assert d.batch_id is None and d.batch_submitted_at is None
    assert daily_spend(redis) == pytest.approx(0.0)


def test_an_ambiguous_create_keeps_the_posts_claimed_for_an_operator(session, redis) -> None:
    d = _doc(session, "a-es")
    client = FakeClient(create_error=_api_error(InternalServerError, 529))
    with pytest.raises(InternalServerError):
        _submit(session, client, redis)
    session.refresh(d)
    assert d.batch_id == AMBIGUOUS_BATCH_ID
    assert daily_spend(redis) == pytest.approx(EST)  # may be billing: not refunded


# -- collect ------------------------------------------------------------------------------


def _submitted(session, redis, *slugs: str):
    docs = [_doc(session, s) for s in slugs]
    client = FakeClient()
    bid = _submit(session, client, redis)
    return docs, client, bid


def test_collect_turns_a_finished_batch_into_claims(session, redis) -> None:
    (d,), client, bid = _submitted(session, redis, "a-es")
    _finish(
        client,
        bid,
        _ok(
            d.url,
            [
                _entity(
                    curiosities="Tombada em 1940.",
                    opening_hours="Das 8h às 18h.",
                    free_entry="sim",
                    tips="Entrada R$ 10. Leve água.",
                ),
                _entity("destino", "Pancas", "Pancas", seasonality="Chove de outubro a janeiro."),
            ],
        ),
    )
    assert _collect(session, client, redis) == {"ES"}
    session.refresh(d)
    assert d.extracted_at is not None and d.batch_id is None and d.error is None

    claims = {
        (c.entity_kind, c.field): c
        for c in session.scalars(select(EditorialClaim).where(EditorialClaim.document_url == d.url))
    }
    assert set(claims) == {
        ("atrativo", "curiosities"),
        ("atrativo", "opening_hours"),
        ("atrativo", "free_entry"),
        ("atrativo", "tips"),
        ("destino", "seasonality"),
    }
    cur = claims[("atrativo", "curiosities")]
    assert (cur.status, cur.stability, cur.value, cur.evidence) == (
        "unmatched",
        "estavel",
        "Tombada em 1940.",
        "trecho",
    )
    assert cur.entity_name == "Pedra do Frade" and cur.municipio_text == "Pancas"
    assert cur.published_time == d.published_time and cur.source == "viagensecaminhos"
    assert claims[("atrativo", "opening_hours")].status == "held_volatile"
    assert claims[("atrativo", "free_entry")].value is True
    assert claims[("atrativo", "tips")].value == "Leve água."  # D3: the R$ sentence is gone
    assert claims[("destino", "seasonality")].value == ["Chove de outubro a janeiro."]


def test_collect_records_the_batch_priced_cost_and_settles_the_reservation(session, redis) -> None:
    (d,), client, bid = _submitted(session, redis, "a-es")
    _finish(client, bid, _ok(d.url, [_entity(curiosities="Fato.")]))
    _collect(session, client, redis)
    gen = session.scalars(
        select(LLMGeneration).where(LLMGeneration.lane == "editorial_extract_batch")
    ).one()
    # sonnet-5-5: $2/$10 per MTok, batch = 50%  -> (10k*2 + 2k*10)/1e6 * 0.5
    assert float(gen.usd_cost) == pytest.approx(0.02)
    assert (
        gen.model_slug == MODEL and gen.prompt_tokens == 10_000 and gen.completion_tokens == 2_000
    )
    assert daily_spend(redis) == pytest.approx(0.02)  # reservation replaced by the real bill


def test_collecting_twice_writes_nothing_more(session, redis) -> None:
    (d,), client, bid = _submitted(session, redis, "a-es")
    _finish(client, bid, _ok(d.url, [_entity(curiosities="Fato.")]))
    _collect(session, client, redis)
    claims = session.scalar(select(func.count()).select_from(EditorialClaim))
    gens = session.scalar(
        select(func.count())
        .select_from(LLMGeneration)
        .where(LLMGeneration.lane == "editorial_extract_batch")
    )
    spend = daily_spend(redis)
    assert _collect(session, client, redis) == set()
    assert session.scalar(select(func.count()).select_from(EditorialClaim)) == claims
    assert (
        session.scalar(
            select(func.count())
            .select_from(LLMGeneration)
            .where(LLMGeneration.lane == "editorial_extract_batch")
        )
        == gens
    )
    assert daily_spend(redis) == spend


def test_a_batch_still_running_is_left_alone(session, redis) -> None:
    (d,), client, bid = _submitted(session, redis, "a-es")
    assert _collect(session, client, redis) == set()
    session.refresh(d)
    assert d.batch_id == bid and d.extracted_at is None


def test_re_extraction_replaces_the_old_claims_of_the_post(session, redis) -> None:
    (d,), client, bid = _submitted(session, redis, "a-es")
    _finish(client, bid, _ok(d.url, [_entity(curiosities="Velho fato.")]))
    _collect(session, client, redis)
    d.extracted_at = None  # the crawler reset it: the post changed
    session.flush()
    client2 = FakeClient()
    bid2 = _submit(session, client2, redis)
    _finish(client2, bid2, _ok(d.url, [_entity(curiosities="Fato novo.")]))
    _collect(session, client2, redis)
    values = session.scalars(
        select(EditorialClaim.value).where(EditorialClaim.document_url == d.url)
    ).all()
    assert values == ["Fato novo."]


def test_an_invalid_entity_is_dropped_and_the_rest_is_kept(session, redis) -> None:
    (d,), client, bid = _submitted(session, redis, "a-es")
    bad = _entity("destino", "Pancas", "Pancas", pace_profile="moderado")
    _finish(client, bid, _ok(d.url, [bad, _entity(curiosities="Fato.")]))
    _collect(session, client, redis)
    session.refresh(d)
    assert d.error is None and d.extracted_at is not None
    assert session.scalar(select(func.count()).select_from(EditorialClaim)) == 1


def test_nothing_valid_in_the_output_is_an_error_not_an_extraction(session, redis) -> None:
    (d,), client, bid = _submitted(session, redis, "a-es")
    _finish(
        client, bid, _ok(d.url, [_entity("destino", "Pancas", "Pancas", pace_profile="moderado")])
    )
    _collect(session, client, redis)
    session.refresh(d)
    assert d.extracted_at is None and "pace_profile" in d.error and d.batch_id is None


@pytest.mark.parametrize(
    ("stop", "content", "reason"),
    [
        (
            "end_turn",
            [SimpleNamespace(type="text", text="Aqui está a extração.")],
            "no_tool_use: stop_reason=end_turn",
        ),
        (
            "tool_use",
            [SimpleNamespace(type="tool_use", name="outra", input={})],
            "no_tool_use: stop_reason=tool_use",
        ),
        ("refusal", [], "refusal: stop_reason=refusal"),
    ],
)
def test_an_answer_without_the_tool_call_is_an_error_with_its_reason(
    session, redis, stop, content, reason
) -> None:
    (d,), client, bid = _submitted(session, redis, "a-es")
    _finish(client, bid, _answer(d.url, content, stop=stop))
    _collect(session, client, redis)
    session.refresh(d)
    assert d.extracted_at is None and d.batch_id is None and d.error.startswith(reason)
    assert retry_errored(session, "ES") == 1  # editorial-retry re-arms it


def test_a_tool_input_off_the_wire_schema_is_an_error(session, redis) -> None:
    (d,), client, bid = _submitted(session, redis, "a-es")
    _finish(client, bid, _ok(d.url, [{"kind": "atrativo", "name": "x"}]))
    _collect(session, client, redis)
    session.refresh(d)
    assert d.extracted_at is None and d.error.startswith("invalid_tool_input")


def test_a_truncated_answer_is_an_error(session, redis) -> None:
    (d,), client, bid = _submitted(session, redis, "a-es")
    _finish(client, bid, _ok(d.url, [_entity(curiosities="Fato.")], stop="max_tokens"))
    _collect(session, client, redis)
    session.refresh(d)
    assert d.extracted_at is None and "max_tokens" in d.error


def test_errored_burns_the_post_and_expired_frees_it(session, redis) -> None:
    (a, b), client, bid = _submitted(session, redis, "a-es", "b-es")
    _finish(
        client,
        bid,
        SimpleNamespace(custom_id=custom_id(a.url), result=_errored()),
        SimpleNamespace(custom_id=custom_id(b.url), result=SimpleNamespace(type="expired")),
    )
    _collect(session, client, redis)
    session.refresh(a)
    session.refresh(b)
    assert a.extracted_at is None and a.batch_id is None  # not retried automatically
    # the API's own reason, so the operator knows what to fix before a retry
    assert a.error == "invalid_request_error: tool_choice forced while thinking is enabled"
    assert b.error is None and b.extracted_at is None and b.batch_id is None  # eligible again
    assert daily_spend(redis) == pytest.approx(0.0)  # both reservations released


def test_an_errored_result_without_details_still_marks_the_post(session, redis) -> None:
    (d,), client, bid = _submitted(session, redis, "a-es")
    _finish(
        client,
        bid,
        SimpleNamespace(custom_id=custom_id(d.url), result=SimpleNamespace(type="errored")),
    )
    _collect(session, client, redis)
    session.refresh(d)
    assert d.error and "errored" in d.error


def test_retry_errored_frees_only_the_errored_posts_of_the_uf(session, redis) -> None:
    bad = _doc(session, "bad-es", error="invalid_request_error: x")
    other_uf = _doc(session, "bad-mg", uf="MG", error="x")
    done = _doc(session, "done-es", extracted_at=datetime.now(UTC))
    ambiguous = _doc(session, "amb-es", batch_id=AMBIGUOUS_BATCH_ID, error=None)
    assert retry_errored(session, "es") == 1
    for d in (bad, other_uf, done, ambiguous):
        session.refresh(d)
    assert bad.error is None and bad.batch_id is None
    assert other_uf.error == "x" and done.extracted_at is not None
    assert ambiguous.batch_id == AMBIGUOUS_BATCH_ID  # never by a retry: may be billing
    # the freed post is eligible again
    client = FakeClient()
    _submit(session, client, redis, uf="ES")
    assert [r["custom_id"] for r in client.batches.created[0]] == [custom_id(bad.url)]
    assert retry_errored(session, None) == 1  # every UF: the MG one


def test_a_foreign_custom_id_is_ignored(session, redis) -> None:
    (d,), client, bid = _submitted(session, redis, "a-es")
    foreign = SimpleNamespace(custom_id="not-ours", result=SimpleNamespace(type="errored"))
    _finish(client, bid, foreign, _ok(d.url, [_entity(curiosities="Fato.")]))
    assert _collect(session, client, redis) == {"ES"}


# -- reaper -------------------------------------------------------------------------------


def _age(session, d: EditorialDocument, hours: int = 26) -> None:
    d.batch_submitted_at = datetime.now(UTC) - timedelta(hours=hours)
    session.flush()


def test_reaper_frees_a_stale_claim_placeholder_by_time_alone(session) -> None:
    d = _doc(session, "a-es", batch_id=CLAIM_BATCH_ID)
    _age(session, d)
    assert reap_stale_claims(session, FakeClient()) == 1
    session.refresh(d)
    assert d.batch_id is None and d.batch_submitted_at is None


def test_reaper_keeps_a_real_batch_until_it_is_gone_and_never_frees_ambiguous(session) -> None:
    live = _doc(session, "live-es", batch_id="msgbatch_live")
    gone = _doc(session, "gone-es", batch_id="msgbatch_gone")
    amb = _doc(session, "amb-es", batch_id=AMBIGUOUS_BATCH_ID)
    fresh = _doc(session, "fresh-es", batch_id=CLAIM_BATCH_ID)
    for d in (live, gone, amb):
        _age(session, d)
    client = FakeClient()
    client.batches.missing = {"msgbatch_gone"}
    assert reap_stale_claims(session, client) == 1
    for d in (live, gone, amb, fresh):
        session.refresh(d)
    assert (live.batch_id, gone.batch_id, amb.batch_id, fresh.batch_id) == (
        "msgbatch_live",
        None,
        AMBIGUOUS_BATCH_ID,
        CLAIM_BATCH_ID,
    )


def test_reaper_without_a_client_frees_only_placeholders(session) -> None:
    real = _doc(session, "real-es", batch_id="msgbatch_x")
    ph = _doc(session, "ph-es", batch_id=CLAIM_BATCH_ID)
    _age(session, real)
    _age(session, ph)
    assert reap_stale_claims(session, None) == 1
    session.refresh(real)
    session.refresh(ph)
    assert real.batch_id == "msgbatch_x" and ph.batch_id is None
