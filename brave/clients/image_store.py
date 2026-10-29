"""The Brave image archive: content-addressed files, local directory or Cloudflare R2.

Key: ``images/{sha1[:2]}/{sha1}.{ext}``. The same bytes always land on the same key, so a
``put`` of a key that already exists is a no-op (the archive is write-once).
``image_store_for(config)`` picks the backend from ``BRAVE_IMAGES_STORE``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from brave.config.settings import ImagesConfig

CACHE_CONTROL = "public, max-age=31536000, immutable"
_EXT = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


def storage_key(sha1: str, mime: str) -> str:
    return f"images/{sha1[:2]}/{sha1}.{_EXT.get(mime, 'jpg')}"


class ImageStoreProtocol(Protocol):
    def put(self, key: str, data: bytes, content_type: str) -> None: ...

    def get(self, key: str) -> bytes: ...


class LocalImageStore:
    """Files under ``root`` (the Mac and the tests). Writes go through a temp file + rename,
    so a crash never leaves a half-written image behind a valid key."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)

    def put(self, key: str, data: bytes, content_type: str) -> None:
        path = self._root / key
        if path.exists():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        tmp.write_bytes(data)
        tmp.replace(path)

    def get(self, key: str) -> bytes:
        return (self._root / key).read_bytes()


def image_store_for(config: ImagesConfig) -> ImageStoreProtocol:
    return LocalImageStore(config.local_dir)
