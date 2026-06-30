"""Unit tests for brave/observability/log_buffer.py.

100% offline — uses fakeredis.FakeRedis() in place of a real Redis client.
Tests verify: LPUSH ring-buffer behavior, monotonic ids, LTRIM cap, secret
stripping, tail_logs ordering and cursor-based incremental fetch.
"""

import json

import fakeredis
import pytest

from brave.observability.log_buffer import (
    _CAP,
    _BLOCKED_FIELDS,
    _LOG_KEY_TPL,
    append_log,
    tail_logs,
)


@pytest.fixture
def redis():
    return fakeredis.FakeRedis()


# ---------------------------------------------------------------------------
# append_log
# ---------------------------------------------------------------------------


def test_append_log_creates_list_entry(redis):
    """append_log x1 → LRANGE has 1 entry with 'id' and 'event' keys."""
    append_log(redis, "tripadvisor", {"event": "page_ingested", "level": "info"})
    raw = redis.lrange(_LOG_KEY_TPL.format("tripadvisor"), 0, -1)
    assert len(raw) == 1
    entry = json.loads(raw[0])
    assert "id" in entry
    assert entry["event"] == "page_ingested"


def test_append_assigns_monotonic_ids(redis):
    """append_log x3 → ids are monotonically increasing; list head has highest id."""
    for i in range(3):
        append_log(redis, "tripadvisor", {"event": f"evt_{i}"})
    raw = redis.lrange(_LOG_KEY_TPL.format("tripadvisor"), 0, -1)
    ids = [json.loads(b)["id"] for b in raw]
    # LPUSH means newest is at index 0 → ids[0] > ids[1] > ids[2]
    assert ids[0] > ids[1] > ids[2]


def test_tail_logs_sorted_oldest_first(redis):
    """tail_logs returns lines sorted by id ascending (oldest first)."""
    for i in range(3):
        append_log(redis, "tripadvisor", {"event": f"evt_{i}"})
    lines, _ = tail_logs(redis, "tripadvisor")
    assert lines[0]["id"] < lines[1]["id"] < lines[2]["id"]


def test_tail_logs_since_id_returns_only_newer(redis):
    """tail_logs(since_id=N) returns only lines where id > N."""
    for i in range(5):
        append_log(redis, "tripadvisor", {"event": f"evt_{i}"})
    all_lines, _ = tail_logs(redis, "tripadvisor")
    cutoff = all_lines[2]["id"]  # third oldest
    newer_lines, _ = tail_logs(redis, "tripadvisor", since_id=cutoff)
    assert len(newer_lines) == 2
    assert all(l["id"] > cutoff for l in newer_lines)


def test_append_log_trims_to_cap(redis):
    """append_log x(_CAP+10) → LLEN == _CAP (LTRIM holds)."""
    for i in range(_CAP + 10):
        append_log(redis, "tripadvisor", {"event": "fill"})
    assert redis.llen(_LOG_KEY_TPL.format("tripadvisor")) == _CAP


def test_append_log_strips_blocked_fields(redis):
    """append_log with blocked fields → those keys absent in stored entry; 'event' present."""
    append_log(
        redis,
        "tripadvisor",
        {"cookies": "DATADOME=abc123", "token": "bearer-xyz", "event": "test_strip"},
    )
    raw = redis.lrange(_LOG_KEY_TPL.format("tripadvisor"), 0, -1)
    entry = json.loads(raw[0])
    assert "cookies" not in entry
    assert "token" not in entry
    assert entry["event"] == "test_strip"


def test_tail_logs_empty_source_returns_empty(redis):
    """tail_logs on a nonexistent source returns ([], 0)."""
    lines, cursor = tail_logs(redis, "nonexistent_source_xyz")
    assert lines == []
    assert cursor == 0
