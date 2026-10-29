"""The photo outbox: NorteiaApiClient.push_media (respx) and brave.push_pending_media
against the test DB (task session = SAVEPOINT of an outer transaction, rolled back).

Rules: an atrativo's photos go only once its Mar row is on norteia-api (only validated
records reach the platform); a destino's only once an atrativo of its município is, since
that push is what creates the destination — and a 404 still leaves it pending, no error.
2xx stamps pushed_at; a permanent 4xx stamps push_error.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.orm import Session

from brave.clients.factory import Clients
from brave.clients.image_store import LocalImageStore
from brave.clients.norteia_api import NorteiaApiClient
from brave.config.settings import AppConfig
from brave.core.models import EntityImage, Image, MarRecord, NascenteRecord, RioRecord
from brave.tasks import pipeline

pytestmark = pytest.mark.integration

BASE = "https://api.norteia.example"
MEDIA = f"{BASE}/api/internal/territorial/media"


async def test_push_media_is_multipart_with_bearer() -> None:
    client = NorteiaApiClient(base_url=BASE, service_token="tok")
    with respx.mock:
        route = respx.post(MEDIA).mock(return_value=httpx.Response(201, json={"created": True}))
        assert await client.push_media({"role": "cover", "sha1": "a" * 40}, b"img", "a.jpg", "image/jpeg")
    req = route.calls.last.request
    assert req.headers["Authorization"] == "Bearer tok"
    assert req.headers["Content-Type"].startswith("multipart/form-data")
    body = req.content
    assert b'name="role"' in body and b"cover" in body and b'filename="a.jpg"' in body and b"img" in body


@pytest.fixture
def h(db_engine, monkeypatch, tmp_path):
    conn = db_engine.connect()
    outer = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")
    # The task (and the CLI) close their session: each gets its own, on the same connection.
    monkeypatch.setattr(
        pipeline, "_get_session",
        lambda: (Session(bind=conn, join_transaction_mode="create_savepoint"), db_engine),
    )
    monkeypatch.setattr(pipeline, "_norteia_api_down", lambda: False)
    monkeypatch.setenv("RUN_REAL_EXTERNALS", "true")
    cfg = AppConfig().model_copy(update={"run_real_externals": True})
    cfg.images.local_dir = str(tmp_path)
    monkeypatch.setattr(pipeline, "_load_config", lambda s: cfg)
    api = NorteiaApiClient(base_url=BASE, service_token="tok")
    monkeypatch.setattr(pipeline, "clients_for", lambda c, **k: Clients(c, norteia_api=api))

    class H:
        pass

    x = H()
    x.session, x.store = session, LocalImageStore(tmp_path)
    yield x
    session.close()
    outer.rollback()
    conn.close()


@pytest.fixture
def beat(monkeypatch):
    """The beat-health calls (task, exc) the outbox makes — what the Painel's beat_errors shows."""
    calls: list = []
    monkeypatch.setattr(pipeline, "_beat_health", lambda task, exc=None: calls.append((task, exc)))
    return calls


def _rio(h, key: str, entity_type: str, municipio: str = "9999999") -> RioRecord:
    n = NascenteRecord(id=uuid.uuid4(), source="t", source_ref=key, entity_type=entity_type,
                       uf="ZZ", payload={}, content_hash=f"h:{key}", version=1)
    h.session.add(n)
    h.session.flush()
    rio = RioRecord(nascente_id=n.id, canonical_key=key, entity_type=entity_type, uf="ZZ",
                    municipio_id=municipio, routing="mar", normalized={})
    h.session.add(rio)
    h.session.flush()
    return rio


def _mar(h, rio: RioRecord, pushed: bool = True) -> None:
    h.session.add(MarRecord(
        rio_id=rio.id, entity_type=rio.entity_type, source_ref=rio.canonical_key, canonical={},
        provenance={}, reliability_score=90, score_version="v1",
        pushed_at=datetime.now(UTC) if pushed else None,
    ))
    h.session.flush()


def _photo(h, key: str, entity_type: str, role: str = "cover") -> EntityImage:
    data = f"img:{key}:{role}".encode()
    sha1 = hashlib.sha1(data).hexdigest()
    h.store.put(f"images/{sha1[:2]}/{sha1}.jpg", data, "image/jpeg")
    img = Image(sha1=sha1, storage_key=f"images/{sha1[:2]}/{sha1}.jpg", commons_title=f"File:{sha1}.jpg",
                source_url="https://commons.wikimedia.org/wiki/File:x.jpg", file_url="https://u/x.jpg",
                width=1920, height=1280, mime="image/jpeg", bytes=len(data), author="Fulano",
                license="CC BY-SA 4.0", license_url="https://cc/by-sa", credit_line="Fulano · CC BY-SA 4.0",
                commons_status="ok")
    h.session.add(img)
    h.session.flush()
    row = EntityImage(canonical_key=key, entity_type=entity_type, image_id=img.id, role=role,
                      rank=0 if role == "cover" else 1, lane="p18", wikidata_qid="Q1")
    h.session.add(row)
    h.session.commit()
    return row


def test_atrativo_photos_wait_for_its_mar_push(h) -> None:
    pushed = _rio(h, "ta:pushed", "attraction")
    _mar(h, pushed)
    unpushed = _rio(h, "ta:unpushed", "attraction")
    _mar(h, unpushed, pushed=False)
    _rio(h, "ta:no-mar", "attraction")
    rows = [_photo(h, k, "attraction") for k in ("ta:pushed", "ta:unpushed", "ta:no-mar")]

    with respx.mock:
        route = respx.post(MEDIA).mock(return_value=httpx.Response(201, json={"created": True}))
        assert pipeline.push_pending_media.run() == 1

    assert route.call_count == 1
    body = route.calls.last.request.content
    assert b"ta:pushed" in body and b"attraction" in body and b"Fulano" in body
    h.session.expire_all()
    assert [r.pushed_at is not None for r in rows] == [True, False, False]


def test_destino_waits_for_an_atrativo_of_its_municipio_and_404_stays_pending(h) -> None:
    _rio(h, "ibge:ZZ:9999999", "destination")
    _rio(h, "ibge:ZZ:8888888", "destination", municipio="8888888")
    atrativo = _rio(h, "ta:1", "attraction")
    _mar(h, atrativo)
    ready = _photo(h, "ibge:ZZ:9999999", "destination")
    lonely = _photo(h, "ibge:ZZ:8888888", "destination")

    with respx.mock:
        route = respx.post(MEDIA).mock(return_value=httpx.Response(404, json={"message": "not found"}))
        assert pipeline.push_pending_media.run() == 0

    assert route.call_count == 1  # the lonely destino was never sent
    h.session.expire_all()
    assert (ready.pushed_at, ready.push_error, lonely.pushed_at) == (None, None, None)


def test_permanent_4xx_is_recorded_and_repush_clears_it(h) -> None:
    rio = _rio(h, "ta:1", "attraction")
    _mar(h, rio)
    row = _photo(h, "ta:1", "attraction")

    with respx.mock:
        respx.post(MEDIA).mock(return_value=httpx.Response(422, json={"message": "bad license"}))
        pipeline.push_pending_media.run()
    h.session.expire_all()
    assert row.push_error.startswith("422") and row.pushed_at is None

    with respx.mock:
        respx.post(MEDIA).mock(return_value=httpx.Response(200, json={"created": False}))
        assert pipeline.push_pending_media.run() == 0  # errored rows are not retried

    from brave.cli import _run_images_repush

    assert _run_images_repush() == 0
    h.session.expire_all()
    assert row.push_error is None
    with respx.mock:
        respx.post(MEDIA).mock(return_value=httpx.Response(200, json={"created": False}))
        assert pipeline.push_pending_media.run() == 1
    h.session.expire_all()
    assert row.pushed_at is not None


@pytest.mark.parametrize("status", [401, 403, 408, 429])
def test_auth_or_transient_4xx_ends_the_tick_and_leaves_rows_pending(h, beat, status) -> None:
    for key in ("ta:1", "ta:2"):
        _mar(h, _rio(h, key, "attraction"))
    rows = [_photo(h, k, "attraction") for k in ("ta:1", "ta:2")]

    with respx.mock:
        route = respx.post(MEDIA).mock(return_value=httpx.Response(status, json={"message": "nope"}))
        if status in (401, 403):  # the token, not the photo: the task fails (beat_errors)
            with pytest.raises(httpx.HTTPStatusError):
                pipeline.push_pending_media.run()
        else:
            assert pipeline.push_pending_media.run() == 0

    assert route.call_count == 1  # the tick stopped at the first one
    h.session.expire_all()
    assert [(r.pushed_at, r.push_error) for r in rows] == [(None, None), (None, None)]
    failed = isinstance(beat[-1][1], httpx.HTTPStatusError)
    assert beat[-1][0] == "brave.push_pending_media" and failed == (status in (401, 403))


def test_no_photo_in_the_archive_fails_the_tick_and_marks_nothing(h, beat) -> None:
    for key in ("ta:1", "ta:2"):
        _mar(h, _rio(h, key, "attraction"))
    rows = [_photo(h, k, "attraction") for k in ("ta:1", "ta:2")]
    for r in rows:  # e.g. BRAVE_IMAGES_STORE switched to a bucket the archive was never copied to
        (h.store._root / h.session.get(Image, r.image_id).storage_key).unlink()

    with respx.mock:
        route = respx.post(MEDIA).mock(return_value=httpx.Response(201, json={"created": True}))
        with pytest.raises(FileNotFoundError, match="wrong store or bucket"):
            pipeline.push_pending_media.run()

    assert route.call_count == 0
    h.session.expire_all()
    assert [(r.pushed_at, r.push_error) for r in rows] == [(None, None), (None, None)]
    assert isinstance(beat[-1][1], FileNotFoundError)


def test_lost_photos_alone_in_a_tick_are_recorded_when_the_archive_holds_a_pushed_one(h) -> None:
    for key in ("ta:1", "ta:2"):
        _mar(h, _rio(h, key, "attraction"))
    sent, gone = (_photo(h, k, "attraction") for k in ("ta:1", "ta:2"))
    sent.pushed_at = datetime.now(UTC)  # an earlier tick: its file proves this is the right archive
    h.session.commit()
    (h.store._root / h.session.get(Image, gone.image_id).storage_key).unlink()

    with respx.mock:
        route = respx.post(MEDIA).mock(return_value=httpx.Response(201, json={"created": True}))
        assert pipeline.push_pending_media.run() == 0

    assert route.call_count == 0
    h.session.expire_all()
    assert gone.push_error.startswith("archive:") and gone.pushed_at is None


def test_a_store_error_other_than_a_missing_key_fails_the_tick_and_marks_nothing(h, beat, monkeypatch) -> None:
    _mar(h, _rio(h, "ta:1", "attraction"))
    row = _photo(h, "ta:1", "attraction")

    def denied(self, key: str) -> bytes:  # e.g. R2 AccessDenied: the archive, not the photo
        raise PermissionError(key)

    monkeypatch.setattr(LocalImageStore, "get", denied)
    with respx.mock:
        route = respx.post(MEDIA).mock(return_value=httpx.Response(201, json={"created": True}))
        with pytest.raises(PermissionError):
            pipeline.push_pending_media.run()

    assert route.call_count == 0
    h.session.expire_all()
    assert (row.pushed_at, row.push_error) == (None, None)
    assert isinstance(beat[-1][1], PermissionError)


def test_a_missing_archive_file_is_recorded_and_the_rest_still_go(h) -> None:
    for key in ("ta:1", "ta:2"):
        _mar(h, _rio(h, key, "attraction"))
    gone, ok = (_photo(h, k, "attraction") for k in ("ta:1", "ta:2"))
    (h.store._root / h.session.get(Image, gone.image_id).storage_key).unlink()

    with respx.mock:
        route = respx.post(MEDIA).mock(return_value=httpx.Response(201, json={"created": True}))
        assert pipeline.push_pending_media.run() == 1

    assert route.call_count == 1
    h.session.expire_all()
    assert gone.push_error.startswith("archive:") and gone.pushed_at is None
    assert ok.pushed_at is not None


def test_removed_or_already_pushed_rows_are_skipped(h) -> None:
    rio = _rio(h, "ta:1", "attraction")
    _mar(h, rio)
    row = _photo(h, "ta:1", "attraction")
    row.removed_at = datetime.now(UTC)
    h.session.commit()
    with respx.mock:
        route = respx.post(MEDIA).mock(return_value=httpx.Response(201, json={}))
        pipeline.push_pending_media.run()
    assert route.call_count == 0
    assert list(h.session.scalars(select(EntityImage.pushed_at))) == [None]
