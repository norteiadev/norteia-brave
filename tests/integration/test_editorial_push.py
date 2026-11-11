"""The destination outbox: ``push_editorial_destinations`` (brave.core.mar.publication).

Destinations are not in Mar, so ``editorial_destinations`` is their outbox. The API upserts by
source_ref and an explicit null OVERWRITES — so a null/empty key must never be sent.
"""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy.orm import Session

from brave.clients.null_norteia_api import NullNorteiaApiClient
from brave.core.mar.publication import (
    _digest,
    editorial_destination_payload,
    push_editorial_destinations,
)
from brave.core.models import EditorialDestination
from brave.shared.exceptions import ApiDown

pytestmark = pytest.mark.integration

REF = "ibge:ZZ:9990001"


@pytest.fixture
def session(db_engine):
    conn = db_engine.connect()
    outer = conn.begin()
    s = Session(bind=conn, join_transaction_mode="create_savepoint")
    try:
        yield s
    finally:
        s.close()
        outer.rollback()
        conn.close()


class FakeApi:
    def __init__(self, exc: Exception | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.exc = exc

    async def push(self, entity_type: str, payload: dict) -> bool:
        if self.exc:
            raise self.exc
        self.calls.append((entity_type, payload))
        return True


def _status_error(status: int, body: str = "nope") -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "https://api.example/x")
    return httpx.HTTPStatusError(
        "e", request=req, response=httpx.Response(status, request=req, text=body)
    )


def dest(
    session, ref=REF, fields=None, name="Pancas", ibge="9990001", **kw
) -> EditorialDestination:
    row = EditorialDestination(
        source_ref=ref, uf="ZZ", municipio_ibge=ibge, tourist_name=name,
        fields=fields if fields is not None else {"tips": "Chegue cedo.", "pace_profile": "lento"}, **kw,
    )  # fmt: skip
    session.add(row)
    session.flush()
    return row


def push(session, api, enabled=True):
    return push_editorial_destinations(session, api, enabled=enabled)


def test_payload_has_the_required_keys_and_only_filled_columns() -> None:
    row = EditorialDestination(
        source_ref="mtur:ZZ:7", uf="ZZ", municipio_ibge="9990001", tourist_name="Pancas",
        fields={"tips": "Chegue cedo.", "seasonality": ["jan a mar"], "best_time_to_visit": "",
                "pace_profile": None, "how_to_get_there": "   ", "connectivity_level": []},
    )  # fmt: skip
    assert editorial_destination_payload(row) == {
        "source_ref": "mtur:ZZ:7",
        "source": "mtur",  # the prefix the destino block of the attraction push already uses
        "tourist_name": "Pancas",
        "municipio_ibge": "9990001",
        "tips": "Chegue cedo.",
        "seasonality": ["jan a mar"],
    }


def test_a_pending_row_is_pushed_and_stamped(session) -> None:
    row = dest(session)
    api = FakeApi()
    out = push(session, api)
    assert out.pushed == 1 and len(api.calls) == 1
    entity_type, payload = api.calls[0]
    assert entity_type == "destination"
    assert None not in payload.values() and "" not in payload.values()
    session.expire(row)
    assert (
        row.pushed_at is not None and row.push_error is None and row.push_hash == _digest(payload)
    )


def test_the_same_hash_is_never_sent_twice(session) -> None:
    row = dest(session)
    api = FakeApi()
    push(session, api)
    push(session, api)  # nothing pending
    assert len(api.calls) == 1
    # the resolver marks it pending again with identical content: stamped, not re-POSTed
    session.expire(row)
    row.pushed_at = None
    session.flush()
    out = push(session, api)
    assert len(api.calls) == 1 and out.unchanged == 1
    session.expire(row)
    assert row.pushed_at is not None


def test_changed_fields_are_sent_again(session) -> None:
    row = dest(session)
    api = FakeApi()
    push(session, api)
    row.fields = {"tips": "Chegue cedíssimo."}
    row.pushed_at = None
    session.flush()
    push(session, api)
    assert len(api.calls) == 2 and api.calls[1][1]["tips"] == "Chegue cedíssimo."
    assert "pace_profile" not in api.calls[1][1]  # dropped column is absent, never null


def test_the_disabled_flag_holds_everything_and_calls_nothing(session) -> None:
    row = dest(session)
    api = FakeApi()
    out = push(session, api, enabled=False)
    assert out.held is True and api.calls == []
    session.expire(row)
    assert row.pushed_at is None and row.push_hash is None


def test_a_permanent_4xx_is_recorded_and_not_retried(session) -> None:
    row = dest(session)
    out = push(session, FakeApi(exc=_status_error(422, "pace_profile invalid")))
    assert out.rejected == 1
    session.expire(row)
    assert "422" in row.push_error and row.pushed_at is None
    api = FakeApi()
    push(session, api)
    assert api.calls == []  # waits for a change (the resolver clears push_error)


@pytest.mark.parametrize("status", [401, 429, 500, 503])
def test_transient_failures_propagate_and_keep_the_row_pending(session, status) -> None:
    row = dest(session)
    with pytest.raises(httpx.HTTPStatusError):
        push(session, FakeApi(exc=_status_error(status)))
    session.expire(row)
    assert row.pushed_at is None and row.push_error is None


def test_api_down_stops_quietly_and_keeps_rows_pending(session) -> None:
    row = dest(session)
    out = push(session, FakeApi(exc=ApiDown("down")))
    assert out.api_down is True and out.pushed == 0
    session.expire(row)
    assert row.pushed_at is None


def test_the_null_adapter_sends_nothing_and_stamps_nothing(session) -> None:
    row = dest(session)
    out = push(session, NullNorteiaApiClient())
    assert out.not_sent == 1 and out.pushed == 0
    session.expire(row)
    assert row.pushed_at is None and row.push_hash is None


def test_a_row_missing_a_required_key_is_rejected_locally(session) -> None:
    row = dest(session, name="")
    api = FakeApi()
    out = push(session, api)
    assert api.calls == [] and out.rejected == 1
    session.expire(row)
    assert "tourist_name" in row.push_error


def test_a_row_with_no_filled_column_sends_nothing(session) -> None:
    dest(session, fields={"tips": "", "seasonality": []})
    api = FakeApi()
    out = push(session, api)
    assert api.calls == [] and out.pushed == 0
