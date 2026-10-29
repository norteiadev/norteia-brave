"""Fake Wikimedia client for offline tests (structural WikimediaClientProtocol).

Pre-configured items/files; ``download`` returns ``b"img:" + title`` so two titles never
share a sha1 unless a test says so (``same_bytes``). ``fail`` makes every call raise.
Records calls for assertions.
"""

from __future__ import annotations

from typing import Any

from brave.clients.wikimedia import CommonsFile, WdItem


class FakeWikimediaClient:
    def __init__(
        self,
        *,
        around: list[WdItem] | None = None,
        by_name: list[WdItem] | None = None,
        files: dict[str, CommonsFile] | None = None,
        same_bytes: dict[str, bytes] | None = None,
        fail: Exception | None = None,
        **lanes: Any,
    ) -> None:
        self.around = around or []
        self.by_name = by_name or []
        self.files = files or {}
        self.same_bytes = same_bytes or {}
        self.fail = fail
        self.lanes = lanes  # depicts={qid: [...]}, category={cat: [...]}, geo=[...], municipio={ibge: item}, top={qid: [...]}
        self.calls: list[tuple[str, Any]] = []

    def _call(self, name: str, arg: Any) -> None:
        self.calls.append((name, arg))
        if self.fail is not None:
            raise self.fail

    async def items_around(self, lat: float, lon: float, km: float) -> list[WdItem]:
        self._call("items_around", (lat, lon, km))
        return self.around

    async def items_by_name(self, name: str, lat: float, lon: float, km: float) -> list[WdItem]:
        self._call("items_by_name", name)
        return self.by_name

    async def image_info(self, titles: list[str]) -> dict[str, CommonsFile]:
        self._call("image_info", list(titles))
        return {t: self.files[t] for t in titles if t in self.files}

    async def download(self, f: CommonsFile) -> tuple[bytes, str]:
        self._call("download", f.title)
        return self.same_bytes.get(f.title, b"img:" + f.title.encode()), "image/jpeg"

    async def depicts(self, qid: str) -> list[str]:
        self._call("depicts", qid)
        return self.lanes.get("depicts", {}).get(qid, [])

    async def category_files(self, category: str) -> list[str]:
        self._call("category_files", category)
        return self.lanes.get("category", {}).get(category, [])

    async def geosearch(self, lat: float, lon: float, radius_m: int) -> list[str]:
        self._call("geosearch", (lat, lon, radius_m))
        return list(self.lanes.get("geo", []))

    async def municipio(self, ibge: str) -> WdItem | None:
        self._call("municipio", ibge)
        return self.lanes.get("municipio", {}).get(ibge)

    async def items_in_municipio(self, qid: str) -> list[WdItem]:
        self._call("items_in_municipio", qid)
        return self.lanes.get("top", {}).get(qid, [])

    def count(self, name: str) -> int:
        return sum(1 for n, _ in self.calls if n == name)


def photo(title: str, w: int = 4000, h: int = 3000, lic: str = "cc-by-sa-4.0") -> CommonsFile:
    return CommonsFile(
        title=title,
        url=f"https://upload.wikimedia.org/wikipedia/commons/a/ab/{title[5:]}?utm_source=x",
        page=f"https://commons.wikimedia.org/wiki/{title}",
        width=w, height=h, mime="image/jpeg",
        license_code=lic, license="CC BY-SA 4.0", license_url="https://creativecommons.org/licenses/by-sa/4.0",
        artist="Fulano",
    )
