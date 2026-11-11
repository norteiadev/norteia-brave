"""Editorial Celery tasks (called directly, no broker) over the test DB in a SAVEPOINT, with a
fake fetcher / Anthropic batch client / norteia-api and fakeredis. Nothing leaves the machine."""

from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import fakeredis
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from brave.clients.factory import Clients
from brave.config.settings import AppConfig
from brave.core.models import EditorialClaim, EditorialDestination, EditorialDocument, Municipio
from brave.domains.editorial.crawler import category_url
from brave.domains.editorial.extract_batch import TOOL_NAME
from brave.tasks import pipeline

pytestmark = pytest.mark.integration

POST = "https://www.viagensecaminhos.com/p-es/"


class Fetcher:
    def __init__(self):
        self.calls: list[str] = []

    async def get_text(self, url):
        self.calls.append(url)
        if url == category_url("ES", 1):
            return f'<h3 class="entry-title"><a href="{POST}">x</a></h3>'
        if url == POST:
            return '<div class="td-post-content"><p>Texto do post.</p></div>'
        return None


class Batches:
    def __init__(self):
        self.created: list[list[dict]] = []
        self.entries: dict[str, list] = {}

    def create(self, *, requests):
        self.created.append(requests)
        bid = f"msgbatch_{len(self.created)}"
        self.entries[bid] = [
            SimpleNamespace(
                custom_id=r["custom_id"],
                result=SimpleNamespace(
                    type="succeeded",
                    message=SimpleNamespace(
                        stop_reason="tool_use",
                        usage=SimpleNamespace(input_tokens=1000, output_tokens=100),
                        content=[
                            SimpleNamespace(
                                type="tool_use",
                                name=TOOL_NAME,
                                input={
                                    "entities": [
                                        {
                                            "kind": "atrativo",
                                            "name": "Pedra",
                                            "municipio": "Pancas",
                                            "facts": [
                                                {
                                                    "field": "curiosities",
                                                    "value": "Fato.",
                                                    "evidence": "e",
                                                }
                                            ],
                                        }
                                    ]
                                },
                            )
                        ],
                    ),
                ),
            )
            for r in requests
        ]
        return SimpleNamespace(id=bid)

    def retrieve(self, bid):
        return SimpleNamespace(processing_status="ended")

    def results(self, bid):
        return iter(self.entries[bid])


class Api:
    def __init__(self):
        self.calls = []

    async def push(self, entity_type, payload):
        self.calls.append((entity_type, payload))
        return True


@pytest.fixture
def h(db_engine, monkeypatch):
    conn = db_engine.connect()
    outer = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")
    monkeypatch.setattr(
        pipeline,
        "_get_session",
        lambda: (Session(bind=conn, join_transaction_mode="create_savepoint"), db_engine),
    )
    monkeypatch.setattr(pipeline, "_norteia_api_down", lambda: False)
    health: list = []
    monkeypatch.setattr(pipeline, "_beat_health", lambda task, exc=None: health.append((task, exc)))
    fake_redis = fakeredis.FakeRedis()
    monkeypatch.setattr("redis.from_url", lambda *a, **k: fake_redis)
    x = SimpleNamespace(
        session=session,
        health=health,
        fetcher=Fetcher(),
        batches=Batches(),
        api=Api(),
        redis=fake_redis,
        cfg=None,
    )

    def configure(**over):
        x.cfg = AppConfig().model_copy(update={"run_real_externals": True, **over})
        client = SimpleNamespace(messages=SimpleNamespace(batches=x.batches))
        monkeypatch.setattr(pipeline, "_load_config", lambda s: x.cfg)
        monkeypatch.setattr(
            pipeline,
            "clients_for",
            lambda c, **k: Clients(c, editorial=x.fetcher, batch=client, norteia_api=x.api),
        )

    x.configure = configure
    yield x
    session.close()
    outer.rollback()
    conn.close()


def _doc(h, slug, uf="ES", **kw):
    d = EditorialDocument(
        url=f"https://www.viagensecaminhos.com/{slug}/",
        source="viagensecaminhos",
        uf=uf,
        content_hash=slug.ljust(64, "0"),
        body_text="Texto.",
        kml_points=[],
        published_time=date(2020, 1, 1),
        **kw,
    )
    h.session.add(d)
    h.session.flush()
    return d


# -- crawl --------------------------------------------------------------------------------


def test_crawl_is_blocked_while_the_lane_is_off(h, monkeypatch) -> None:
    h.configure(editorial_enabled=False)
    submit = MagicMock()
    monkeypatch.setattr(pipeline.editorial_submit_batches, "delay", submit)
    pipeline.editorial_crawl_uf.run("ES")
    assert h.fetcher.calls == [] and submit.call_count == 0
    assert h.session.get(EditorialDocument, POST) is None


def test_crawl_stores_the_posts_then_dispatches_the_submit(h, monkeypatch) -> None:
    h.configure(editorial_enabled=True)
    submit = MagicMock()
    monkeypatch.setattr(pipeline.editorial_submit_batches, "delay", submit)
    pipeline.editorial_crawl_uf.run("ES")
    assert h.session.get(EditorialDocument, POST) is not None
    submit.assert_called_once_with("ES")


def test_crawl_only_and_a_crawl_with_nothing_new_do_not_submit(h, monkeypatch) -> None:
    h.configure(editorial_enabled=True)
    submit = MagicMock()
    monkeypatch.setattr(pipeline.editorial_submit_batches, "delay", submit)
    pipeline.editorial_crawl_uf.run("ES", submit=False)
    pipeline.editorial_crawl_uf.run("ES", submit=True)  # same content now: nothing fetched
    assert submit.call_count == 0


def test_crawl_chains_the_next_uf_even_when_this_one_fails(h, monkeypatch) -> None:
    h.configure(editorial_enabled=True)
    nxt = MagicMock()
    monkeypatch.setattr(pipeline.editorial_crawl_uf, "delay", nxt)
    h.fetcher.get_text = MagicMock(side_effect=RuntimeError("site down"))
    pipeline.editorial_crawl_uf.run("ES", submit=False, rest=["MG", "SP"])
    nxt.assert_called_once_with("MG", submit=False, rest=["SP"])


# -- submit -------------------------------------------------------------------------------


def test_submit_is_blocked_by_either_master_switch(h) -> None:
    _doc(h, "a-es")
    h.configure(editorial_enabled=False)
    pipeline.editorial_submit_batches.run()
    h.configure(editorial_enabled=True, run_real_externals=False)
    pipeline.editorial_submit_batches.run()
    assert h.batches.created == []


def test_submit_sends_every_pending_post_in_capped_batches(h) -> None:
    for i in range(3):
        _doc(h, f"p{i}-es")
    h.configure(editorial_enabled=True, editorial_batch_size=2)
    pipeline.editorial_submit_batches.run("ES")
    assert [len(b) for b in h.batches.created] == [2, 1]
    assert h.batches.created[0][0]["params"]["model"] == h.cfg.editorial_model_slug


# -- collect + resolve --------------------------------------------------------------------


def test_collect_applies_ended_batches_and_dispatches_a_resolve_per_uf(h, monkeypatch) -> None:
    d = _doc(h, "a-es")
    h.configure(editorial_enabled=True)
    pipeline.editorial_submit_batches.run("ES")
    resolve = MagicMock()
    monkeypatch.setattr(pipeline.editorial_resolve, "delay", resolve)
    pipeline.editorial_collect_batches.run()
    h.session.expire(d)
    assert d.extracted_at is not None
    assert h.session.scalars(select(EditorialClaim.field)).all() == ["curiosities"]
    resolve.assert_called_once_with("ES")
    assert ("brave.editorial_collect_batches", None) in h.health


def test_collect_with_externals_off_only_reaps(h, monkeypatch) -> None:
    h.configure(editorial_enabled=True, run_real_externals=False)
    reap = MagicMock()
    monkeypatch.setattr("brave.domains.editorial.extract_batch.reap_stale_claims", reap)
    pipeline.editorial_collect_batches.run()
    reap.assert_called_once()
    assert reap.call_args.args[1] is None  # no client: placeholders only


def test_resolve_task_runs_without_the_lane_switch(h) -> None:
    h.configure(editorial_enabled=False)
    d = _doc(h, "pancas-es", extracted_at=datetime.now(UTC))
    h.session.add(Municipio(ibge_code="9990009", nome="Pancas", uf="ES", lat=0.0, lng=0.0))
    h.session.add(
        EditorialClaim(
            document_url=d.url,
            source=d.source,
            entity_kind="destino",
            entity_name="Pancas",
            municipio_text="Pancas",
            field="tips",
            value="Chegue cedo.",
            stability="estavel",
            status="unmatched",
            published_time=d.published_time,
        )
    )
    h.session.flush()
    pipeline.editorial_resolve.run("ES")
    assert h.session.get(EditorialDestination, "ibge:ES:9990009").fields == {"tips": "Chegue cedo."}


# -- destination outbox -------------------------------------------------------------------


def _dest(h):
    row = EditorialDestination(
        source_ref="ibge:ES:9990009",
        uf="ES",
        municipio_ibge="9990009",
        tourist_name="Pancas",
        fields={"tips": "Chegue cedo."},
    )
    h.session.add(row)
    h.session.flush()
    return row


def test_destination_push_is_held_while_editorial_push_is_off(h) -> None:
    row = _dest(h)
    h.configure(editorial_push_enabled=False)
    assert pipeline.push_editorial_destinations.run() == 0
    h.session.expire(row)
    assert h.api.calls == [] and row.pushed_at is None
    assert ("brave.push_editorial_destinations", None) in h.health  # a hold is not a failure


def test_destination_push_sends_when_enabled(h) -> None:
    row = _dest(h)
    h.configure(editorial_push_enabled=True)
    assert pipeline.push_editorial_destinations.run() == 1
    h.session.expire(row)
    assert row.pushed_at is not None and h.api.calls[0][0] == "destination"


def test_destination_push_does_nothing_offline_or_with_the_api_down(h, monkeypatch) -> None:
    _dest(h)
    h.configure(editorial_push_enabled=True, run_real_externals=False)
    assert pipeline.push_editorial_destinations.run() == 0
    h.configure(editorial_push_enabled=True)
    monkeypatch.setattr(pipeline, "_norteia_api_down", lambda: True)
    assert pipeline.push_editorial_destinations.run() == 0
    assert h.api.calls == []


def test_a_crawl_that_read_a_missing_map_again_re_resolves_the_uf(h, monkeypatch) -> None:
    from brave.domains.editorial.crawler import content_hash, kml_url

    body = '<div class="td-post-content"><p>Texto do post.</p></div>'
    page = body + '<iframe src="https://www.google.com/maps/d/embed?mid=MapX"></iframe>'
    kml = "<kml><Placemark><name>Pedra</name><Point><coordinates>-40.1,-20.2,0</coordinates></Point></Placemark></kml>"
    pages = {
        category_url("ES", 1): f'<h3 class="entry-title"><a href="{POST}">x</a></h3>',
        POST: page,
        kml_url("MapX"): kml,
    }

    async def get_text(url):
        return pages.get(url)

    h.fetcher.get_text = get_text
    d = _doc(h, "p-es", kml_error="MapX: timeout", extracted_at=datetime.now(UTC))
    d.content_hash = content_hash("Texto do post.")  # the text did not change
    h.session.flush()
    h.configure(editorial_enabled=True)
    resolve, submit = MagicMock(), MagicMock()
    monkeypatch.setattr(pipeline.editorial_resolve, "delay", resolve)
    monkeypatch.setattr(pipeline.editorial_submit_batches, "delay", submit)
    pipeline.editorial_crawl_uf.run("ES")
    resolve.assert_called_once_with("ES")
    assert submit.call_count == 0  # same text: nothing to pay for
