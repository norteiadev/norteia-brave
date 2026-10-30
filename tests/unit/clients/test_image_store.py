"""Brave image archive backends. LocalImageStore on tmp_path; R2ImageStore through botocore's
Stubber (no network)."""

from __future__ import annotations

import pytest
from botocore.stub import Stubber

from brave.clients.image_store import (
    CACHE_CONTROL,
    LocalImageStore,
    R2ImageStore,
    image_store_for,
    storage_key,
)
from brave.config.settings import ImagesConfig

SHA = "ab" + "0" * 38


def test_storage_key_is_content_addressed() -> None:
    assert storage_key(SHA, "image/jpeg") == f"images/ab/{SHA}.jpg"
    assert storage_key(SHA, "image/png") == f"images/ab/{SHA}.png"


def test_local_store_is_write_once(tmp_path) -> None:
    store = LocalImageStore(tmp_path)
    store.put("images/ab/x.jpg", b"one", "image/jpeg")
    store.put("images/ab/x.jpg", b"two", "image/jpeg")
    assert store.get("images/ab/x.jpg") == b"one"


def test_local_delete_is_idempotent(tmp_path) -> None:
    store = LocalImageStore(tmp_path)
    store.put("images/ab/x.jpg", b"one", "image/jpeg")
    store.delete("images/ab/x.jpg")
    store.delete("images/ab/x.jpg")
    with pytest.raises(FileNotFoundError):
        store.get("images/ab/x.jpg")


def _r2() -> tuple[R2ImageStore, Stubber]:
    store = R2ImageStore(endpoint="https://acc.r2.cloudflarestorage.com", bucket="acervo",
                         access_key_id="k", secret_access_key="s")
    return store, Stubber(store._s3)


def test_r2_put_is_conditional_and_immutable() -> None:
    store, stub = _r2()
    stub.add_response("put_object", {}, {
        "Bucket": "acervo", "Key": "images/ab/x.jpg", "Body": b"img",
        "ContentType": "image/jpeg", "CacheControl": CACHE_CONTROL, "IfNoneMatch": "*",
    })
    with stub:
        store.put("images/ab/x.jpg", b"img", "image/jpeg")
    stub.assert_no_pending_responses()


def test_r2_412_means_already_there() -> None:
    store, stub = _r2()
    stub.add_client_error("put_object", "PreconditionFailed", http_status_code=412)
    with stub:
        store.put("images/ab/x.jpg", b"img", "image/jpeg")  # no raise


def test_r2_other_errors_raise() -> None:
    from botocore.exceptions import ClientError

    store, stub = _r2()
    stub.add_client_error("put_object", "AccessDenied", http_status_code=403)
    with stub, pytest.raises(ClientError):
        store.put("images/ab/x.jpg", b"img", "image/jpeg")


def test_a_missing_key_is_file_not_found_on_both_backends(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        LocalImageStore(tmp_path).get("images/ab/gone.jpg")
    store, stub = _r2()
    stub.add_client_error("get_object", "NoSuchKey", http_status_code=404)
    with stub, pytest.raises(FileNotFoundError):
        store.get("images/ab/gone.jpg")


def test_r2_get_other_errors_are_not_a_missing_key() -> None:
    from botocore.exceptions import ClientError

    store, stub = _r2()
    stub.add_client_error("get_object", "AccessDenied", http_status_code=403)
    with stub, pytest.raises(ClientError):
        store.get("images/ab/x.jpg")


def test_store_for_r2_needs_its_settings(tmp_path) -> None:
    assert isinstance(image_store_for(ImagesConfig(local_dir=str(tmp_path))), LocalImageStore)
    with pytest.raises(ValueError, match="BRAVE_IMAGES_R2_BUCKET"):
        image_store_for(ImagesConfig(store="r2", r2_endpoint="https://x", r2_access_key_id="k",
                                     r2_secret_access_key="s"))
    store = image_store_for(ImagesConfig(store="r2", r2_endpoint="https://x", r2_bucket="b",
                                         r2_access_key_id="k", r2_secret_access_key="s"))
    assert isinstance(store, R2ImageStore)
