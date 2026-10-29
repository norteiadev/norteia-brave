"""ImageLaneAgent against the test DB, a fake Wikimedia client and LocalImageStore(tmp_path).

No network: the agent sees only FakeWikimediaClient. The Rio rows are transient (never
added): the lane reads them and writes only its own tables.
"""

from __future__ import annotations

import hashlib

import pytest
from sqlalchemy import select

from brave.clients.image_store import LocalImageStore
from brave.clients.wikimedia import WdItem, WikimediaError
from brave.config.settings import ImagesConfig
from brave.core.models import EntityImage, Image, ImageLookup, RioRecord
from brave.domains.images.agent import ImageLaneAgent
from tests.fakes.fake_wikimedia import FakeWikimediaClient, photo

pytestmark = pytest.mark.integration

SACO = WdItem("Q1", frozenset({"Praia do Saco"}), ("File:Saco.jpg",), dist_km=1.2)


def _atrativo(key: str = "tripadvisor:attraction:1", name: str = "Praia do Saco") -> RioRecord:
    return RioRecord(
        canonical_key=key, entity_type="attraction", uf="SE",
        normalized={"name": name, "lat": -11.4, "lon": -37.3, "municipio_id": "2807105"},
    )


def _agent(db_session, fake, tmp_path) -> ImageLaneAgent:
    return ImageLaneAgent(db_session, fake, LocalImageStore(tmp_path), ImagesConfig())


def _rows(db_session, key: str) -> list[EntityImage]:
    return list(db_session.scalars(select(EntityImage).where(EntityImage.canonical_key == key)))


async def test_matched_attraction_gets_its_p18_cover(db_session, tmp_path) -> None:
    fake = FakeWikimediaClient(around=[SACO], files={"File:Saco.jpg": photo("File:Saco.jpg")})

    result = await _agent(db_session, fake, tmp_path).run(_atrativo())

    assert result is not None and result.outcome == "matched"
    [row] = _rows(db_session, "tripadvisor:attraction:1")
    assert (row.role, row.rank, row.lane, row.wikidata_qid, row.pushed_at) == ("cover", 0, "p18", "Q1", None)
    img = row.image
    assert img.sha1 == hashlib.sha1(b"img:File:Saco.jpg").hexdigest()
    assert (tmp_path / img.storage_key).read_bytes() == b"img:File:Saco.jpg"
    assert (img.width, img.height) == (1920, 1440)  # the 1920 px thumb of a 4000×3000 original
    assert img.file_url.endswith("/Saco.jpg")  # no ?utm_
    assert (img.author, img.license) == ("Fulano", "CC BY-SA 4.0")
    lookup = db_session.get(ImageLookup, "tripadvisor:attraction:1")
    assert (lookup.outcome, lookup.qid, lookup.via, lookup.dist_km) == ("matched", "Q1", "around5km", 1.2)


async def test_second_run_is_a_no_op(db_session, tmp_path) -> None:
    fake = FakeWikimediaClient(around=[SACO], files={"File:Saco.jpg": photo("File:Saco.jpg")})
    agent = _agent(db_session, fake, tmp_path)
    await agent.run(_atrativo())
    calls = len(fake.calls)

    assert await agent.run(_atrativo()) is None
    assert len(fake.calls) == calls
    assert len(_rows(db_session, "tripadvisor:attraction:1")) == 1


async def test_same_file_is_archived_once(db_session, tmp_path) -> None:
    fake = FakeWikimediaClient(around=[SACO], files={"File:Saco.jpg": photo("File:Saco.jpg")})
    agent = _agent(db_session, fake, tmp_path)
    await agent.run(_atrativo("tripadvisor:attraction:1"))
    await agent.run(_atrativo("tripadvisor:attraction:2"))

    assert fake.count("download") == 1
    a, b = _rows(db_session, "tripadvisor:attraction:1"), _rows(db_session, "tripadvisor:attraction:2")
    assert a[0].image_id == b[0].image_id


async def test_same_bytes_under_another_title_reuse_the_file(db_session, tmp_path) -> None:
    twin = WdItem("Q9", frozenset({"Praia do Saco"}), ("File:Saco copy.jpg",))
    files = {t: photo(t) for t in ("File:Saco.jpg", "File:Saco copy.jpg")}
    same = {t: b"same" for t in files}
    agent = _agent(db_session, FakeWikimediaClient(around=[SACO], files=files, same_bytes=same), tmp_path)
    await agent.run(_atrativo("tripadvisor:attraction:1"))
    agent = _agent(db_session, FakeWikimediaClient(around=[twin], files=files, same_bytes=same), tmp_path)
    await agent.run(_atrativo("tripadvisor:attraction:2"))

    assert len(list(db_session.scalars(select(Image).where(Image.sha1 == hashlib.sha1(b"same").hexdigest())))) == 1


async def test_attraction_without_qid_gets_no_photo(db_session, tmp_path) -> None:
    other = WdItem("Q3", frozenset({"Jolimont"}), ("File:Jolimont.jpg",), dist_km=0.2)
    fake = FakeWikimediaClient(around=[other], files={"File:Jolimont.jpg": photo("File:Jolimont.jpg")})

    result = await _agent(db_session, fake, tmp_path).run(_atrativo())

    assert result.outcome == "no_match"
    assert _rows(db_session, "tripadvisor:attraction:1") == []
    assert fake.count("download") == 0
    assert db_session.get(ImageLookup, "tripadvisor:attraction:1").outcome == "no_match"


async def test_name_fallback_within_30km(db_session, tmp_path) -> None:
    far = WdItem("Q1", frozenset({"Praia do Saco"}), ("File:Saco.jpg",), dist_km=12.0)
    fake = FakeWikimediaClient(by_name=[far], files={"File:Saco.jpg": photo("File:Saco.jpg")})

    await _agent(db_session, fake, tmp_path).run(_atrativo())

    lookup = db_session.get(ImageLookup, "tripadvisor:attraction:1")
    assert (lookup.via, lookup.dist_km) == ("name30km", 12.0)


async def test_external_error_is_not_a_final_marker(db_session, tmp_path) -> None:
    agent = _agent(db_session, FakeWikimediaClient(fail=WikimediaError("maxlag")), tmp_path)
    result = await agent.run(_atrativo())

    assert result.outcome == "error"
    assert db_session.get(ImageLookup, "tripadvisor:attraction:1").outcome == "error"

    fake = FakeWikimediaClient(around=[SACO], files={"File:Saco.jpg": photo("File:Saco.jpg")})
    assert (await _agent(db_session, fake, tmp_path).run(_atrativo())).outcome == "matched"


async def test_gallery_from_depicts_category_and_nearby(db_session, tmp_path) -> None:
    item = WdItem("Q1", frozenset({"Praia do Saco"}), ("File:Saco.jpg",), p373="Praia do Saco")
    titles = ["File:Saco.jpg", "File:D1.jpg", "File:C1.jpg", "File:G1.jpg"]
    fake = FakeWikimediaClient(
        around=[item], files={t: photo(t) for t in titles},
        depicts={"Q1": ["File:D1.jpg"]}, category={"Praia do Saco": ["File:C1.jpg"]},
        geo=["File:G1.jpg", "File:Saco.jpg"],
    )

    await _agent(db_session, fake, tmp_path).run(_atrativo())

    rows = sorted(_rows(db_session, "tripadvisor:attraction:1"), key=lambda r: r.rank)
    assert [(r.role, r.lane) for r in rows] == [
        ("cover", "p18"), ("gallery", "p180"), ("gallery", "p373"), ("gallery", "geo500"),
    ]


# -- destino ----------------------------------------------------------------------

ARACAJU = WdItem("Q186", frozenset(), ("File:Aracaju montagem.jpg",))


def _destino(code: str = "2800308") -> RioRecord:
    return RioRecord(
        canonical_key=f"ibge:SE:{code}", entity_type="destination", uf="SE", municipio_id=code,
        normalized={"name": "Aracaju", "municipio_id": code},
    )


def _seed_atrativo(db_session, key: str, reviews: int, code: str = "2800308") -> None:
    """A persisted atrativo of the city (the destino fallback reads Rio)."""
    import uuid

    from brave.core.models import NascenteRecord

    nascente = NascenteRecord(
        id=uuid.uuid4(), source="tripadvisor", source_ref=key, entity_type="attraction", uf="SE",
        payload={}, content_hash=f"hash:{key}", version=1,
    )
    db_session.add(nascente)
    db_session.flush()
    db_session.add(RioRecord(
        nascente_id=nascente.id, canonical_key=key, entity_type="attraction", uf="SE",
        municipio_id=code, routing="mar",
        normalized={"name": key, "lat": -10.9, "lon": -37.0, "review_count": reviews},
    ))
    db_session.flush()


async def test_destino_cover_is_the_municipio_p18(db_session, tmp_path) -> None:
    good = WdItem("Q186", frozenset(), ("File:Orla de Aracaju.jpg",))
    fake = FakeWikimediaClient(files={"File:Orla de Aracaju.jpg": photo("File:Orla de Aracaju.jpg")},
                               municipio={"2800308": good})

    result = await _agent(db_session, fake, tmp_path).run(_destino())

    assert result.outcome == "matched"
    [row] = _rows(db_session, "ibge:SE:2800308")
    assert (row.role, row.lane, row.entity_type, row.wikidata_qid) == ("cover", "p18", "destination", "Q186")
    lookup = db_session.get(ImageLookup, "ibge:SE:2800308")
    assert (lookup.via, lookup.qid, lookup.dist_km) == ("ibge", "Q186", None)


async def test_destino_falls_back_to_the_most_reviewed_atrativo_cover(db_session, tmp_path) -> None:
    files = {t: photo(t) for t in ("File:Aracaju montagem.jpg", "File:Popular.jpg", "File:Menos.jpg")}
    for key, reviews, title in (("ta:1", 10, "File:Menos.jpg"), ("ta:2", 900, "File:Popular.jpg")):
        _seed_atrativo(db_session, key, reviews)
        item = WdItem("Q" + key[-1], frozenset({key}), (title,))
        await _agent(db_session, FakeWikimediaClient(around=[item], files=files), tmp_path).run(
            _atrativo(key, name=key)
        )
    fake = FakeWikimediaClient(files=files, municipio={"2800308": ARACAJU})

    await _agent(db_session, fake, tmp_path).run(_destino())

    [row] = _rows(db_session, "ibge:SE:2800308")
    assert (row.lane, row.image.commons_title) == ("atrativo", "File:Popular.jpg")
    assert fake.count("download") == 0  # the atrativo's file is reused


async def test_destino_last_resort_is_the_top_item_of_the_municipio(db_session, tmp_path) -> None:
    files = {t: photo(t) for t in ("File:Aracaju montagem.jpg", "File:Orla.jpg")}
    fake = FakeWikimediaClient(
        files=files, municipio={"2800308": ARACAJU},
        top={"Q186": [WdItem("Q7", frozenset(), ("File:Orla.jpg",), sitelinks=9)]},
    )

    await _agent(db_session, fake, tmp_path).run(_destino())

    [row] = _rows(db_session, "ibge:SE:2800308")
    assert (row.lane, row.image.commons_title) == ("muni_top", "File:Orla.jpg")


async def test_destino_without_wikidata_item_is_no_match(db_session, tmp_path) -> None:
    result = await _agent(db_session, FakeWikimediaClient(), tmp_path).run(_destino())
    assert result.outcome == "no_match"


async def test_destino_without_a_cover_yet_is_retried_later(db_session, tmp_path) -> None:
    """No usable cover now (collage P18, no atrativo cover yet, no top item): not a final
    marker, so a later run finds the atrativo covers collected in between."""
    files = {t: photo(t) for t in ("File:Aracaju montagem.jpg", "File:Popular.jpg")}
    fake = FakeWikimediaClient(files=files, municipio={"2800308": ARACAJU})
    agent = _agent(db_session, fake, tmp_path)

    assert (await agent.run(_destino())).outcome == "no_photo"
    assert db_session.get(ImageLookup, "ibge:SE:2800308").outcome == "no_photo"

    _seed_atrativo(db_session, "ta:2", 900)
    item = WdItem("Q2", frozenset({"ta:2"}), ("File:Popular.jpg",))
    await _agent(db_session, FakeWikimediaClient(around=[item], files=files), tmp_path).run(
        _atrativo("ta:2", name="ta:2")
    )
    assert (await agent.run(_destino())).outcome == "matched"
    [row] = _rows(db_session, "ibge:SE:2800308")
    assert row.lane == "atrativo"
