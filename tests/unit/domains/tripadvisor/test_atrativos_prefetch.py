"""produce(): hybrid Places runs once per TA page, AFTER the card loop placed its rows (one
batched lookup per page, then run() per row); google mode runs Places inline per card.
Offline: store_raw / quarantine patched, MagicMock session."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from brave.config.settings import ScoreConfig
from tests.fakes.fake_tripadvisor import FakeTripAdvisorClient


class _PrefetchAgent:
    """Records the order of ingest / prefetch_items / prefetch / run (by the row's tag)."""

    prefetches = True

    def __init__(self, fail: dict[str, BaseException] | None = None) -> None:
        self.seq: list[tuple[str, Any]] = []
        self._fail = fail or {}

    def prefetch_items(self, rios, refresh=False):
        self.seq.append(("items", [r.tag for r in rios]))
        return [{"tag": r.tag} for r in rios]

    async def prefetch(self, items):
        self.seq.append(("prefetch", len(items)))

    async def locate(self, nome, uf, category=None):
        return None

    async def run(self, rio):
        self.seq.append(("run", rio.tag))
        if rio.tag in self._fail:
            raise self._fail[rio.tag]


class _GoogleAgent(_PrefetchAgent):
    """Google mode: no ``prefetches`` → Places runs inline, never through a page lot."""

    prefetches = None

    def prefetch_items(self, *a, **k):  # pragma: no cover — must not run
        raise AssertionError("google mode must not prefetch")


def _card(i: int, name: str, category: str = "Cachoeiras") -> dict[str, Any]:
    return {"locationId": i, "name": name, "review_count": 10, "rating": 4.5, "category": category}


async def _fake_ingest_one(self, uf, entity, *, run_rio, enrich_reviews=False, defer=None):
    """_ingest_one placing every card: its Places step deferred exactly like the real one."""
    self._places_agent.seq.append(("ingest", entity["name"]))
    rio = SimpleNamespace(id=uuid.uuid4(), tag=entity["name"])
    if defer is not None:
        defer.append(rio)
    else:
        await self._places_agent.run(rio)
    return str(rio.id)


async def _produce(agent, pages, *, backfill=None, **kw):
    from brave.domains.tripadvisor.atrativos import TripAdvisorAtrativosIngest

    ingest_cls = "brave.domains.tripadvisor.atrativos.TripAdvisorAtrativosIngest"
    with (
        patch("brave.domains.tripadvisor.atrativos.quarantine_poison"),
        patch("brave.domains.tripadvisor.atrativos.record_event_once"),
        patch(f"{ingest_cls}._ingest_one", _fake_ingest_one),
        patch(f"{ingest_cls}._already_synced", return_value=set(backfill or ())),
        patch(f"{ingest_cls}._backfill_candidates", return_value=backfill or {}),
    ):
        ingest = TripAdvisorAtrativosIngest(
            ta_client=FakeTripAdvisorClient(gql_pages=pages, geo_ids={"GO": 303323}),
            session=MagicMock(), config=ScoreConfig(), ibge_records=[], places_agent=agent,
        )
        await ingest.produce("GO", run_rio=True, **kw)


@pytest.mark.asyncio
async def test_hybrid_page_is_one_lookup_after_the_loop_places_its_cards() -> None:
    agent = _PrefetchAgent()
    pages = [
        (0, [_card(1, "Cachoeira A"), _card(2, "Shopping X", "Shopping centers"),
             _card(3, "Cachoeira B")]),
        (30, [_card(4, "Cachoeira C")]),
    ]
    await _produce(agent, pages)
    assert agent.seq == [
        ("ingest", "Cachoeira A"), ("ingest", "Cachoeira B"),  # mall card dropped
        ("items", ["Cachoeira A", "Cachoeira B"]), ("prefetch", 2),
        ("run", "Cachoeira A"), ("run", "Cachoeira B"),
        ("ingest", "Cachoeira C"), ("items", ["Cachoeira C"]), ("prefetch", 1),
        ("run", "Cachoeira C"),
    ]


@pytest.mark.asyncio
async def test_hybrid_lot_honours_the_operator_cap() -> None:
    agent = _PrefetchAgent()
    await _produce(agent, [(0, [_card(1, "A"), _card(2, "B"), _card(3, "C")])], max_per_uf=2)
    assert ("items", ["A", "B"]) in agent.seq and ("ingest", "C") not in agent.seq


@pytest.mark.asyncio
async def test_google_mode_runs_places_inline() -> None:
    agent = _GoogleAgent()
    await _produce(agent, [(0, [_card(1, "A"), _card(2, "B")])])
    assert agent.seq == [("ingest", "A"), ("run", "A"), ("ingest", "B"), ("run", "B")]


@pytest.mark.asyncio
async def test_backfill_rows_join_the_page_lot() -> None:
    from brave.domains.tripadvisor.atrativos import TA_BACKFILLED_KEY

    agent = _PrefetchAgent()
    done_ta = SimpleNamespace(id=uuid.uuid4(), tag="B", uf="GO",
                              normalized={TA_BACKFILLED_KEY: "2026-10-01T00:00:00+00:00"})
    await _produce(agent, [(0, [_card(1, "A"), _card(2, "B")])],
                   backfill={"tripadvisor:attraction:2": done_ta}, enrich_reviews=True)
    assert agent.seq == [
        ("ingest", "A"), ("items", ["A", "B"]), ("prefetch", 2), ("run", "A"), ("run", "B"),
    ]


@pytest.mark.asyncio
async def test_places_failure_in_the_lot_keeps_the_sweep_going() -> None:
    agent = _PrefetchAgent(fail={"A": RuntimeError("apify 500")})
    await _produce(agent, [(0, [_card(1, "A"), _card(2, "B")]), (30, [_card(3, "C")])])
    assert ("run", "B") in agent.seq and ("run", "C") in agent.seq


@pytest.mark.asyncio
async def test_wall_in_the_lot_stops_the_sweep() -> None:
    from brave.shared.exceptions import ProviderBalanceError

    agent = _PrefetchAgent(fail={"A": ProviderBalanceError("apify", "402")})
    with pytest.raises(ProviderBalanceError):
        await _produce(agent, [(0, [_card(1, "A"), _card(2, "B")]), (30, [_card(3, "C")])])
    assert ("run", "B") not in agent.seq and ("ingest", "C") not in agent.seq


# -- the real _ingest_one: which placed cards join the lot -------------------------------


class _TaggedAgent(_PrefetchAgent):
    def prefetch_items(self, rios, refresh=False):
        self.seq.append(("items", list(rios)))
        return [{}] * len(rios)

    async def run(self, rio):
        self.seq.append(("run", rio))


@pytest.mark.asyncio
async def test_card_placed_by_a_fallback_joins_the_lot() -> None:
    """TA's page answered without a locality ({}): the name step places the card, and its
    own normalized is what the lot searches."""
    from tests.unit.domains.tripadvisor.test_atrativos import (
        _IBGE_CODE_UB,
        _IBGE_RECORDS,
        _make_card,
        _produce_page,
    )

    rio = MagicMock(canonical_key="tripadvisor:attraction:312332", normalized={}, id=uuid.uuid4())
    agent = _TaggedAgent()
    store, _, _ = await _produce_page(
        "MG", _make_card(), records=_IBGE_RECORDS, places_agent=agent, run_rio=True, process=rio,
    )
    assert store.call_args.kwargs["payload"]["municipio_id"] == _IBGE_CODE_UB
    assert agent.seq == [("items", [rio]), ("prefetch", 1), ("run", rio)]


@pytest.mark.asyncio
async def test_card_with_a_failed_ta_page_is_not_enriched() -> None:
    from brave.shared.exceptions import SourceRecordError
    from tests.unit.domains.tripadvisor.test_atrativos import (
        _IBGE_RECORDS,
        _make_card,
        _produce_page,
    )

    rio = MagicMock(canonical_key="tripadvisor:attraction:312332", normalized={}, id=uuid.uuid4())
    agent = _TaggedAgent()
    await _produce_page(
        "MG", _make_card(), records=_IBGE_RECORDS, address_error=SourceRecordError("no block"),
        places_agent=agent, run_rio=True, process=rio,
    )
    assert agent.seq == []  # no lot, no run: the backfill redoes the page, then Places
