"""``editorial-status``: the lane's funnel, from the DB (UF "ZZ" apart from real data)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy.orm import Session

from brave.core.models import (
    EditorialClaim,
    EditorialDestination,
    EditorialDocument,
    LLMGeneration,
    NascenteRecord,
    RioRecord,
)
from brave.domains.editorial.status import format_funnel, funnel

pytestmark = pytest.mark.integration

NOW = datetime.now(UTC)


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


def _doc(s, slug, uf="ZZ", **kw):
    d = EditorialDocument(url=f"https://x.example/{slug}/", source="viagensecaminhos", uf=uf,
                          content_hash=slug.ljust(64, "0"), body_text="t", kml_points=[], **kw)  # fmt: skip
    s.add(d)
    s.flush()
    return d


def _claim(s, d, field, status, key=None, kind="atrativo"):
    s.add(EditorialClaim(id=uuid.uuid4(), document_url=d.url, source=d.source, entity_kind=kind, entity_name="X",
                         field=field, value="v", stability="estavel", status=status, canonical_key=key,
                         published_time=date(2020, 1, 1)))  # fmt: skip


def _rio(s, key, editorial):
    n = NascenteRecord(id=uuid.uuid4(), source="t", source_ref=key, entity_type="attraction", uf="ZZ",
                       payload={}, content_hash=key, version=1)  # fmt: skip
    s.add(n)
    s.flush()
    s.add(RioRecord(nascente_id=n.id, canonical_key=key, entity_type="attraction", uf="ZZ", routing="mar",
                    normalized={"name": key, **({"editorial": editorial} if editorial else {})}))  # fmt: skip


def _seed(s):
    done = _doc(s, "done", extracted_at=NOW)
    _doc(s, "todo")
    _doc(s, "err", error="boom")
    _doc(s, "busy", batch_id="msgbatch_1")
    _doc(s, "other-uf", uf="ZY", extracted_at=NOW)
    _claim(s, done, "curiosities", "matched", "k1")
    _claim(s, done, "tips", "matched", "k1")
    _claim(s, done, "curiosities", "matched", "k2")
    _claim(s, done, "curiosities", "unmatched")
    _claim(s, done, "tips", "ambiguous")
    _claim(s, done, "price", "held_volatile")
    _claim(s, done, "tips", "matched", "9990001", kind="destino")
    _rio(s, "k1", {"curiosities": "a", "tips": "b"})
    _rio(s, "k2", {"curiosities": "c"})
    _rio(s, "k3", None)
    for ref, kw in (
        ("p", {}),
        ("ok", {"pushed_at": NOW, "push_hash": "h"}),
        ("bad", {"push_error": "422"}),
    ):
        s.add(EditorialDestination(source_ref=f"ibge:ZZ:{ref}", uf="ZZ", municipio_ibge="9990001",
                                   tourist_name="N", fields={"tips": "x"}, **kw))  # fmt: skip
    s.add(LLMGeneration(id=uuid.uuid4(), lane="editorial_extract_batch", model_slug="m", prompt_tokens=1,
                        completion_tokens=1, usd_cost=0.25))  # fmt: skip
    s.flush()


def test_funnel_counts_each_stage_of_the_uf(session) -> None:
    _seed(session)
    f = funnel(session, "ZZ")
    assert f["documents"] == {
        "fetched": 4,
        "extracted": 1,
        "error": 1,
        "pending": 1,
        "in_flight": 1,
    }
    assert f["claims"] == {"matched": 4, "unmatched": 1, "ambiguous": 1, "held_volatile": 1}
    assert f["attractions_matched"] == 2  # k1, k2: distinct records
    assert f["editorial"] == {"attractions": 2, "fields": 3}
    assert f["destinations"] == {"pending": 1, "pushed": 1, "error": 1}
    assert f["llm_cost_usd"] >= 0.25
    assert f["pending_estimate_usd"] == pytest.approx(0.06)  # one post still to extract


def test_without_a_uf_it_spans_every_uf(session) -> None:
    _seed(session)
    f = funnel(session, None)
    assert f["documents"]["fetched"] >= 5 and f["documents"]["extracted"] >= 2


def test_format_is_a_readable_funnel() -> None:
    text = format_funnel(
        {
            "uf": "ES",
            "documents": {"fetched": 17, "extracted": 15, "error": 1, "pending": 1, "in_flight": 0},
            "claims": {"matched": 100, "unmatched": 40, "ambiguous": 3, "held_volatile": 20},
            "attractions_matched": 74,
            "editorial": {"attractions": 70, "fields": 190},
            "destinations": {"pending": 5, "pushed": 10, "error": 0},
            "llm_cost_usd": 0.81,
            "pending_estimate_usd": 0.06,
        }
    )
    for needle in ("ES", "17", "74", "190", "held_volatile", "0.81", "pending"):
        assert needle in text


def test_cli_status_prints_the_funnel(session, monkeypatch, capsys) -> None:
    from brave import cli
    from brave.tasks import pipeline

    _seed(session)
    monkeypatch.setattr(pipeline, "_get_session", lambda: (session, None))
    session.close = lambda: None  # the CLI closes its session; the fixture owns it
    assert cli._run_editorial_status("ZZ") == 0
    out = capsys.readouterr().out
    assert "ZZ" in out and "matched" in out
