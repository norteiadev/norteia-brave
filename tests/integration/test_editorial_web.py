"""Editorial source "web": Mar attractions' editorial columns written from public pages.

Scripted search + LLM (no network). Test DB (SAVEPOINT, rolled back). UF "ZZ"."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from brave.clients.parallel import ParallelSearch
from brave.core.models import (
    EditorialClaim,
    EditorialDocument,
    EditorialDocumentVersion,
    MarRecord,
    NascenteRecord,
    RioRecord,
)
from brave.domains.editorial import web
from brave.domains.editorial.resolver import resolve_uf
from brave.shared.exceptions import CostGuardError

pytestmark = pytest.mark.integration

UF = "ZZ"
TODAY = date(2026, 11, 24)
MODEL = "gemini-3.5-flash-lite"
GOV = "https://turismo.exemplo.gov.br/pedra"


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


def attraction(s, key="ta:1", name="Pedra do Frade", *, mar=True, uf=UF):
    n = NascenteRecord(
        id=uuid.uuid4(), source="t", source_ref=key, entity_type="attraction", uf=uf,
        payload={}, content_hash=f"h:{key}", version=1,
    )  # fmt: skip
    s.add(n)
    s.flush()
    r = RioRecord(
        nascente_id=n.id, canonical_key=key, entity_type="attraction", uf=uf, routing="mar",
        score=80, normalized={"name": name, "municipio": "Pancas"},
    )  # fmt: skip
    s.add(r)
    s.flush()
    if mar:
        s.add(
            MarRecord(
                rio_id=r.id,
                entity_type="attraction",
                source_ref=key,
                canonical={},
                provenance={},
                reliability_score=90,
                score_version="v1",
            )  # fmt: skip
        )
        s.flush()
    return r


GOOD = {
    "how_to_get_there": {
        "value": "O acesso é pela ES-080, a 12 km do centro de Pancas, com placa na entrada.",
        "sources": [GOV],
    },
    "tips": {
        "value": "Leve água e protetor solar; a subida é exposta ao sol. Abre das 8h às 17h.",
        "sources": [GOV],
    },
    "local_infrastructure": {
        "value": "Não há informações específicas sobre estacionamento ou banheiros no local.",
        "sources": [GOV],
    },
    "curiosities": {
        "value": "O nome vem do formato das duas rochas, que lembram um frade e uma freira.",
        "sources": ["https://outro-site.com.br/nao-veio-da-busca"],
    },
    "accessibility": {
        "value": "O mirante é bonito e fica perto da estrada principal da região.",
        "sources": [GOV],
    },
}


class Search:
    def __init__(self, results=None):
        self.results = (
            [
                {"url": GOV, "title": "Turismo", "excerpts": ["A Pedra do Frade fica em Pancas."]},
                {"url": "https://www.viagensecaminhos.com/pancas/", "excerpts": ["blog"]},
                {"url": "https://www.google.com/maps/place?q=x", "excerpts": ["maps"]},
            ]
            if results is None
            else results
        )
        self.calls = []

    async def search(self, queries, objective):
        self.calls.append(queries)
        return ParallelSearch(
            search_id=f"s{len(self.calls)}", mode="turbo", objective=objective,
            queries=list(queries), results=self.results, usage=None, warnings=None, usd=0.001,
            latency_ms=1,
        )  # fmt: skip


class LLM:
    def __init__(self, answer=None, error=None):
        self.answer, self.error, self.calls = answer or GOOD, error, []

    async def generate(self, messages, model, *, system=None, tools=None, response_schema=None):
        self.calls.append({"messages": messages, "model": model, "schema": response_schema})
        if self.error:
            raise self.error
        return json.dumps(self.answer)


def run(s, llm, search, targets=None):
    targets = (
        targets
        if targets is not None
        else web.pending_attractions(s, UF, refresh_days=365, limit=50)
    )
    return asyncio.run(web.fill(s, llm, search, model=MODEL, targets=targets, today=TODAY))


def claims(s, key="ta:1"):
    return {
        c.field: c
        for c in s.scalars(
            select(EditorialClaim).where(
                EditorialClaim.canonical_key == key, EditorialClaim.status != "superseded"
            )
        )
    }


# -- pure ---------------------------------------------------------------------------------------


def test_the_blog_and_low_signal_hosts_never_reach_the_prompt() -> None:
    kept = web.usable_results(Search().results)
    assert [r["url"] for r in kept] == [GOV]


@pytest.mark.parametrize(
    ("column", "value", "reason"),
    [
        ("tips", "Curto.", "curto"),
        ("tips", "Não há informações específicas sobre o que levar para a trilha.", "enchimento"),
        ("tips", "É recomendado confirmar o horário com a prefeitura antes de ir.", "enchimento"),
        (
            "accessibility",
            "O lugar é tranquilo e bonito, bom para passar a tarde.",
            "sem fato de acessibilidade",
        ),
        ("accessibility", "Há rampa de acesso e banheiro adaptado no centro de visitantes.", None),
    ],
)
def test_drop_reason(column, value, reason) -> None:
    assert web.drop_reason(column, value, [GOV], "trecho qualquer") == reason


# -- the run ------------------------------------------------------------------------------------


def test_only_active_mar_attractions_of_the_uf_are_filled(session) -> None:
    attraction(session, "ta:1")
    attraction(session, "ta:2", mar=False)  # in Rio only
    attraction(session, "ta:3", uf="ZY")  # another UF
    keys = [
        t.canonical_key for t in web.pending_attractions(session, UF, refresh_days=365, limit=50)
    ]
    assert keys == ["ta:1"]


def test_a_run_stores_the_search_and_one_claim_per_column(session) -> None:
    attraction(session)
    llm, search = LLM(), Search()
    r = run(session, llm, search)
    assert (r.attractions, r.empty, r.failed) == (1, 0, 0)
    assert llm.calls[0]["schema"] is web.WRITE_SCHEMA and llm.calls[0]["model"] == MODEL
    prompt = llm.calls[0]["messages"][0]["content"]
    assert GOV in prompt and "viagensecaminhos" not in prompt and "google.com" not in prompt

    doc = session.get(EditorialDocument, web.doc_url("ta:1"))
    assert doc.source == "web_parallel" and doc.uf == UF and doc.published_time == TODAY
    version = session.get(EditorialDocumentVersion, doc.current_version_id)
    assert json.loads(version.html)["results"] == search.results  # the whole answer, kept

    c = claims(session)
    assert c["how_to_get_there"].status == "matched"
    assert c["how_to_get_there"].match_method == "direct" and c["how_to_get_there"].evidence == GOV
    assert c["tips"].status == "matched" and "8h" not in c["tips"].value  # hours stripped (D3)
    assert c["local_infrastructure"].status == "held_web"
    assert "enchimento" in c["local_infrastructure"].evidence
    assert c["curiosities"].status == "held_web" and "sem fonte" in c["curiosities"].evidence
    assert c["accessibility"].status == "held_web"
    assert (r.written, r.held) == (2, 3)


def test_the_resolver_writes_the_web_values_and_ignores_the_blog(session) -> None:
    rio = attraction(session)
    blog = EditorialDocument(
        url="https://www.viagensecaminhos.com/p/", source="viagensecaminhos", uf=UF,
        content_hash="b" * 64, body_text="x", published_time=date(2026, 11, 30), kml_points=[],
    )  # fmt: skip
    session.add(blog)
    session.flush()
    session.add(
        EditorialClaim(
            id=uuid.uuid4(),
            document_url=blog.url,
            source="viagensecaminhos",
            entity_kind="atrativo",
            entity_name="Pedra do Frade",
            field="curiosities",
            value="Do blog.",
            stability="estavel",
            status="matched",
            canonical_key="ta:1",
            match_method="kml",
        )  # fmt: skip
    )
    session.flush()
    run(session, LLM(), Search())

    r = resolve_uf(session, UF, today=TODAY, sources=["web_parallel"])
    session.refresh(rio)
    editorial = rio.normalized["editorial"]
    assert set(editorial) == {"how_to_get_there", "tips"}  # held columns and the blog: out
    assert r.attractions_written == 1
    direct = claims(session)["how_to_get_there"]
    assert (direct.status, direct.canonical_key) == ("matched", "ta:1")  # not re-matched


def test_a_new_run_supersedes_the_previous_claims_and_keeps_every_version(session) -> None:
    attraction(session)
    run(session, LLM(), Search())
    first = claims(session)["how_to_get_there"]
    again = web.Target("ta:1", UF, "Pedra do Frade", "Pancas")
    run(session, LLM(), Search(), targets=[again])
    session.expire_all()
    assert session.get(EditorialClaim, first.id).status == "superseded"
    assert session.get(EditorialClaim, first.id).superseded_from_status == "matched"
    assert claims(session)["how_to_get_there"].id != first.id
    versions = session.scalar(
        select(func.count())
        .select_from(EditorialDocumentVersion)
        .where(EditorialDocumentVersion.document_url == web.doc_url("ta:1"))
    )
    assert versions == 2


def test_a_recent_fill_is_not_picked_again_until_the_refresh(session) -> None:
    attraction(session)
    run(session, LLM(), Search())
    assert web.pending_attractions(session, UF, refresh_days=365, limit=50) == []
    later = datetime.now(UTC) + timedelta(days=400)
    assert [t.canonical_key for t in web.pending_attractions(
        session, UF, refresh_days=365, limit=50, now=later
    )] == ["ta:1"]  # fmt: skip


def test_an_empty_search_stores_the_search_but_pays_no_generation(session) -> None:
    attraction(session)
    llm = LLM()
    r = run(session, llm, Search(results=[{"url": "https://www.viagensecaminhos.com/x/"}]))
    assert llm.calls == [] and (r.attractions, r.empty) == (1, 1)
    assert session.get(EditorialDocument, web.doc_url("ta:1")) is not None
    assert claims(session) == {}


def test_a_failing_attraction_is_skipped_and_retried_next_run(session) -> None:
    attraction(session)
    r = run(session, LLM(error=RuntimeError("503")), Search())
    assert (r.attractions, r.failed) == (0, 1)
    assert session.get(EditorialDocument, web.doc_url("ta:1")) is None
    assert len(web.pending_attractions(session, UF, refresh_days=365, limit=50)) == 1


def test_the_budget_wall_stops_the_run(session) -> None:
    attraction(session)
    with pytest.raises(CostGuardError):
        run(session, LLM(error=CostGuardError("budget")), Search())


ROUTE = "pela BR-101 e pela Rodovia do Sol ES-060 passando por Itaoca e Itaipava"  # 13 words


def test_a_shared_route_is_not_a_copy_in_how_to_get_there_only() -> None:
    value = f"O acesso é feito {ROUTE}, com placa na entrada da praia."
    assert web.drop_reason("how_to_get_there", value, [GOV], ROUTE) is None
    assert web.drop_reason("curiosities", value, [GOV], ROUTE) == "cópia do trecho"
    long_copy = " ".join(f"palavra{i}" for i in range(20))
    assert web.drop_reason("how_to_get_there", long_copy, [GOV], long_copy) == "cópia do trecho"


def test_refilter_releases_a_held_column_the_current_filters_pass(session) -> None:
    attraction(session)
    route = {"url": GOV, "title": "Turismo", "excerpts": [ROUTE]}
    run(session, LLM(), Search(results=[route]))
    held = claims(session)["how_to_get_there"]
    held.status, held.evidence = "held_web", f"descartado: cópia do trecho | {GOV}"
    session.flush()

    assert web.refilter(session, UF) == 1
    session.expire_all()
    assert session.get(EditorialClaim, held.id).status == "superseded"
    now = claims(session)["how_to_get_there"]
    assert (now.status, now.evidence, now.value) == ("matched", GOV, held.value)
    assert claims(session)["curiosities"].status == "held_web"  # still sem fonte
    assert web.refilter(session, UF) == 0
