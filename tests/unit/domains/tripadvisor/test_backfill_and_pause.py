"""Bulk-completion backfill (C2) + run-level error propagation of the TA lane.

Covers: backfill_record (TA page → UF/município/parent, review recency max-merge, Places
step, idempotency, propagation), run-level errors escaping produce()/produce_paginated
instead of poisoning every card (F00/F06), per-card savepoints (F15), Nominatim failure not
aborting the município chain (F07), a cross-UF TA page (F12), the bulk's UF consistency
(F02) and per-UF completion of records already in Rio (F09).

100% offline: fake TA client / geocoder, MagicMock session, store_raw/route patched.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from celery.exceptions import SoftTimeLimitExceeded

from brave.config.settings import ScoreConfig, TripAdvisorConfig
from brave.core.models import RioRecord
from brave.domains.tripadvisor.client import SessionExpiredError
from brave.domains.tripadvisor.ibge import IbgeMunicipio
from brave.shared.exceptions import (
    CostGuardError,
    ProviderBalanceError,
    SourceRateLimitError,
    SourceSchemaError,
)
from tests.fakes.fake_nominatim import FakeGeocoderClient
from tests.fakes.fake_tripadvisor import FakeTripAdvisorClient

_UBERLANDIA = IbgeMunicipio("3170107", "Uberlândia", "MG", -18.9186, -48.2772)
_CHAPADA = IbgeMunicipio("5103007", "Chapada dos Guimarães", "MT", -15.4606, -55.7497)
_POCONE = IbgeMunicipio("5106505", "Poconé", "MT", -16.2566, -56.6228)
_CORUMBA = IbgeMunicipio("5003207", "Corumbá", "MS", -19.0077, -57.651)
_RECORDS = [_UBERLANDIA, _CHAPADA, _POCONE, _CORUMBA]


def _config() -> ScoreConfig:
    return ScoreConfig(
        weight_origem=30.0,
        weight_completude=20.0,
        weight_corroboracao=20.0,
        weight_atualidade=15.0,
        weight_validacao_humana=15.0,
        threshold_mar=85.0,
        score_version="v1.1",
    )


def _bulk_rio(**normalized: Any) -> RioRecord:
    """A bulk record: name-only national geocode put it in Uberlândia/MG, no parent."""
    base = {
        "name": "Cachoeira Véu De Noiva",
        "municipio": "Uberlândia",
        "municipio_id": "3170107",
        "lat": -18.92,
        "lon": -48.28,
        "origem_value": 65.0,
        "completude_value": 60.0,
        "corroboracao_value": 50.0,
        "atualidade_value": 0.0,
        "validacao_humana_value": 0.0,
        "review_count": 100,
    }
    base.update(normalized)
    return RioRecord(
        id=uuid.uuid4(),
        nascente_id=uuid.uuid4(),
        entity_type="attraction",
        uf="MG",
        municipio_id="3170107",
        routing="dlq",
        normalized=base,
        canonical_key="tripadvisor:attraction:555",
    )


def _geo(city: str | None, state: str) -> dict[str, Any]:
    """TA's attraction-page (or geo query) answer: locality + region."""
    return {"city_name": city, "state_name": state}


class _Agent:
    """PlacesEnrichmentAgent stand-in recording run()/locate()."""

    def __init__(self, place: dict[str, Any] | None = None, run_error: Exception | None = None):
        self._place = place
        self._run_error = run_error
        self.run_calls: list[RioRecord] = []
        self.locate_calls: list[tuple[str, str]] = []

    async def locate(self, nome: str, uf: str) -> dict[str, Any] | None:
        self.locate_calls.append((nome, uf))
        return self._place

    async def run(self, rio: RioRecord) -> None:
        self.run_calls.append(rio)
        if self._run_error is not None:
            raise self._run_error
        rio.normalized = {**(rio.normalized or {}), "google_enriched": True}


def _ingest(client: FakeTripAdvisorClient, **kw: Any):
    from brave.domains.tripadvisor.atrativos import TripAdvisorAtrativosIngest

    kw.setdefault("ta_config", TripAdvisorConfig(page_throttle_seconds=0))
    return TripAdvisorAtrativosIngest(
        ta_client=client,
        session=kw.pop("session", MagicMock()),
        config=_config(),
        ibge_records=_RECORDS,
        destino_rio_map=kw.pop("destino_rio_map", {}),
        **kw,
    )


_PARENT = (uuid.uuid4(), "ibge:destination:5103007")


# ---------------------------------------------------------------------------
# C2 — backfill_record
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backfill_moves_a_wrong_uf_bulk_record_to_ta_city_and_links_parent() -> None:
    recent = datetime.now(UTC) - timedelta(days=5)
    client = FakeTripAdvisorClient(
        fixture_address={555: _geo("Chapada dos Guimaraes", "State of Mato Grosso")},
        recent_review={"review_count": 250, "rating": None, "most_recent_review_at": recent},
    )
    agent = _Agent()
    ingest = _ingest(client, places_agent=agent)
    rio = _bulk_rio(google_enriched=True, place_id_cache="ChIJ-wrong-uf")

    with patch.object(type(ingest), "_ensure_destino", return_value=_PARENT) as ensure:
        status = await ingest.backfill_record(rio)

    n = rio.normalized
    assert rio.uf == "MT" and rio.municipio_id == "5103007"
    assert n["municipio"] == "Chapada dos Guimarães" and n["municipio_id"] == "5103007"
    assert n["parent_rio_id"] == str(_PARENT[0]) and n["parent_source_ref"] == _PARENT[1]
    ensure.assert_called_once_with(_CHAPADA)
    # The wrong-UF place/coords are dropped and the paid Places step re-opened (then re-ran).
    assert n["place_id_cache"] is None and n["lat"] is None
    assert agent.run_calls == [rio] and n["google_enriched"] is True
    # Recency merged; marker set; re-scored.
    assert n["most_recent_review_at"] == recent.isoformat()
    assert n["review_count"] == 250 and n["atualidade_value"] > 0
    assert n["ta_backfilled_at"] and n["ta_uf"] == "MT"
    assert rio.score is not None
    assert status == "ta+places"


@pytest.mark.asyncio
async def test_backfill_same_uf_keeps_municipio_links_parent_and_never_regresses_recency() -> None:
    newer = datetime.now(UTC) - timedelta(days=2)
    older = newer - timedelta(days=300)
    client = FakeTripAdvisorClient(
        fixture_address={555: _geo(None, "State of Minas Gerais")},  # TA gives no city
        recent_review={"review_count": 40, "rating": None, "most_recent_review_at": older},
    )
    ingest = _ingest(client)
    rio = _bulk_rio(most_recent_review_at=newer.isoformat(), atualidade_value=100.0)

    with patch.object(type(ingest), "_ensure_destino", return_value=(uuid.uuid4(), "ibge:x")):
        status = await ingest.backfill_record(rio)

    n = rio.normalized
    assert rio.uf == "MG" and n["municipio_id"] == "3170107"  # confirmed, not moved
    assert n["parent_rio_id"]  # bulk record linked to its destino
    assert n["most_recent_review_at"] == newer.isoformat()  # older TA date never regresses
    assert n["atualidade_value"] == 100.0 and n["review_count"] == 100
    assert status == "ta"


@pytest.mark.asyncio
async def test_backfill_unplaceable_other_uf_goes_to_dlq_and_skips_places() -> None:
    client = FakeTripAdvisorClient(
        fixture_address={555: _geo(None, "State of Mato Grosso")}, recent_review=None
    )
    agent = _Agent(place=None)  # Places locate cannot place it in MT either
    ingest = _ingest(client, places_agent=agent)
    rio = _bulk_rio()

    status = await ingest.backfill_record(rio)

    assert status == "municipio_nao_confirmado"
    assert rio.routing == "dlq" and rio.dlq_reason == "municipio_nao_confirmado"
    assert rio.uf == "MG"  # nothing confidently better to write
    assert agent.locate_calls == [("Cachoeira Véu De Noiva", "MT")]
    assert agent.run_calls == []  # a search in the stored (wrong) UF would match a homonym
    assert rio.normalized["ta_backfilled_at"]
    # Re-selected later for the Places side: still skipped.
    assert await ingest.backfill_record(rio) == "municipio_nao_confirmado"
    assert agent.run_calls == []


@pytest.mark.asyncio
async def test_backfill_places_locate_hands_over_place_id() -> None:
    client = FakeTripAdvisorClient(fixture_address={555: _geo(None, "State of Mato Grosso")})
    agent = _Agent(place={"place_id": "ChIJ-veu", "municipio_ibge": "5103007",
                          "location": {"lat": -15.4, "lng": -55.8}})
    ingest = _ingest(client, places_agent=agent)
    rio = _bulk_rio()

    with patch.object(type(ingest), "_ensure_destino", return_value=_PARENT):
        await ingest.backfill_record(rio)

    assert rio.uf == "MT" and rio.normalized["place_id_cache"] == "ChIJ-veu"
    assert (rio.normalized["lat"], rio.normalized["lon"]) == (-15.4, -55.8)


@pytest.mark.asyncio
async def test_backfill_is_idempotent_on_the_markers() -> None:
    client = FakeTripAdvisorClient()
    agent = _Agent()
    ingest = _ingest(client, places_agent=agent)
    rio = _bulk_rio(ta_backfilled_at="2026-10-01T00:00:00+00:00", google_enriched=True)

    assert await ingest.backfill_record(rio) == "noop"
    assert client.address_calls == [] and client.recent_review_calls == [] and agent.run_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        SessionExpiredError("x"),
        SourceRateLimitError("x"),
        SourceSchemaError("x"),
        ProviderBalanceError("google_places"),
        SoftTimeLimitExceeded(),
    ],
    ids=lambda e: type(e).__name__,
)
async def test_backfill_propagates_run_level_errors_without_marking(error) -> None:
    client = FakeTripAdvisorClient(fixture_address={555: _geo("Uberlandia", "State of Minas Gerais")},
                                   recent_review_error=error)
    ingest = _ingest(client)
    rio = _bulk_rio()

    with pytest.raises(type(error)):
        await ingest.backfill_record(rio)
    assert "ta_backfilled_at" not in rio.normalized


@pytest.mark.asyncio
async def test_backfill_propagates_places_cost_cap_after_ta_marker() -> None:
    client = FakeTripAdvisorClient(fixture_address={555: _geo("Uberlandia", "State of Minas Gerais")})
    agent = _Agent(run_error=CostGuardError("places cap"))
    ingest = _ingest(client, places_agent=agent)
    rio = _bulk_rio(parent_rio_id="p")

    with pytest.raises(CostGuardError):
        await ingest.backfill_record(rio)
    assert rio.normalized["ta_backfilled_at"]  # TA side done; Places side retried later
    assert rio.normalized.get("google_enriched") is not True


# ---------------------------------------------------------------------------
# F00 / F06 — run-level errors escape the per-card quarantine
# ---------------------------------------------------------------------------


def _card(location_id: int, name: str = "Uberlândia") -> dict[str, Any]:
    return {"locationId": location_id, "name": name, "review_count": 10, "rating": 4.0,
            "category": "Parks"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [SessionExpiredError("x"), SourceRateLimitError("x"), SourceSchemaError("x"),
     SoftTimeLimitExceeded(), CostGuardError("x")],
    ids=lambda e: type(e).__name__,
)
async def test_produce_propagates_run_level_errors_instead_of_poisoning(error) -> None:
    client = FakeTripAdvisorClient(
        gql_pages=[(0, [_card(1), _card(2)])], geo_ids={"MG": 1}, recent_review_error=error
    )
    with (
        patch("brave.domains.tripadvisor.atrativos.store_raw"),
        patch("brave.domains.tripadvisor.atrativos.process_nascente_record"),
        patch("brave.domains.tripadvisor.atrativos.quarantine_poison") as poison,
    ):
        ingest = _ingest(client)
        with pytest.raises(type(error)):
            await ingest.produce("MG", run_rio=True, enrich_reviews=True)

    # Stopped within the page's first window of TA calls (both cards' were in flight).
    assert sorted(client.recent_review_calls) == [1, 2]
    assert not poison.called


@pytest.mark.asyncio
async def test_produce_paginated_propagates_soft_time_limit() -> None:
    import fakeredis

    class _Geo(FakeGeocoderClient):
        async def geocode_national(self, location_id, name):
            raise SoftTimeLimitExceeded()

    client = FakeTripAdvisorClient(gql_pages=[(0, [_card(1), _card(2)])])
    with patch("brave.domains.tripadvisor.atrativos.quarantine_poison") as poison:
        ingest = _ingest(client, geocoder=_Geo())
        with pytest.raises(SoftTimeLimitExceeded):
            await ingest.produce_paginated(294280, 1, 1, fakeredis.FakeRedis())
    assert not poison.called


# ---------------------------------------------------------------------------
# F15 — one savepoint per card on the per-page-commit paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_produce_paginated_isolates_each_card_in_a_savepoint() -> None:
    import fakeredis

    session = MagicMock()
    geo = FakeGeocoderClient(fixture_national_results={
        "1": {"lat": -18.92, "lon": -48.28, "osm_id": 1, "municipio_name": "Uberlândia",
              "uf": "MG"},
        "2": {"lat": -18.92, "lon": -48.28, "osm_id": 2, "municipio_name": "Uberlândia",
              "uf": "MG"},
    })
    client = FakeTripAdvisorClient(gql_pages=[(0, [_card(1), _card(2)])])
    with (
        patch("brave.domains.tripadvisor.atrativos.store_raw",
              side_effect=[RuntimeError("uq_canonical_key"), MagicMock()]),
        patch("brave.domains.tripadvisor.atrativos.quarantine_poison") as poison,
        patch("brave.domains.tripadvisor.atrativos.TripAdvisorAtrativosIngest._ensure_destino",
              return_value=_PARENT),
    ):
        ingest = _ingest(client, session=session, geocoder=geo)
        await ingest.produce_paginated(294280, 1, 1, fakeredis.FakeRedis(), run_rio=False)

    assert session.begin_nested.call_count == 2
    # The failing card's savepoint saw the exception (rolled back only that card) …
    exit_args = session.begin_nested.return_value.__exit__.call_args_list[0].args
    assert exit_args[0] is RuntimeError
    # … and the poison row + page commit still happened on a healthy transaction.
    assert poison.call_count == 1 and session.commit.called


# ---------------------------------------------------------------------------
# F07 / F12 — município chain robustness in _ingest_one
# ---------------------------------------------------------------------------


class _BannedGeocoder(FakeGeocoderClient):
    async def geocode(self, location_id, name, uf):
        raise RuntimeError("nominatim 403 banned")


@pytest.mark.asyncio
async def test_banned_nominatim_never_blocks_the_ta_page() -> None:
    card = _card(7, name="Mirante Sem Nome")
    client = FakeTripAdvisorClient(
        gql_pages=[(0, [card])], geo_ids={"MT": 1},
        fixture_address={7: _geo("Chapada dos Guimaraes", "State of Mato Grosso")},
    )
    with (
        patch("brave.domains.tripadvisor.atrativos.store_raw") as store,
        patch("brave.domains.tripadvisor.atrativos.quarantine_poison") as poison,
    ):
        ingest = _ingest(client, geocoder=_BannedGeocoder(),
                         destino_rio_map={"5103007": _PARENT})
        await ingest.produce("MT", run_rio=False)

    assert not poison.called
    assert client.address_calls == [7]
    assert store.call_args.kwargs["payload"]["municipio_id"] == "5103007"


@pytest.mark.asyncio
async def test_ta_page_region_moves_the_card_to_its_uf() -> None:
    card = _card(8, name="Transpantaneira")
    client = FakeTripAdvisorClient(
        gql_pages=[(0, [card])], geo_ids={"MS": 1},
        fixture_address={8: _geo("Pocone", "State of Mato Grosso")},  # listed under MS
    )
    with patch("brave.domains.tripadvisor.atrativos.store_raw") as store:
        ingest = _ingest(client, destino_rio_map={"5106505": _PARENT})
        await ingest.produce("MS", run_rio=False)

    kwargs = store.call_args.kwargs
    assert kwargs["uf"] == "MT"
    assert kwargs["payload"]["uf"] == "MT" and kwargs["payload"]["canonical"]["uf"] == "MT"
    assert kwargs["payload"]["municipio_id"] == "5106505"


# ---------------------------------------------------------------------------
# F02 — the bulk keeps the IBGE seat inside Nominatim's UF
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_stays_inside_the_uf_nominatim_reported() -> None:
    # A border point: nearest seat is Corumbá/MS, but Nominatim says the point is in MT.
    near_border = {"lat": -18.4, "lon": -57.2, "osm_id": 1, "municipio_name": None, "uf": "MT"}
    records = [IbgeMunicipio("5003207", "Corumbá", "MS", -18.45, -57.25),
               IbgeMunicipio("5106505", "Poconé", "MT", -18.2, -57.0)]
    geo = FakeGeocoderClient(fixture_national_results={"9": near_border})
    from brave.domains.tripadvisor.atrativos import TripAdvisorAtrativosIngest

    with (
        patch("brave.domains.tripadvisor.atrativos.store_raw") as store,
        patch.object(TripAdvisorAtrativosIngest, "_ensure_destino", return_value=_PARENT),
    ):
        ingest = TripAdvisorAtrativosIngest(
            ta_client=FakeTripAdvisorClient(), session=MagicMock(), config=_config(),
            ibge_records=records, geocoder=geo,
        )
        assert await ingest._ingest_one_bulk(_card(9, name="Fazenda X"), run_rio=False)

    assert store.call_args.kwargs["uf"] == "MT"
    assert store.call_args.kwargs["payload"]["municipio_id"] == "5106505"


# ---------------------------------------------------------------------------
# F09 / C2 — the per-UF sweep completes records already in Rio without the marker
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_per_uf_sweep_backfills_rio_records_missing_the_marker() -> None:
    rio = _bulk_rio()
    rio.canonical_key = "tripadvisor:attraction:1"
    session = MagicMock()
    # Rio canonical keys; unmatched quarantines; Rio rows awaiting the TA backfill.
    session.scalars.side_effect = [["tripadvisor:attraction:1"], [], [rio]]
    client = FakeTripAdvisorClient(
        gql_pages=[(0, [_card(1)])], geo_ids={"MG": 1},
        fixture_address={1: _geo("Uberlandia", "State of Minas Gerais")},
        recent_review={"review_count": 120, "rating": None,
                       "most_recent_review_at": datetime.now(UTC) - timedelta(days=3)},
    )
    with (
        patch("brave.domains.tripadvisor.atrativos.store_raw") as store,
        patch.object(
            __import__("brave.domains.tripadvisor.atrativos", fromlist=["x"])
            .TripAdvisorAtrativosIngest,
            "_ensure_destino", return_value=_PARENT,
        ),
    ):
        ingest = _ingest(client, session=session)
        await ingest.produce("MG", run_rio=True, enrich_reviews=True)

    assert not store.called  # completed in place — no duplicate Nascente row
    assert client.recent_review_calls == [1]
    assert rio.normalized["ta_backfilled_at"] and rio.normalized["most_recent_review_at"]
    assert session.commit.called


# ---------------------------------------------------------------------------
# Latency — TA page + newest review in parallel, one throttle per card
# ---------------------------------------------------------------------------


class _Together(FakeTripAdvisorClient):
    """Each call waits until the other one started: two calls in a row time out."""

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self._started: list[str] = []
        self._both = asyncio.Event()

    async def _meet(self, what: str) -> None:
        self._started.append(what)
        if len(self._started) == 2:
            self._both.set()
        await asyncio.wait_for(self._both.wait(), timeout=1)

    async def fetch_attraction_address(self, location_id: int) -> dict[str, Any]:
        await self._meet("page")
        return await super().fetch_attraction_address(location_id)

    async def fetch_recent_review(self, location_id: int) -> dict[str, Any] | None:
        await self._meet("reviews")
        return await super().fetch_recent_review(location_id)


@pytest.mark.asyncio
async def test_sweep_fetches_ta_page_and_reviews_together_after_one_throttle() -> None:
    recent = datetime.now(UTC) - timedelta(days=5)
    client = _Together(
        gql_pages=[(0, [_card(7, name="Mirante Sem Nome")])], geo_ids={"MT": 1},
        fixture_address={7: _geo("Chapada dos Guimaraes", "State of Mato Grosso")},
        recent_review={"review_count": 30, "rating": None, "most_recent_review_at": recent},
    )
    with (
        patch("brave.domains.tripadvisor.atrativos.store_raw") as store,
        patch("brave.domains.tripadvisor.atrativos.process_nascente_record"),
    ):
        ingest = _ingest(client, destino_rio_map={"5103007": _PARENT})
        with patch.object(ingest, "_throttle", AsyncMock()) as throttle:
            await ingest.produce("MT", run_rio=False, enrich_reviews=True)

    assert throttle.await_count == 1
    assert client.address_calls == [7] and client.recent_review_calls == [7]
    payload = store.call_args.kwargs["payload"]
    assert payload["municipio_id"] == "5103007" and payload["review_count"] == 30


@pytest.mark.asyncio
async def test_backfill_fetches_ta_page_and_reviews_together_after_one_throttle() -> None:
    recent = datetime.now(UTC) - timedelta(days=5)
    client = _Together(
        fixture_address={555: _geo("Chapada dos Guimaraes", "State of Mato Grosso")},
        recent_review={"review_count": 250, "rating": None, "most_recent_review_at": recent},
    )
    ingest = _ingest(client)
    rio = _bulk_rio(google_enriched=True, place_id_cache="ChIJ-x")

    with (
        patch.object(type(ingest), "_ensure_destino", return_value=_PARENT),
        patch.object(ingest, "_throttle", AsyncMock()) as throttle,
    ):
        await ingest.backfill_record(rio)

    assert throttle.await_count == 1
    assert rio.uf == "MT" and rio.normalized["review_count"] == 250


@pytest.mark.asyncio
async def test_backfill_raises_a_session_error_once_both_calls_settled() -> None:
    client = FakeTripAdvisorClient(
        address_error=SessionExpiredError("captcha"),
        recent_review={"review_count": 1, "rating": None, "most_recent_review_at": None},
    )
    ingest = _ingest(client)

    with pytest.raises(SessionExpiredError):
        await ingest.backfill_record(_bulk_rio())

    assert client.recent_review_calls == [555]


class _Inflight(FakeTripAdvisorClient):
    """Counts the attraction pages in flight at once; one page id may fail."""

    def __init__(self, fail: int | None = None, **kw: Any) -> None:
        super().__init__(**kw)
        self._fail = fail
        self.now = self.peak = 0

    async def fetch_attraction_address(self, location_id: int) -> dict[str, Any]:
        self.now += 1
        self.peak = max(self.peak, self.now)
        await asyncio.sleep(0.01)
        self.now -= 1
        if location_id == self._fail:
            raise RuntimeError("boom")
        return await super().fetch_attraction_address(location_id)


def _sweep_client(n: int, **kw: Any) -> _Inflight:
    chapada = _geo("Chapada dos Guimaraes", "State of Mato Grosso")
    return _Inflight(
        gql_pages=[(0, [_card(i, name=f"Mirante {i}") for i in range(1, n + 1)])],
        geo_ids={"MT": 1},
        fixture_address={i: chapada for i in range(1, n + 1)},
        recent_review={"review_count": 3, "rating": None, "most_recent_review_at": None},
        **kw,
    )


async def _sweep(client: _Inflight, **kw: Any) -> tuple[MagicMock, MagicMock]:
    with (
        patch("brave.domains.tripadvisor.atrativos.store_raw") as store,
        patch("brave.domains.tripadvisor.atrativos.process_nascente_record"),
        patch("brave.domains.tripadvisor.atrativos.quarantine_poison") as poison,
    ):
        ingest = _ingest(client, destino_rio_map={"5103007": _PARENT})
        await ingest.produce("MT", run_rio=False, enrich_reviews=True, **kw)
    return store, poison


@pytest.mark.asyncio
async def test_sweep_page_runs_ta_calls_four_cards_at_a_time() -> None:
    client = _sweep_client(10)

    store, _ = await _sweep(client)

    assert client.peak == 4
    assert sorted(client.address_calls) == list(range(1, 11))  # once each, never refetched
    assert sorted(client.recent_review_calls) == list(range(1, 11))
    refs = [c.kwargs["source_ref"] for c in store.call_args_list]
    assert refs == [f"tripadvisor:attraction:{i}" for i in range(1, 11)]  # card order kept


@pytest.mark.asyncio
async def test_sweep_ahead_fetches_only_up_to_the_operator_cap() -> None:
    client = _sweep_client(10)

    store, _ = await _sweep(client, max_per_uf=3)

    assert sorted(client.address_calls) == [1, 2, 3]
    assert store.call_count == 3


@pytest.mark.asyncio
async def test_sweep_ahead_error_lands_on_its_own_card() -> None:
    client = _sweep_client(3, fail=2)

    store, poison = await _sweep(client)

    refs = [c.kwargs["source_ref"] for c in store.call_args_list]
    assert refs == ["tripadvisor:attraction:1", "tripadvisor:attraction:3"]
    assert poison.call_count == 1 and poison.call_args.kwargs["payload"]["locationId"] == "2"


@pytest.mark.asyncio
async def test_sweep_ahead_session_error_stops_the_page_before_any_card() -> None:
    client = _sweep_client(3, address_error=SessionExpiredError("captcha"))

    with pytest.raises(SessionExpiredError):
        await _sweep(client)
