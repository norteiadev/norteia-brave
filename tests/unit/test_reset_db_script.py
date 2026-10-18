"""reset-brave-db skill script: the Redis flush keeps redbeat/session/cost/geo state and
the broker purge clears acks_late in-flight bookkeeping (F88)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import fakeredis

_SCRIPT = Path(__file__).resolve().parents[2] / ".claude/skills/reset-brave-db/scripts/reset_db.py"
_spec = importlib.util.spec_from_file_location("reset_db", _SCRIPT)
reset_db = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(reset_db)

_KEEP = [
    "brave:schedule", "brave:statics", "brave:lock",
    "brave:ta:session", "brave:ta:needs_bootstrap",
    "brave:cost:daily:2026-10-17", "brave:geo:123", "brave:ta:geo:SP",
]
_WIPE = ["brave:engine:state", "brave:ta:sweep:progress", "brave:logs:default", "brave:config:overlay"]


def _seed() -> fakeredis.FakeRedis:
    r = fakeredis.FakeRedis()
    for k in _KEEP + _WIPE + ["other:key"]:
        r.set(k, "1")
    return r


def test_flush_preserves_infra_state_and_wipes_pipeline_state() -> None:
    r = _seed()
    deleted, kept = reset_db.flush_brave_keys(r, "brave:*")
    assert (deleted, kept) == (len(_WIPE), len(_KEEP))
    assert all(r.exists(k) for k in _KEEP + ["other:key"])
    assert not any(r.exists(k) for k in _WIPE)


def test_flush_opt_in_flags_drop_cost_and_geo() -> None:
    r = _seed()
    reset_db.flush_brave_keys(r, "brave:*", reset_cost=True, flush_geo_cache=True)
    assert not r.exists("brave:cost:daily:2026-10-17")
    assert not r.exists("brave:geo:123") and not r.exists("brave:ta:geo:SP")
    assert r.exists("brave:lock") and r.exists("brave:ta:session")


def test_reset_cost_keeps_the_places_key_credit() -> None:
    """--reset-cost frees today's budget; the Places key's credit counter is NOT a daily
    budget — wiping it would let an exhausted key spend another US$300."""
    r = _seed()
    r.set("brave:cost:places:total:abc123", "250")
    r.set("brave:cost:places:abc123:daily:2026-10-17", "5")
    reset_db.flush_brave_keys(r, "brave:*", reset_cost=True)
    assert r.exists("brave:cost:places:total:abc123")
    assert not r.exists("brave:cost:places:abc123:daily:2026-10-17")
    reset_db.flush_brave_keys(r, "brave:*", reset_places_credit=True)
    assert not r.exists("brave:cost:places:total:abc123")


def test_purge_broker_clears_unacked_bookkeeping() -> None:
    r = fakeredis.FakeRedis()
    r.rpush("celery", "msg")
    r.hset("unacked", "tag", "msg")
    r.zadd("unacked_index", {"tag": 1})
    r.set("unacked_mutex", "1")
    r.sadd("_kombu.binding.celery", "x")
    r.set("brave:lock", "1")
    reset_db.purge_broker(r)
    for k in ("celery", "unacked", "unacked_index", "unacked_mutex", "_kombu.binding.celery"):
        assert not r.exists(k), k
    assert r.exists("brave:lock")


def test_refuses_wipe_with_inflight_description_batches(monkeypatch) -> None:
    """Paid Anthropic batches are tracked only by rio_records.descricao_batch_id."""
    from unittest.mock import MagicMock

    import sqlalchemy

    def _execute(stmt):
        sql = str(stmt)
        res = MagicMock()
        if "pg_tables" in sql:
            res.__iter__.return_value = iter([("rio_records",)])
        res.scalar.return_value = 2 if "descricao_batch_id" in sql else 5
        return res

    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value.execute.side_effect = _execute
    monkeypatch.setattr(sqlalchemy, "create_engine", lambda url: engine)

    rc = reset_db.main(["--db-url", "postgresql://x/y", "--yes", "--no-redis"])
    assert rc == 4
    engine.begin.assert_not_called()  # no TRUNCATE
