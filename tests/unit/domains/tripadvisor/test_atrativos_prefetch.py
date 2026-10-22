"""produce(): hybrid Places prefetches each TA page once (one Apify run per page, not per
card); google mode never does. Offline: store_raw / quarantine patched, MagicMock session."""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from brave.config.settings import ScoreConfig
from tests.fakes.fake_tripadvisor import FakeTripAdvisorClient


class _PrefetchAgent:
    prefetches = True

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def prefetch(self, rios=(), *, refresh=False, cards=(), uf=""):
        self.calls.append({"rios": list(rios), "cards": [c["name"] for c in cards], "uf": uf})

    async def locate(self, nome, uf, category=None):
        return None

    async def run(self, rio):  # pragma: no cover — cards quarantine (no município)
        return None


class _GoogleAgent(_PrefetchAgent):
    """Google mode: no ``prefetches`` → produce() must never call prefetch()."""

    prefetches = None

    async def prefetch(self, *a, **k):  # pragma: no cover — must not run
        raise AssertionError("google mode must not prefetch")


def _card(i: int, name: str, category: str = "Cachoeiras") -> dict[str, Any]:
    return {"locationId": i, "name": name, "review_count": 10, "rating": 4.5, "category": category}


async def _produce(agent, pages, **kw):
    from brave.domains.tripadvisor.atrativos import TripAdvisorAtrativosIngest

    with (
        patch("brave.domains.tripadvisor.atrativos.store_raw") as store,
        patch("brave.domains.tripadvisor.atrativos.quarantine_poison"),
        patch("brave.domains.tripadvisor.atrativos.record_event_once"),
    ):
        store.return_value = MagicMock(id=uuid.uuid4())
        ingest = TripAdvisorAtrativosIngest(
            ta_client=FakeTripAdvisorClient(gql_pages=pages, geo_ids={"GO": 303323}),
            session=MagicMock(), config=ScoreConfig(), ibge_records=[], places_agent=agent,
        )
        await ingest.produce("GO", run_rio=True, **kw)


@pytest.mark.asyncio
async def test_hybrid_prefetches_each_page_once_with_the_cards_the_loop_ingests() -> None:
    agent = _PrefetchAgent()
    pages = [
        (0, [_card(1, "Cachoeira A"), _card(2, "Shopping X", "Shopping centers"),
             _card(3, "Cachoeira B")]),
        (30, [_card(4, "Cachoeira C")]),
    ]
    await _produce(agent, pages)
    assert agent.calls == [
        {"rios": [], "cards": ["Cachoeira A", "Cachoeira B"], "uf": "GO"},  # mall card dropped
        {"rios": [], "cards": ["Cachoeira C"], "uf": "GO"},
    ]


@pytest.mark.asyncio
async def test_hybrid_prefetch_honours_the_operator_cap() -> None:
    agent = _PrefetchAgent()
    await _produce(agent, [(0, [_card(1, "A"), _card(2, "B"), _card(3, "C")])], max_per_uf=2)
    assert agent.calls[0]["cards"] == ["A", "B"]


@pytest.mark.asyncio
async def test_google_mode_never_prefetches() -> None:
    await _produce(_GoogleAgent(), [(0, [_card(1, "Cachoeira A")])])
