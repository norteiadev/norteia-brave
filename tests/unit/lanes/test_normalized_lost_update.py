"""The `normalized` lost-update fix at the two Places-FSM agents (SignalAgent, ContactFinder).

PlacesEnrichmentAgent was fixed first (see test_places_enrichment.py) but the bug is in the
SHAPE, not in that one call site: read `normalized` → spend seconds in place_details → write
the whole JSONB column back from the pre-I/O snapshot. Both agents below did exactly that.

The money case: copy_batch.collect commits a PAID `descricao_editorial` + the lifted
`completude_value` and clears `descricao_batch_id` in one transaction. A snapshot write
landing right after erases the prose — and with the stamp already NULL the record matches the
batch selector again on the next tick and is billed a SECOND time.

100% offline: FakePlacesClient + mock RioRecord + MagicMock session (no DB, no network).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from tests.fakes.fake_places import SIGNAL_FIXTURE_OPEN, FakePlacesClient

_NOW = datetime(2026, 6, 15, tzinfo=UTC)
_PLACE_ID = "ChIJtest001"

# What the concurrent (batch collect) transaction commits while the agent is in place_details.
_PAID = {"descricao_editorial": "Prosa paga pelo batch.", "completude_value": 90.0}


class _RacingPlacesClient:
    """Commits the batched description WHILE the agent is inside its Places call.

    That is the real timeline: the task reads `normalized` at T0, spends ~2s in
    place_details, and copy_batch.apply_result commits its own transaction at T0+1s.
    Workers genuinely run in parallel (prefetch 1 is not concurrency 1).
    """

    def __init__(self, inner: FakePlacesClient, rio) -> None:  # noqa: ANN001
        self._inner = inner
        self._rio = rio

    async def text_search(self, query: str, uf: str):  # noqa: ANN201
        return await self._inner.text_search(query, uf)

    async def place_details(self, place_id: str):  # noqa: ANN201
        details = await self._inner.place_details(place_id)
        self._rio.normalized = {**self._rio.normalized, **_PAID}
        return details


def _make_rio(sub_state: str) -> MagicMock:
    rio = MagicMock()
    rio.id = uuid.uuid4()
    rio.sub_state = sub_state
    rio.routing = "in_progress"
    rio.dlq_reason = None
    rio.entity_type = "attraction"
    rio.uf = "BA"
    rio.normalized = {
        "place_id_cache": _PLACE_ID,
        "completude_value": 75.0,
        "atualidade_value": 0.0,
    }
    return rio


@pytest.mark.asyncio
async def test_signal_agent_does_not_erase_a_description_committed_mid_call() -> None:
    """SignalAgent must merge its delta onto the CURRENT column, under a row lock."""
    from brave.lanes.atrativos.signal_agent import SignalAgent

    inner = FakePlacesClient(fixture_details={_PLACE_ID: SIGNAL_FIXTURE_OPEN})
    rio = _make_rio("contacts_found")
    session = MagicMock()
    agent = SignalAgent(places_client=_RacingPlacesClient(inner, rio), session=session, now=_NOW)

    with (
        patch("brave.lanes.atrativos.signal_agent.write_audit"),
        patch("brave.lanes.atrativos.signal_agent.route_by_score"),
    ):
        await agent.run(rio)

    # The paid work survives...
    assert rio.normalized["descricao_editorial"] == "Prosa paga pelo batch."
    assert rio.normalized["completude_value"] == 90.0
    # ...and this run's own signals still landed.
    assert rio.normalized["weekday_text"] == SIGNAL_FIXTURE_OPEN["weekday_text"]
    assert rio.normalized["atualidade_value"] == 100.0
    assert rio.normalized["signal"]["business_status"] == "OPERATIONAL"
    assert rio.sub_state == "signals_gathered"
    # Merging a value read WITHOUT a lock only narrows the window; it does not close it.
    session.refresh.assert_called_once_with(rio, ["normalized"], with_for_update=True)


@pytest.mark.asyncio
async def test_contact_finder_does_not_erase_a_description_committed_mid_call() -> None:
    """Same shape at ContactFinderAgent — it writes the whole column too."""
    from brave.lanes.atrativos.contact_finder_agent import ContactFinderAgent

    inner = FakePlacesClient(
        fixture_details={_PLACE_ID: {"international_phone_number": "+55 73 99999-0001"}}
    )
    rio = _make_rio("discovered")
    session = MagicMock()
    agent = ContactFinderAgent(places_client=_RacingPlacesClient(inner, rio), session=session)

    with patch("brave.lanes.atrativos.contact_finder_agent.write_audit"):
        await agent.run(rio)

    assert rio.normalized["descricao_editorial"] == "Prosa paga pelo batch."
    assert rio.normalized["completude_value"] == 90.0
    assert rio.normalized["contacts"]["phone_e164"] == "+5573999990001"
    assert rio.sub_state == "contacts_found"
    session.refresh.assert_called_once_with(rio, ["normalized"], with_for_update=True)


@pytest.mark.parametrize("task_name", ["find_contacts_task", "gather_signals_task"])
def test_the_dispatching_task_takes_the_row_lock(task_name: str) -> None:
    """The merge re-reads under FOR UPDATE; the task must hold that lock for the whole run,
    or a concurrent writer simply commits between our read and our write instead of during
    it. The three pre-existing locked call sites (promote/push/gate) do exactly this.
    """
    from types import SimpleNamespace

    from brave.core.models import RioRecord
    from brave.tasks import pipeline

    raw_fn = getattr(pipeline, task_name).__wrapped__.__func__
    session = MagicMock()
    rio_id = str(uuid.uuid4())

    with (
        patch("brave.tasks.pipeline._get_session", return_value=(session, MagicMock())),
        patch("brave.tasks.pipeline.AppConfig") as app_config,
        patch("brave.tasks.pipeline.load_effective_config"),
    ):
        app_config.return_value.run_real_externals = False  # NullPlacesClient, no network
        raw_fn(SimpleNamespace(), rio_id)

    session.get.assert_called_once_with(RioRecord, uuid.UUID(rio_id), with_for_update=True)
