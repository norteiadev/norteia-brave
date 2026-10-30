"""_images_chunk with entities really in flight together, each on its own session.

Unlike the other image tests these sessions are separate connections, so the rows are
committed for real (unique keys per test run) and deleted afterwards. Wikimedia is a fake
whose downloads sleep, so the entities interleave.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from brave.clients.image_store import LocalImageStore
from brave.clients.wikimedia import WdItem
from brave.config.settings import ImagesConfig
from brave.core.models import EntityImage, Image, ImageLookup, NascenteRecord, RioRecord
from brave.domains.images.agent import ImageLaneAgent
from brave.tasks.pipeline import _images_chunk
from tests.fakes.fake_wikimedia import FakeWikimediaClient, photo

pytestmark = pytest.mark.integration


class PerEntity(FakeWikimediaClient):
    """Entity i (at lat -(i+1)) matches its own item, whose P18 is ``titles[i]``."""

    def __init__(self, titles: list[str], *, boom: str | None = None, delay: dict[str, float] | None = None):
        super().__init__(files={t: photo(t) for t in titles})
        self.titles, self.boom, self.delay = titles, boom, delay or {}

    async def items_around(self, lat: float, lon: float, km: float) -> list[WdItem]:
        i = round(-lat) - 1
        return [WdItem(f"Q{i + 1}", frozenset({f"Praia {i}"}), (self.titles[i],))]

    async def download(self, f):
        await asyncio.sleep(self.delay.get(f.title, 0.01))
        if f.title == self.boom:
            raise RuntimeError("boom")  # not an external error: the entity's session rolls back
        return await super().download(f)


@pytest.fixture
def lane(db_engine, tmp_path):
    run = uuid.uuid4().hex[:8]
    prefix = f"tc:{run}:"
    factory = sessionmaker(bind=db_engine)

    class L:
        pass

    x = L()
    x.prefix, x.run, x.factory = prefix, run, factory
    x.title = lambda name: f"File:TC {run} {name}.jpg"

    def seed(n: int) -> list:
        with factory() as s:
            rios = []
            for i in range(n):
                nas = NascenteRecord(id=uuid.uuid4(), source="t", source_ref=f"{prefix}{i}",
                                     entity_type="attraction", uf="ZY", payload={},
                                     content_hash=f"h:{prefix}{i}", version=1)
                s.add(nas)
                s.flush()
                rio = RioRecord(nascente_id=nas.id, canonical_key=f"{prefix}{i}", entity_type="attraction",
                                uf="ZY", routing="mar",
                                normalized={"name": f"Praia {i}", "lat": -(i + 1.0), "lon": -2.0})
                s.add(rio)
                rios.append(rio)
            s.commit()
            return [r.id for r in rios]

    def chunk(fake, ids, *, stop=lambda: False, concurrency=3) -> int:
        def agent(s: Session) -> ImageLaneAgent:
            return ImageLaneAgent(s, fake, LocalImageStore(tmp_path), ImagesConfig())

        return asyncio.run(_images_chunk(factory, agent, contextlib.nullcontext(), ids, stop, "ZY", concurrency))

    def linked() -> dict[str, list]:
        with factory() as s:
            rows = s.scalars(select(EntityImage).where(EntityImage.canonical_key.like(f"{prefix}%")))
            out: dict[str, list] = {}
            for r in rows:
                out.setdefault(r.canonical_key, []).append(r.image_id)
            return out

    def lookups() -> set[str]:
        with factory() as s:
            return set(s.scalars(select(ImageLookup.canonical_key).where(
                ImageLookup.canonical_key.like(f"{prefix}%"))))

    x.seed, x.chunk, x.linked, x.lookups = seed, chunk, linked, lookups
    yield x
    with factory() as s:
        s.execute(delete(EntityImage).where(EntityImage.canonical_key.like(f"{prefix}%")))
        s.execute(delete(ImageLookup).where(ImageLookup.canonical_key.like(f"{prefix}%")))
        s.execute(delete(Image).where(Image.commons_title.like(f"File:TC {run} %")))
        s.execute(delete(RioRecord).where(RioRecord.canonical_key.like(f"{prefix}%")))
        s.execute(delete(NascenteRecord).where(NascenteRecord.source_ref.like(f"{prefix}%")))
        s.commit()


def test_same_file_reached_by_concurrent_entities_is_archived_once(lane) -> None:
    ids = lane.seed(5)
    fake = PerEntity([lane.title("shared")] * 5)

    assert lane.chunk(fake, ids) == 5

    links = lane.linked()
    assert set(links) == {f"{lane.prefix}{i}" for i in range(5)}
    assert len({i for v in links.values() for i in v}) == 1
    assert fake.count("download") > 1  # they did overlap: the later ones reused the row


def test_a_failing_entity_does_not_touch_the_others(lane) -> None:
    ids = lane.seed(5)
    # Entity 2 fails after the others have started (its download is the slowest).
    fake = PerEntity([lane.title(str(i)) for i in range(5)], boom=lane.title("2"),
                     delay={lane.title("2"): 0.05})

    assert lane.chunk(fake, ids) == 5  # a failed entity counts as finished

    ok = {f"{lane.prefix}{i}" for i in (0, 1, 3, 4)}
    assert set(lane.linked()) == ok
    assert lane.lookups() == ok  # entity 2 rolled back alone: no lookup, retried next run


def test_cursor_stops_at_the_finished_prefix(lane) -> None:
    ids = lane.seed(5)
    # Entity 0 is the slowest: 1 and 2 finish before it.
    fake = PerEntity([lane.title(str(i)) for i in range(5)], delay={lane.title("0"): 0.1})
    calls = 0

    def stop() -> bool:  # halt after three entities were taken
        nonlocal calls
        calls += 1
        return calls > 3

    n = lane.chunk(fake, ids, stop=stop)

    assert n == 3
    assert lane.lookups() == {f"{lane.prefix}{i}" for i in range(n)}
