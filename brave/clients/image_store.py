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


class R2ImageStore:
    """Cloudflare R2 through the S3 API (boto3). ``PutObject(IfNoneMatch="*")`` makes the
    write conditional; R2's 412 means the key already holds these bytes."""

    def __init__(self, *, endpoint: str, bucket: str, access_key_id: str, secret_access_key: str) -> None:
        import boto3  # noqa: PLC0415
        from botocore.config import Config  # noqa: PLC0415

        self._bucket = bucket
        self._s3 = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name="auto",
            # R2 rejects the CRC checksums newer botocore sends by default.
            config=Config(
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
                retries={"max_attempts": 5, "mode": "standard"},
            ),
        )

    def put(self, key: str, data: bytes, content_type: str) -> None:
        from botocore.exceptions import ClientError  # noqa: PLC0415

        try:
            self._s3.put_object(
                Bucket=self._bucket, Key=key, Body=data, ContentType=content_type,
                CacheControl=CACHE_CONTROL, IfNoneMatch="*",
            )
        except ClientError as exc:
            if exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 412:
                raise

    def get(self, key: str) -> bytes:
        return self._s3.get_object(Bucket=self._bucket, Key=key)["Body"].read()


def image_store_for(config: ImagesConfig) -> ImageStoreProtocol:
    """The archive backend ``BRAVE_IMAGES_STORE`` names (local | r2)."""
    if config.store == "local":
        return LocalImageStore(config.local_dir)
    missing = [
        f"BRAVE_IMAGES_{name.upper()}"
        for name in ("r2_endpoint", "r2_bucket", "r2_access_key_id", "r2_secret_access_key")
        if not getattr(config, name)
    ]
    if missing:
        raise ValueError(f"store=r2 needs {', '.join(missing)}")
    return R2ImageStore(
        endpoint=config.r2_endpoint, bucket=config.r2_bucket,
        access_key_id=config.r2_access_key_id, secret_access_key=config.r2_secret_access_key,
    )
