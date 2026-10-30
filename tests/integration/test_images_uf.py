"""brave.images_uf against the test DB: selection, order, chaining and the run lifecycle.

The task's session joins an outer transaction as a SAVEPOINT, so its commits are rolled
back with the test. Wikimedia is FakeWikimediaClient; the archive is tmp_path.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import fakeredis
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from brave.clients.factory import Clients
from brave.clients.wikimedia import WdItem
from brave.config.settings import AppConfig
from brave.core import engine as ce
from brave.core.models import EntityImage, ImageLookup, NascenteRecord, RioRecord
from brave.tasks import pipeline
from tests.fakes.fake_wikimedia import FakeWikimediaClient, photo

pytestmark = pytest.mark.integration

UF = "ZZ"  # synthetic: no real row shares it


@pytest.fixture
def h(db_engine, monkeypatch, tmp_path):
    conn = db_engine.connect()
    outer = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")
    monkeypatch.setattr(pipeline, "_get_session", lambda: (session, db_engine))

    fake_redis = fakeredis.FakeStrictRedis()
    monkeypatch.setattr("redis.from_url", lambda *_a, **_k: fake_redis)
    run_id = ce.start(fake_redis, None, action="images", depth="imagens", source="imagens",
                      ufs=[UF], lane="both")

    cfg = AppConfig().model_copy(update={"run_real_externals": True, "images_enrichment_enabled": True})
    cfg.images.local_dir = str(tmp_path)
    monkeypatch.setattr(pipeline, "_load_config", lambda s: cfg)

    class H:
        pass

    x = H()
    x.session, x.redis, x.run_id, x.cfg, x.tmp = session, fake_redis, run_id, cfg, tmp_path
    x.fake = FakeWikimediaClient(
        around=[WdItem("Q1", frozenset({"Praia Um"}), ("File:Um.jpg",))],
        files={t: photo(t) for t in ("File:Um.jpg", "File:Cidade.jpg")},
        municipio={"9999999": WdItem("Q9", frozenset(), ("File:Cidade.jpg",))},
    )
    monkeypatch.setattr(pipeline, "clients_for", lambda c, **k: Clients(c, wikimedia=x.fake))
    x.done = MagicMock()
    monkeypatch.setattr(pipeline, "_producer_done", x.done)
    x.chain = MagicMock()
    monkeypatch.setattr(pipeline, "_hand_over", x.chain)
    yield x
    session.close()
    outer.rollback()
    conn.close()


def _rio(h, key: str, entity_type: str, **norm) -> RioRecord:
    n = NascenteRecord(id=uuid.uuid4(), source="t", source_ref=key, entity_type=entity_type, uf=UF,
                       payload={}, content_hash=f"h:{key}", version=1)
    h.session.add(n)
    h.session.flush()
    rio = RioRecord(nascente_id=n.id, canonical_key=key, entity_type=entity_type, uf=UF,
                    municipio_id="9999999", routing=norm.pop("routing", "mar"), normalized=norm)
    h.session.add(rio)
    h.session.commit()  # a savepoint of the outer transaction: the task's rollback keeps it
    return rio


def _keys(h) -> set[str]:
    return set(h.session.scalars(select(EntityImage.canonical_key)))


def test_atrativos_then_destinos(h) -> None:
    _rio(h, "ta:1", "attraction", name="Praia Um", lat=-1.0, lon=-2.0, google_enriched=True)
    _rio(h, "ta:skip-descarte", "attraction", name="Praia Um", lat=-1.0, lon=-2.0,
         google_enriched=True, routing="descarte")
    _rio(h, "ta:skip-no-places", "attraction", name="Praia Um", lat=-1.0, lon=-2.0)
    _rio(h, "ta:skip-no-geo", "attraction", name="Praia Um", google_enriched=True)
    _rio(h, f"ibge:{UF}:9999999", "destination", name="Cidade")

    pipeline.images_uf.run(UF, run_id=h.run_id)
    assert _keys(h) == {"ta:1"}
    assert h.session.get(ImageLookup, "ta:skip-descarte") is None
    # The atrativo phase is done: it hands over to the destino phase.
    assert h.chain.call_args.kwargs["phase"] == "destination"
    h.done.assert_not_called()

    pipeline.images_uf.run(UF, phase="destination", run_id=h.run_id)
    assert _keys(h) == {"ta:1", f"ibge:{UF}:9999999"}
    h.done.assert_called_once_with(h.run_id, failed=False)


def test_final_lookups_are_not_selected_again(h) -> None:
    _rio(h, "ta:1", "attraction", name="Praia Um", lat=-1.0, lon=-2.0, google_enriched=True)
    pipeline.images_uf.run(UF, run_id=h.run_id)
    calls = len(h.fake.calls)

    pipeline.images_uf.run(UF, run_id=h.run_id)
    assert len(h.fake.calls) == calls


def test_max_n_caps_the_atrativos(h) -> None:
    for i in range(3):
        _rio(h, f"ta:{i}", "attraction", name="Praia Um", lat=-1.0, lon=-2.0, google_enriched=True)

    pipeline.images_uf.run(UF, 2, run_id=h.run_id)
    assert len(list(h.session.scalars(select(ImageLookup.canonical_key).where(
        ImageLookup.canonical_key.like("ta:%"))))) == 2
    assert h.chain.call_args.kwargs["phase"] == "destination"


def test_lane_off_does_nothing(h) -> None:
    h.cfg.images_enrichment_enabled = False
    _rio(h, "ta:1", "attraction", name="Praia Um", lat=-1.0, lon=-2.0, google_enriched=True)

    pipeline.images_uf.run(UF, run_id=h.run_id)
    assert _keys(h) == set()
    h.done.assert_called_once_with(h.run_id, failed=True)
