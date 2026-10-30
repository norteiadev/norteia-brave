"""WikimediaClient (WDQS + Commons API + upload server), 100% offline via respx.

What these pin down, each paid for in the image POCs:
  - Commons answers HTTP 200 with {"error": {"code": "cirrussearch-too-busy-error"}}: that is
    retried, never read as an empty lane.
  - 429 / 5xx are retried.
  - The original URL's ?utm_ query is stripped before the /thumb/ URL is built, and only the
    configured standard width is asked for (other widths are a 400 on the upload server).
  - The descriptive User-Agent goes on every request.
"""

from __future__ import annotations

import fakeredis
import httpx
import pytest
import respx

from brave.config.settings import ImagesConfig

API = "https://commons.wikimedia.org/w/api.php"
WDQS = "https://query.wikidata.org/sparql"
UPLOAD = "https://upload.wikimedia.org/wikipedia/commons"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("RUN_REAL_EXTERNALS", "true")
    from brave.clients.wikimedia import WikimediaClient

    return WikimediaClient(
        config=ImagesConfig(min_interval_s=0, backoff_s=0), redis=fakeredis.FakeRedis()
    )


def _imageinfo_page(title: str, width: int, url: str | None = None, **meta: str) -> dict:
    ext = {k: {"value": v} for k, v in meta.items()}
    return {
        "title": title,
        "imageinfo": [{
            "url": url or f"{UPLOAD}/a/ab/{title.removeprefix('File:').replace(' ', '_')}",
            "descriptionurl": f"https://commons.wikimedia.org/wiki/{title.replace(' ', '_')}",
            "width": width,
            "height": width * 2 // 3,
            "mime": "image/jpeg",
            "extmetadata": ext,
        }],
    }


def test_guard_raises() -> None:
    from brave.clients.wikimedia import WikimediaClient

    with pytest.raises(RuntimeError, match="run_real_externals=False"):
        WikimediaClient(config=ImagesConfig(), redis=fakeredis.FakeRedis())


async def test_busy_error_in_200_is_retried(client) -> None:
    page = _imageinfo_page(
        "File:Praia.jpg", 4000, License="cc-by-sa-4.0", LicenseShortName="CC BY-SA 4.0",
        Artist='<a href="x">Fulano</a>',
    )
    with respx.mock:
        route = respx.get(API).mock(side_effect=[
            httpx.Response(200, json={"error": {"code": "cirrussearch-too-busy-error"}}),
            httpx.Response(429),
            httpx.Response(200, json={"query": {"pages": [page]}}),
        ])
        info = await client.image_info(["File:Praia.jpg"])

    assert route.call_count == 3
    f = info["File:Praia.jpg"]
    assert (f.width, f.license_code, f.license, f.artist) == (4000, "cc-by-sa-4.0", "CC BY-SA 4.0", "Fulano")
    assert route.calls.last.request.headers["User-Agent"].startswith("norteia-brave/")


async def test_busy_error_that_never_clears_raises(client) -> None:
    from brave.clients.wikimedia import WikimediaError

    with respx.mock:
        respx.get(API).mock(
            return_value=httpx.Response(200, json={"error": {"code": "maxlag"}})
        )
        with pytest.raises(WikimediaError):
            await client.image_info(["File:Praia.jpg"])


async def test_unknown_api_error_is_not_retried(client) -> None:
    from brave.clients.wikimedia import WikimediaError

    with respx.mock:
        route = respx.get(API).mock(
            return_value=httpx.Response(200, json={"error": {"code": "badvalue"}})
        )
        with pytest.raises(WikimediaError):
            await client.image_info(["File:Praia.jpg"])
    assert route.call_count == 1


async def test_download_strips_utm_and_uses_standard_width(client) -> None:
    from brave.clients.wikimedia import CommonsFile

    big = CommonsFile(
        title="File:Praia do Saco.jpg",
        url=f"{UPLOAD}/a/ab/Praia_do_Saco.jpg?utm_source=commons.wikimedia.org&utm_campaign=x",
        page="", width=4000, height=3000, mime="image/jpeg",
    )
    small = CommonsFile(
        title="File:Mini.jpg", url=f"{UPLOAD}/c/cd/Mini.jpg?utm_source=x",
        page="", width=800, height=600, mime="image/jpeg",
    )
    with respx.mock:
        thumb = respx.get(f"{UPLOAD}/thumb/a/ab/Praia_do_Saco.jpg/1920px-Praia_do_Saco.jpg").mock(
            return_value=httpx.Response(200, content=b"big", headers={"content-type": "image/jpeg"})
        )
        orig = respx.get(f"{UPLOAD}/c/cd/Mini.jpg").mock(
            return_value=httpx.Response(200, content=b"small", headers={"content-type": "image/jpeg"})
        )
        assert await client.download(big) == (b"big", "image/jpeg")
        assert await client.download(small) == (b"small", "image/jpeg")
    assert thumb.calls.last.request.url.query == b""
    assert orig.calls.last.request.url.query == b""


async def test_items_around_parses_sparql(client) -> None:
    bindings = [
        {
            "item": {"value": "http://www.wikidata.org/entity/Q1"},
            "label": {"value": "Praia do Saco"},
            "p18": {"value": "http://commons.wikimedia.org/wiki/Special:FilePath/Praia%20do%20Saco.jpg"},
            "p373": {"value": "Praia do Saco"},
            "dist": {"value": "1.25"},
        },
        {
            "item": {"value": "http://www.wikidata.org/entity/Q1"},
            "label": {"value": "Saco beach"},
            "dist": {"value": "1.25"},
        },
    ]
    with respx.mock:
        route = respx.post(WDQS).mock(side_effect=[
            httpx.Response(503),
            httpx.Response(200, json={"results": {"bindings": bindings}}),
        ])
        items = await client.items_around(-11.2, -37.3, 5)

    assert route.call_count == 2
    [it] = items
    assert (it.qid, it.labels, it.p18, it.p373, it.dist_km) == (
        "Q1", frozenset({"Praia do Saco", "Saco beach"}), ("File:Praia do Saco.jpg",),
        "Praia do Saco", 1.25,
    )


async def test_gallery_lanes_query_the_file_namespace(client) -> None:
    with respx.mock:
        route = respx.get(API).mock(side_effect=[
            httpx.Response(200, json={"query": {"search": [{"title": "File:D1.jpg"}]}}),
            httpx.Response(200, json={"query": {"categorymembers": [{"title": "File:C1.jpg"}]}}),
            httpx.Response(200, json={"query": {"geosearch": [{"title": "File:G1.jpg", "dist": 12.0}]}}),
        ])
        assert await client.depicts("Q1") == ["File:D1.jpg"]
        assert await client.category_files("Praia do Saco") == ["File:C1.jpg"]
        assert await client.geosearch(-11.2, -37.3, 500) == ["File:G1.jpg"]

    depicts, category, geo = (c.request.url.params for c in route.calls)
    assert (depicts["srsearch"], depicts["srnamespace"]) == ("haswbstatement:P180=Q1", "6")
    assert (category["cmtitle"], category["cmnamespace"]) == ("Category:Praia do Saco", "6")
    assert (geo["gsradius"], geo["gsnamespace"]) == ("500", "6")


async def test_municipio_and_its_top_items(client) -> None:
    fp = "http://commons.wikimedia.org/wiki/Special:FilePath/"
    with respx.mock:
        route = respx.post(WDQS).mock(side_effect=[
            httpx.Response(200, json={"results": {"bindings": [
                {"item": {"value": "http://www.wikidata.org/entity/Q186"}, "p18": {"value": fp + "Aracaju.jpg"}},
            ]}}),
            httpx.Response(200, json={"results": {"bindings": [
                {"item": {"value": "http://www.wikidata.org/entity/Q7"}, "p18": {"value": fp + "Orla.jpg"}, "sl": {"value": "9"}},
                {"item": {"value": "http://www.wikidata.org/entity/Q8"}, "p18": {"value": fp + "Ponte.jpg"}, "sl": {"value": "4"}},
            ]}}),
            httpx.Response(200, json={"results": {"bindings": []}}),
        ])
        muni = await client.municipio("2800308")
        top = await client.items_in_municipio("Q186")
        assert await client.municipio("0000000") is None

    assert (muni.qid, muni.p18) == ("Q186", ("File:Aracaju.jpg",))
    assert [(i.qid, i.p18, i.sitelinks) for i in top] == [
        ("Q7", ("File:Orla.jpg",), 9), ("Q8", ("File:Ponte.jpg",), 4),
    ]
    assert '"2800308"' in route.calls[0].request.content.decode().replace("%22", '"')


async def test_series_per_service_overlap_across_services(client) -> None:
    """Two calls to one service never overlap; WDQS, the Commons API and the upload server
    do (Robot policy: Action API concurrency 1)."""
    import asyncio

    from brave.clients.wikimedia import CommonsFile

    live: dict[str, int] = {}
    peak: dict[str, int] = {}
    overlapped = asyncio.Event()

    def busy(host: str, resp: httpx.Response):
        async def side_effect(request: httpx.Request) -> httpx.Response:
            live[host] = live.get(host, 0) + 1
            peak[host] = max(peak.get(host, 0), live[host])
            if sum(live.values()) == 3:
                overlapped.set()
            await asyncio.sleep(0.02)
            live[host] -= 1
            return resp
        return side_effect

    f = CommonsFile(title="File:M.jpg", url=f"{UPLOAD}/c/cd/M.jpg", page="", width=10, height=10, mime="image/jpeg")
    with respx.mock:
        respx.get(API).mock(side_effect=busy("api", httpx.Response(200, json={"query": {"search": []}})))
        respx.post(WDQS).mock(side_effect=busy("wdqs", httpx.Response(200, json={"results": {"bindings": []}})))
        respx.get(f"{UPLOAD}/c/cd/M.jpg").mock(side_effect=busy("upload", httpx.Response(200, content=b"x")))
        await asyncio.gather(
            *(client.depicts(f"Q{i}") for i in range(3)),
            *(client.items_around(0, 0, 1) for _ in range(3)),
            *(client.download(f) for _ in range(3)),
        )

    assert peak == {"api": 1, "wdqs": 1, "upload": 1}
    assert overlapped.is_set()


async def test_slow_api_request_holds_the_api_lane_5s(monkeypatch: pytest.MonkeyPatch) -> None:
    """Robot policy, Action API: a request that took >1 s to serve → wait 5 s before the
    next one — here and, through the Redis slot, in every other process. Other services
    keep their own gap."""
    import asyncio

    from brave.clients import wikimedia
    from brave.clients.wikimedia import WIKIMEDIA_SLOT_KEY, WikimediaClient

    monkeypatch.setenv("RUN_REAL_EXTERNALS", "true")
    clock = [1000.0]
    slept: list[float] = []

    class Clock:
        @staticmethod
        def monotonic() -> float:
            return clock[0]

    async def sleep(s: float) -> None:
        slept.append(round(s, 3))
        clock[0] += s

    monkeypatch.setattr(wikimedia, "time", Clock)
    monkeypatch.setattr(asyncio, "sleep", sleep)
    redis = fakeredis.FakeRedis()
    client = WikimediaClient(config=ImagesConfig(min_interval_s=1.0, backoff_s=0), redis=redis)
    api_ttls: list[int] = []

    def slow(request: httpx.Request) -> httpx.Response:
        clock[0] += 1.5  # the server took 1.5 s
        return httpx.Response(200, json={"query": {"search": []}})

    with respx.mock:
        respx.get(API).mock(side_effect=slow)
        respx.post(WDQS).mock(return_value=httpx.Response(200, json={"results": {"bindings": []}}))
        await client.depicts("Q1")
        api_ttls.append(redis.pttl(f"{WIKIMEDIA_SLOT_KEY}:api"))
        redis.delete(f"{WIKIMEDIA_SLOT_KEY}:api")  # the fake clock does not age Redis keys
        await client.items_around(0, 0, 1)  # another service: not held
        await client.depicts("Q2")

    assert api_ttls[0] > 1000  # the cross-process slot now holds for 5 s, not 1 s
    assert slept == [5.0]  # only the second API call waited, the full 5 s
