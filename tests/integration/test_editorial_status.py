"""``editorial-status``: the lane's funnel, from the DB (UF "ZZ" apart from real data)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from brave.config.settings import AppConfig
from brave.core.models import (
    EditorialClaim,
    EditorialDestination,
    EditorialDocument,
    LLMGeneration,
    MarRecord,
    NascenteRecord,
    RioRecord,
)
from brave.domains.editorial.status import format_funnel, funnel

pytestmark = pytest.mark.integration

NOW = datetime.now(UTC)
EST = AppConfig().editorial_est_usd_per_post  # reservation ceiling per post (settings)
EXPECTED = AppConfig().editorial_expected_usd_per_post  # expected cost per post (settings)


def _funnel(session, uf):
    return funnel(session, uf, est_usd_per_post=EST, expected_usd_per_post=EXPECTED)


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


def _rio(s, key, editorial, routing="mar", processed_at=None):
    n = NascenteRecord(id=uuid.uuid4(), source="t", source_ref=key, entity_type="attraction", uf="ZZ",
                       payload={}, content_hash=key, version=1)  # fmt: skip
    s.add(n)
    s.flush()
    r = RioRecord(nascente_id=n.id, canonical_key=key, entity_type="attraction", uf="ZZ", routing=routing,
                  processed_at=processed_at,
                  normalized={"name": key, **({"editorial": editorial} if editorial else {})})  # fmt: skip
    s.add(r)
    s.flush()
    return r


def _mar(s, rio, pushed_at=None, push_error=None):
    s.add(MarRecord(rio_id=rio.id, entity_type="attraction", source_ref=rio.canonical_key, canonical={},
                    provenance={}, reliability_score=90, score_version="v1", pushed_at=pushed_at,
                    push_error=push_error))  # fmt: skip
    s.flush()


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
    f = _funnel(session, "ZZ")
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
    # one post still to extract: what it should cost, and what submit reserves for it
    assert f["pending_expected_usd"] == pytest.approx(EXPECTED)
    assert f["pending_ceiling_usd"] == pytest.approx(EST)
    assert EXPECTED < EST


def test_without_a_uf_it_spans_every_uf(session) -> None:
    _seed(session)
    f = _funnel(session, None)
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
            "attraction_push": {
                "with_editorial": 70,
                "in_mar": 60,
                "pushed": 50,
                "pending": 9,
                "error": 1,
                "not_in_mar": 10,
            },  # fmt: skip
            "llm_cost_usd": 0.81,
            "pending_expected_usd": 0.078,
            "pending_ceiling_usd": 0.18,
            "expected_usd_per_post": 0.026,
            "est_usd_per_post": 0.06,
        }
    )
    for needle in ("ES", "17", "74", "190", "held_volatile", "0.81", "pending"):
        assert needle in text
    # the cost gate of Passo 7: the expected cost AND the reservation ceiling, both labelled
    assert "expected $0.08 ($0.026/post)" in text
    assert "ceiling/reserva $0.18 ($0.06/post)" in text
    assert "pushed with editorial 50" in text and "push_error 1" in text and "not in Mar 10" in text


def test_cli_status_prints_the_funnel(session, monkeypatch, capsys) -> None:
    from brave import cli
    from brave.tasks import pipeline

    _seed(session)
    monkeypatch.setattr(pipeline, "_get_session", lambda: (session, None))
    session.close = lambda: None  # the CLI closes its session; the fixture owns it
    assert cli._run_editorial_status("ZZ") == 0
    out = capsys.readouterr().out
    assert "ZZ" in out and "matched" in out


def test_cli_retry_frees_the_errored_posts_and_says_how_to_resubmit(
    session, monkeypatch, capsys
) -> None:
    import sys

    from brave import cli
    from brave.tasks import pipeline

    bad = _doc(session, "bad", uf="AC", error="invalid_request_error: x")  # the CLI takes real UFs
    monkeypatch.setattr(pipeline, "_get_session", lambda: (session, None))
    session.close = lambda: None
    monkeypatch.setattr(sys, "argv", ["brave", "editorial-retry", "ac"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0
    session.refresh(bad)
    assert bad.error is None
    out = capsys.readouterr().out
    assert "1" in out and "--submit-only" in out


def test_cli_retry_with_several_ufs_frees_each_of_them(session, monkeypatch, capsys) -> None:
    import sys

    from brave import cli
    from brave.tasks import pipeline

    ac = _doc(session, "bad-ac", uf="AC", error="x")
    al = _doc(session, "bad-al", uf="AL", error="x")
    am = _doc(session, "bad-am", uf="AM", error="x")  # not listed: untouched
    monkeypatch.setattr(pipeline, "_get_session", lambda: (session, None))
    session.close = lambda: None
    monkeypatch.setattr(sys, "argv", ["brave", "editorial-retry", "ac", "al"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0
    for d in (ac, al, am):
        session.refresh(d)
    assert (ac.error, al.error, am.error) == (None, None, "x")
    assert "AC AL" in capsys.readouterr().out


def test_funnel_shows_the_attraction_push_of_the_editorial_write(session) -> None:
    written = NOW - timedelta(hours=1)
    pushed = _rio(session, "p1", {"tips": "a"}, processed_at=written)
    _mar(session, pushed, pushed_at=NOW)  # pushed AFTER the editorial write
    stale = _rio(session, "p2", {"tips": "a"}, processed_at=written)
    _mar(session, stale, pushed_at=written - timedelta(days=1))  # last push predates it
    never = _rio(session, "p3", {"tips": "a"}, processed_at=written)
    _mar(session, never)  # never accepted
    rejected = _rio(session, "p4", {"tips": "a"}, processed_at=written)
    _mar(session, rejected, push_error="422: bad")
    _rio(session, "p5", {"tips": "a"}, routing="dlq", processed_at=written)  # waits for Promoção
    _rio(session, "p6", None, processed_at=written)  # no editorial: not counted
    f = _funnel(session, "ZZ")
    assert f["attraction_push"] == {
        "with_editorial": 5,
        "in_mar": 4,
        "pushed": 1,
        "pending": 2,
        "error": 1,
        "not_in_mar": 1,
    }
