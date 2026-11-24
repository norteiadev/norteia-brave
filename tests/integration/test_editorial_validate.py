"""Web validation of perishable editorial facts (Gemini 2.5 Flash + Google Search grounding).

The LLM is a scripted stub that answers from the facts it is sent: no network, no Gemini.
Test DB (SAVEPOINT, rolled back). UF "ZZ" keeps it apart from real data."""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from brave.clients.base import GroundedAnswer
from brave.clients.parallel import ParallelSearch
from brave.core.models import (
    EditorialClaim,
    EditorialDocument,
    Municipio,
    NascenteRecord,
    RioRecord,
)
from brave.domains.editorial.validate import (
    ANSWER_SCHEMA,
    FactVerdict,
    build_prompt,
    parse_answer,
    pending_entities,
    reopen_failed,
    reopen_validated,
    validate_pending,
)
from brave.shared.exceptions import CostGuardError

pytestmark = pytest.mark.integration

UF = "ZZ"
IBGE = "9990001"
TODAY = date(2026, 11, 12)
MODEL = "gemini-2.5-flash"
SOURCE = {"uri": "https://vertex.example/r/1", "title": "parque.es.gov.br"}


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


def rio(session, key, name, municipio="Pancas"):
    n = NascenteRecord(
        id=uuid.uuid4(), source="t", source_ref=key, entity_type="attraction", uf=UF,
        payload={}, content_hash=f"h:{key}", version=1,
    )  # fmt: skip
    session.add(n)
    session.flush()
    session.add(
        RioRecord(
            nascente_id=n.id,
            canonical_key=key,
            entity_type="attraction",
            uf=UF,
            municipio_id=IBGE,
            routing="mar",
            score=80,
            normalized={"name": name, "municipio": municipio},
        )  # fmt: skip
    )
    session.flush()


def doc(session, slug="p1", published=date(2024, 5, 1)):
    d = EditorialDocument(
        url=f"https://www.viagensecaminhos.com/{slug}/", source="viagensecaminhos", uf=UF,
        content_hash=slug.ljust(64, "0"), body_text="x", published_time=published,
        kml_points=[], extracted_at=datetime.now(UTC),
    )  # fmt: skip
    session.add(d)
    session.flush()
    return d


def claim(session, d, field, value, key="ta:1", kind="atrativo", status="pending_validation",
          name="Pedra do Frade"):  # fmt: skip
    c = EditorialClaim(
        id=uuid.uuid4(), document_url=d.url, source=d.source, entity_kind=kind, entity_name=name,
        municipio_text="Pancas", field=field, value=value, stability="estavel", evidence="x",
        published_time=d.published_time, status=status, canonical_key=key,
    )  # fmt: skip
    session.add(c)
    session.flush()
    return c


class ScriptedLLM:
    """Answers ``verdicts[field]`` (default confirmado) for every fact id in the user prompt."""

    def __init__(self, verdicts=None, *, grounded=True, text=None, error=None, fail_on=None):
        self.verdicts, self.grounded, self.text, self.error = verdicts or {}, grounded, text, error
        self.fail_on, self.calls = fail_on, []

    cite = "https://exemplo.gov.br/pagina"

    def _answer(self, messages, model, system, **kw) -> str:
        self.calls.append({"messages": messages, "model": model, "system": system, **kw})
        if self.fail_on is not None and len(self.calls) == self.fail_on:
            raise CostGuardError("budget")
        if self.error:
            raise self.error
        user = messages[0]["content"]
        facts = re.findall(r"id=(\S+) \| coluna=(\w+)", user)
        if self.text is not None:
            return self.text
        return json.dumps(
            {
                "facts": [
                    {
                        "id": i,
                        "verdict": self.verdicts.get(col, "confirmado"),
                        "sources": [self.cite],
                        "note": "A página oficial confirma.",
                    }
                    for i, col in facts
                ]
            }
        )

    async def generate(self, messages, model, *, system=None, tools=None, response_schema=None):
        return self._answer(messages, model, system, response_schema=response_schema)

    async def generate_grounded(self, messages, model, *, system=None) -> GroundedAnswer:
        text = self._answer(messages, model, system)
        return GroundedAnswer(
            text=text,
            sources=(SOURCE,) if self.grounded else (),
            queries=("q",) if self.grounded else (),
        )


class ScriptedSearch:
    """Parallel-shaped: returns ``results`` for every search and records the requests."""

    def __init__(self, results=None):
        self.results = (
            [
                {
                    "url": "https://exemplo.gov.br/pagina/",
                    "title": "Prefeitura",
                    "publish_date": "2026-03-01",
                    "excerpts": ["O parque abre todos os dias e tem estacionamento."],
                }
            ]
            if results is None
            else results
        )
        self.calls = []

    async def search(self, queries, objective):
        self.calls.append({"queries": queries, "objective": objective})
        return ParallelSearch(
            search_id="s1", mode="turbo", objective=objective, queries=list(queries),
            results=self.results, usage=None, warnings=None, usd=0.001, latency_ms=1,
        )  # fmt: skip


def run(session, llm, **kw):
    import asyncio

    return asyncio.run(
        validate_pending(
            session,
            llm,
            model=MODEL,
            uf=UF,
            limit=kw.pop("limit", 50),
            max_attempts=kw.pop("max_attempts", 3),
            today=TODAY,
            **kw,
        )
    )


def fresh(session, row):
    session.expire(row)
    return row


# -- pure: the prompt and the answer ----------------------------------------------------------


def test_the_prompt_carries_the_entity_the_place_the_post_date_and_every_fact() -> None:
    prompt = build_prompt(
        kind="atrativo", name="Pedra do Frade", post_name="Pedra do Frade e a Freira",
        municipio="Pancas", uf="ES", today=TODAY,
        facts=[("c1", "tips", date(2024, 5, 1), "Leve água."), ("c2", "accessibility", None, "Rampa.")],
    )  # fmt: skip
    assert "Pedra do Frade" in prompt and "Pancas" in prompt and "ES" in prompt
    assert "2026-11-12" in prompt  # today: what "still holds" is measured against
    assert "id=c1 | coluna=tips | post=2024-05-01 | fato: Leve água." in prompt
    assert "id=c2 | coluna=accessibility | post=? | fato: Rampa." in prompt
    assert "Pedra do Frade e a Freira" in prompt  # the name the post used


def test_parse_answer_reads_json_with_or_without_a_code_fence() -> None:
    raw = '{"facts":[{"id":"a","verdict":"confirmado","sources":["https://x.br/a"],"note":"ok"}]}'
    expected = [FactVerdict(id="a", verdict="confirmado", sources=["https://x.br/a"], note="ok")]
    assert parse_answer(raw) == expected
    assert parse_answer(f"```json\n{raw}\n```") == expected
    assert parse_answer(f"Segue o resultado:\n{raw}\nFim.") == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "não achei nada",
        '{"facts": [{"id": "a", "verdict": "talvez"}]}',  # verdict outside the enum
        '{"facts": "nope"}',
        '{"resultado": []}',
    ],
)
def test_parse_answer_rejects_what_is_not_the_contract(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_answer(raw)


def test_parse_answer_keeps_only_http_urls_and_caps_them() -> None:
    raw = json.dumps(
        {
            "facts": [
                {
                    "id": "a",
                    "verdict": "confirmado",
                    "sources": [
                        "https://a.br",
                        "ftp://b",
                        "texto solto",
                        "http://c.br",
                        "https://d.br",
                        "https://e.br",
                    ],
                    "note": "n" * 500,
                }
            ]
        }
    )
    (fact,) = parse_answer(raw)
    assert fact.sources == ["https://a.br", "http://c.br", "https://d.br"]
    assert len(fact.note) <= 300


# -- the run ------------------------------------------------------------------------------------


def test_a_confirmed_fact_goes_back_to_matched_with_its_proof(session) -> None:
    rio(session, "ta:1", "Pedra do Frade")
    c = claim(session, doc(session), "tips", "Leve água.")
    llm = ScriptedLLM()
    result = run(session, llm)
    fresh(session, c)
    assert c.status == "matched" and c.validation_verdict == "confirmado"
    assert c.validation_note == "A página oficial confirma."
    assert c.validation_sources == {
        "cited": ["https://exemplo.gov.br/pagina"],
        "grounding": [SOURCE],
        "queries": ["q"],
    }
    assert c.validated_at is not None
    assert (result.entities, result.confirmed, result.outdated, result.unverified) == (1, 1, 0, 0)
    assert result.ufs == {UF}


def test_outdated_and_no_evidence_are_kept_out_of_the_push_but_kept(session) -> None:
    rio(session, "ta:1", "Pedra do Frade")
    d = doc(session)
    old = claim(session, d, "tips", "Dica velha.")
    nope = claim(session, d, "how_to_get_there", "Pela estrada velha.")
    ok = claim(session, d, "accessibility", "Rampa na entrada.")
    run(session, ScriptedLLM({"tips": "desatualizado", "how_to_get_there": "sem_evidencia"}))
    assert (fresh(session, old).status, old.validation_verdict) == ("outdated", "desatualizado")
    assert (fresh(session, nope).status, nope.validation_verdict) == ("unverified", "sem_evidencia")
    assert fresh(session, ok).status == "matched"
    assert session.scalar(select(func.count()).select_from(EditorialClaim)) == 3  # nothing deleted


def test_confirmed_without_any_grounding_is_not_trusted(session) -> None:
    """The model's own memory is not evidence: no grounding page, no confirmation."""
    rio(session, "ta:1", "Pedra do Frade")
    c = claim(session, doc(session), "tips", "Leve água.")
    run(session, ScriptedLLM(grounded=False))
    fresh(session, c)
    assert (c.status, c.validation_verdict) == ("unverified", "sem_evidencia")
    assert "busca" in c.validation_note


def test_one_call_per_entity_with_gemini_and_the_search_grounded_prompt(session) -> None:
    rio(session, "ta:1", "Pedra do Frade")
    rio(session, "ta:2", "Gruta Azul")
    d = doc(session)
    claim(session, d, "tips", "A.", key="ta:1")
    claim(session, d, "accessibility", "B.", key="ta:1")
    claim(session, d, "tips", "C.", key="ta:2", name="Gruta Azul")
    llm = ScriptedLLM()
    result = run(session, llm)
    assert len(llm.calls) == 2 and result.entities == 2
    assert {c["model"] for c in llm.calls} == {MODEL}
    first = next(c for c in llm.calls if "Pedra do Frade" in c["messages"][0]["content"])
    assert first["messages"][0]["content"].count("id=") == 2  # both facts, one prompt
    assert "Pancas" in first["messages"][0]["content"] and UF in first["messages"][0]["content"]


def test_a_destination_is_looked_up_by_its_ibge_code(session) -> None:
    session.add(Municipio(ibge_code=IBGE, nome="Pancas", uf=UF, lat=-19.2, lng=-40.8))
    session.flush()
    c = claim(
        session, doc(session), "tips", "Chegue cedo.", key=IBGE, kind="destino", name="Pancas"
    )
    llm = ScriptedLLM()
    run(session, llm)
    assert "destino" in llm.calls[0]["messages"][0]["content"].lower()
    assert fresh(session, c).status == "matched"


def test_an_unreadable_answer_leaves_the_claims_pending_for_the_next_run(session) -> None:
    rio(session, "ta:1", "Pedra do Frade")
    c = claim(session, doc(session), "tips", "Leve água.")
    result = run(session, ScriptedLLM(text="desculpe, não consegui"))
    assert fresh(session, c).status == "pending_validation" and c.validation_verdict is None
    assert (result.failed, result.confirmed) == (1, 0)


def test_a_fact_the_model_skipped_stays_pending(session) -> None:
    rio(session, "ta:1", "Pedra do Frade")
    d = doc(session)
    a = claim(session, d, "tips", "A.")
    b = claim(session, d, "accessibility", "B.")
    only_a = json.dumps(
        {"facts": [{"id": str(a.id), "verdict": "confirmado", "sources": [], "note": ""}]}
    )
    result = run(session, ScriptedLLM(text=only_a))
    assert (
        fresh(session, a).status == "matched" and fresh(session, b).status == "pending_validation"
    )
    assert result.unanswered == 1


def test_a_failing_entity_does_not_stop_the_others(session) -> None:
    rio(session, "ta:1", "Pedra do Frade")
    rio(session, "ta:2", "Gruta Azul")
    d = doc(session)
    claim(session, d, "tips", "A.", key="ta:1")
    c2 = claim(session, d, "tips", "C.", key="ta:2", name="Gruta Azul")

    class Flaky(ScriptedLLM):
        async def generate_grounded(self, messages, model, *, system=None):
            if "Pedra do Frade" in messages[0]["content"]:
                raise RuntimeError("503")
            return await super().generate_grounded(messages, model, system=system)

    result = run(session, Flaky())
    assert (result.failed, result.confirmed) == (1, 1)
    assert fresh(session, c2).status == "matched"


def test_the_budget_wall_stops_the_run_but_keeps_what_was_paid_for(session) -> None:
    rio(session, "ta:1", "Pedra do Frade")
    rio(session, "ta:2", "Gruta Azul")
    d = doc(session)
    first = claim(session, d, "tips", "A.", key="ta:1")
    second = claim(session, d, "tips", "C.", key="ta:2", name="Gruta Azul")
    with pytest.raises(CostGuardError):
        run(session, ScriptedLLM(fail_on=2))
    assert fresh(session, first).status == "matched"  # entity 1 was committed
    assert fresh(session, second).status == "pending_validation"


def test_limit_caps_the_entities_per_run_and_only_pending_claims_are_read(session) -> None:
    d = doc(session)
    for i in range(3):
        rio(session, f"ta:{i}", f"Lugar {i}")
        claim(session, d, "tips", f"Dica {i}.", key=f"ta:{i}", name=f"Lugar {i}")
    claim(session, d, "tips", "Já matched.", key="ta:9", status="matched")
    claim(session, d, "tips", "Sem chave.", key=None)
    llm = ScriptedLLM()
    assert run(session, llm, limit=2).entities == 2
    assert len(pending_entities(session, UF)) == 1


def test_pending_entities_counts_distinct_entities_not_claims(session) -> None:
    d = doc(session)
    claim(session, d, "tips", "A.", key="ta:1")
    claim(session, d, "accessibility", "B.", key="ta:1")
    claim(session, d, "tips", "C.", key="ta:2")
    other = doc(session, "p2")
    claim(session, other, "tips", "D.", key="ta:1")  # same entity, another post: still one
    assert len(pending_entities(session, UF)) == 2
    assert len(pending_entities(session, "XX")) == 0


# -- an entity that keeps failing is not paid for forever ---------------------------------------


def test_a_failed_attempt_is_written_on_the_claim(session) -> None:
    rio(session, "ta:1", "Pedra do Frade")
    c = claim(session, doc(session), "tips", "Leve água.")
    run(session, ScriptedLLM(text="desculpe, não consegui"))
    fresh(session, c)
    assert c.status == "pending_validation" and c.validation_attempts == 1
    assert "JSON" in c.last_validation_error and c.last_attempt_at is not None


def test_after_the_limit_the_claim_is_kept_but_never_paid_for_again(session) -> None:
    rio(session, "ta:1", "Pedra do Frade")
    c = claim(session, doc(session), "tips", "Leve água.")
    broken = ScriptedLLM(text="RECITATION")
    for _ in range(2):
        run(session, broken, max_attempts=3)
    assert fresh(session, c).status == "pending_validation" and c.validation_attempts == 2
    result = run(session, broken, max_attempts=3)
    assert (fresh(session, c).status, c.validation_attempts) == ("validation_failed", 3)
    assert result.exhausted == 1 and session.get(EditorialClaim, c.id) is not None

    calls = len(broken.calls)
    run(session, broken, max_attempts=3)
    assert len(broken.calls) == calls  # nothing pending: no new paid call


def test_a_call_that_raises_counts_as_an_attempt_too(session) -> None:
    rio(session, "ta:1", "Pedra do Frade")
    c = claim(session, doc(session), "tips", "Leve água.")
    run(session, ScriptedLLM(error=RuntimeError("finishReason=SAFETY")))
    assert fresh(session, c).validation_attempts == 1 and "SAFETY" in c.last_validation_error


def test_a_skipped_fact_counts_as_an_attempt(session) -> None:
    rio(session, "ta:1", "Pedra do Frade")
    d = doc(session)
    a = claim(session, d, "tips", "A.")
    b = claim(session, d, "accessibility", "B.")
    only_a = json.dumps(
        {"facts": [{"id": str(a.id), "verdict": "confirmado", "sources": [], "note": ""}]}
    )
    run(session, ScriptedLLM(text=only_a))
    assert fresh(session, b).validation_attempts == 1 and fresh(session, a).validation_attempts == 0


def test_the_budget_wall_is_not_an_attempt(session) -> None:
    rio(session, "ta:1", "Pedra do Frade")
    c = claim(session, doc(session), "tips", "Leve água.")
    with pytest.raises(CostGuardError):
        run(session, ScriptedLLM(fail_on=1))
    assert fresh(session, c).validation_attempts == 0


def test_the_batch_takes_the_least_tried_entities_first(session) -> None:
    d = doc(session)
    rio(session, "ta:1", "Lugar 1")
    rio(session, "ta:2", "Lugar 2")
    tried = claim(session, d, "tips", "A.", key="ta:1", name="Lugar 1")
    tried.validation_attempts, tried.last_attempt_at = 2, datetime(2026, 11, 1, tzinfo=UTC)
    claim(session, d, "tips", "B.", key="ta:2", name="Lugar 2")  # sorts later, never tried
    assert pending_entities(session, UF) == [("atrativo", "ta:2"), ("atrativo", "ta:1")]
    llm = ScriptedLLM()
    run(session, llm, limit=1)
    assert "Lugar 2" in llm.calls[0]["messages"][0]["content"]


def test_among_equally_tried_entities_the_oldest_attempt_goes_first(session) -> None:
    d = doc(session)
    newer = claim(session, d, "tips", "A.", key="ta:1")
    newer.validation_attempts, newer.last_attempt_at = 1, datetime(2026, 11, 10, tzinfo=UTC)
    older = claim(session, d, "tips", "B.", key="ta:2")
    older.validation_attempts, older.last_attempt_at = 1, datetime(2026, 11, 2, tzinfo=UTC)
    session.flush()
    assert pending_entities(session, UF) == [("atrativo", "ta:2"), ("atrativo", "ta:1")]


def test_reopen_failed_puts_the_exhausted_claims_back_in_line(session) -> None:
    d = doc(session)
    dead = claim(session, d, "tips", "A.", status="validation_failed")
    dead.validation_attempts, dead.last_validation_error = 3, "RECITATION"
    other = claim(session, doc(session, "p2"), "tips", "B.", status="outdated")
    assert reopen_failed(session, UF) == 1
    assert fresh(session, dead).status == "pending_validation" and dead.validation_attempts == 0
    assert dead.last_validation_error == "RECITATION"  # the history stays
    assert fresh(session, other).status == "outdated"
    assert reopen_failed(session, "XX") == 0


# -- the Parallel search path ---------------------------------------------------------------------


def test_parallel_one_search_then_gemini_in_a_schema_judges_by_the_excerpts(session) -> None:
    rio(session, "ta:1", "Pedra do Frade")
    d = doc(session)
    c = claim(session, d, "tips", "Leve água.")
    claim(session, d, "how_to_get_there", "Pela BR-259.")
    llm, search = ScriptedLLM(), ScriptedSearch()
    result = run(session, llm, search=search)
    assert len(search.calls) == 1 and len(llm.calls) == 1  # one search + one call per entity
    (q,) = search.calls
    assert all("Pedra do Frade" in x and "Pancas" in x for x in q["queries"])
    assert "como chegar" in q["objective"] and "dicas" in q["objective"]
    call = llm.calls[0]
    assert call["response_schema"] is ANSWER_SCHEMA and "Google" not in call["system"]
    user = call["messages"][0]["content"]
    assert "estacionamento" in user and "data: 2026-03-01" in user
    fresh(session, c)
    assert (c.status, c.validation_verdict) == ("matched", "confirmado")
    assert c.validation_sources == {
        "cited": [ScriptedLLM.cite],
        "search": ["https://exemplo.gov.br/pagina/"],
        "queries": q["queries"],
        "search_id": "s1",
    }
    assert result.confirmed == 2


def test_parallel_a_confirmation_citing_a_page_the_search_did_not_return_is_not_trusted(
    session,
) -> None:
    rio(session, "ta:1", "Pedra do Frade")
    c = claim(session, doc(session), "tips", "Leve água.")
    llm = ScriptedLLM()
    llm.cite = "https://outro-site.com.br/x"
    run(session, llm, search=ScriptedSearch())
    fresh(session, c)
    assert (c.status, c.validation_verdict) == ("unverified", "sem_evidencia")
    assert "busca" in c.validation_note


def test_parallel_an_empty_search_is_no_evidence_without_a_paid_generation(session) -> None:
    rio(session, "ta:1", "Pedra do Frade")
    c = claim(session, doc(session), "tips", "Leve água.")
    llm = ScriptedLLM()
    result = run(session, llm, search=ScriptedSearch(results=[]))
    assert llm.calls == []
    assert (fresh(session, c).status, c.validation_verdict) == ("unverified", "sem_evidencia")
    assert result.unverified == 1 and c.validation_attempts == 0


def test_parallel_the_guide_s_own_site_is_never_evidence(session) -> None:
    rio(session, "ta:1", "Pedra do Frade")
    c = claim(session, doc(session), "tips", "Leve água.")
    own = [{"url": "https://www.viagensecaminhos.com/p1/", "excerpts": ["Leve água."]}]
    llm = ScriptedLLM()
    run(session, llm, search=ScriptedSearch(results=own))
    assert llm.calls == []  # only the post itself came back: nothing to judge by
    assert fresh(session, c).validation_verdict == "sem_evidencia"
    assert c.validation_sources["search"] == []


def test_revalidating_keeps_the_old_verdict_on_the_claim(session) -> None:
    rio(session, "ta:1", "Pedra do Frade")
    c = claim(session, doc(session), "tips", "Leve água.")
    run(session, ScriptedLLM({"tips": "desatualizado"}))
    assert fresh(session, c).status == "outdated"
    first_at = c.validated_at

    assert reopen_validated(session, UF) == 1
    fresh(session, c)
    assert (c.status, c.validation_verdict, c.validation_attempts) == (
        "pending_validation",
        None,
        0,
    )
    (prev,) = c.validation_sources["previous"]
    assert prev["verdict"] == "desatualizado" and prev["status"] == "outdated"
    assert prev["validated_at"] == first_at.isoformat() and prev["sources"]["grounding"] == [SOURCE]

    run(session, ScriptedLLM(), search=ScriptedSearch())
    fresh(session, c)
    assert (c.status, c.validation_verdict) == ("matched", "confirmado")
    assert c.validation_sources["previous"] == [prev]  # the history rides on the new verdict
    assert reopen_validated(session, "XX") == 0
