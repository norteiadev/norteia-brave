"""Review fixes of the TA backfill / bulk lane (wave 2).

C1 marker only on a conclusive verdict, run-level errors through the paid in-UF fallbacks,
parent linkage without usable TA geo, same-UF moves only without evidence, stale Google /
description fields dropped on a move, deliberate DLQ verdicts preserved by the re-score,
the Places savepoint, and the bulk's TA-geo fallback for cards the national geocode
missed. 100% offline: fake TA client / geocoder, MagicMock session.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import fakeredis
import pytest
from celery.exceptions import SoftTimeLimitExceeded

from brave.domains.tripadvisor.client import SessionExpiredError
from brave.shared.exceptions import CostGuardError, ProviderBalanceError
from tests.fakes.fake_nominatim import FakeGeocoderClient
from tests.fakes.fake_tripadvisor import FakeTripAdvisorClient
from tests.unit.domains.tripadvisor.test_backfill_and_pause import (
    _PARENT,
    _Agent,
    _bulk_rio,
    _card,
    _geo,
    _ingest,
)

_MOD = "brave.domains.tripadvisor.atrativos"


def _ensure(ingest: Any):
    return patch.object(type(ingest), "_ensure_destino", return_value=_PARENT)


class _BannedGeocoder(FakeGeocoderClient):
    async def geocode(self, location_id, name, uf):
        raise RuntimeError("nominatim 429 banned")


class _FailingLocateAgent(_Agent):
    """Like PlacesEnrichmentAgent.locate on a transient error: None + locate_failed."""

    async def locate(self, nome, uf):
        self.locate_calls.append((nome, uf))
        self.locate_failed = True
        return None


class _RaisingLocateAgent(_Agent):
    def __init__(self, error: BaseException) -> None:
        super().__init__()
        self._error = error

    async def locate(self, nome, uf):
        raise self._error


def _wrong_uf_client() -> FakeTripAdvisorClient:
    # TA says Mato Grosso, no city: only the in-UF fallbacks could place the card.
    return FakeTripAdvisorClient(fixture_address={555: _geo(None, "State of Mato Grosso")})


# ---------------------------------------------------------------------------
# C1 — a verdict reached while a resolver was off/failing is not final
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kw",
    [
        {"places_agent": _RaisingLocateAgent(RuntimeError("503"))},
        {"places_agent": _FailingLocateAgent()},
        {"places_agent": None, "geocoder": _BannedGeocoder()},
        {"places_agent": None},  # Places off (no agent built)
    ],
    ids=["locate-raises", "locate-failed", "places-off+nominatim-banned", "places-off"],
)
async def test_unconfirmed_without_a_real_answer_keeps_the_record_pending(kw) -> None:
    ingest = _ingest(_wrong_uf_client(), **kw)
    rio = _bulk_rio()

    status = await ingest.backfill_record(rio)

    assert status == "municipio_pendente"
    assert "ta_backfilled_at" not in rio.normalized  # the next backfill retries it
    assert rio.routing == "dlq" and rio.dlq_reason == "municipio_nao_confirmado"
    assert rio.normalized["ta_uf"] == "MT" and rio.uf == "MG"


@pytest.mark.asyncio
async def test_pending_record_resolved_later_clears_our_verdict() -> None:
    rio = _bulk_rio()
    await _ingest(_wrong_uf_client(), places_agent=None).backfill_record(rio)
    assert rio.dlq_reason == "municipio_nao_confirmado"

    # Places back on: locate places it in Chapada dos Guimarães/MT.
    agent = _Agent(place={"place_id": "ChIJ-x", "municipio_ibge": "5103007", "location": {}})
    ingest = _ingest(_wrong_uf_client(), places_agent=agent)
    with _ensure(ingest):
        status = await ingest.backfill_record(rio)

    assert status == "ta+places"
    assert rio.uf == "MT" and rio.normalized["ta_backfilled_at"]
    assert rio.dlq_reason != "municipio_nao_confirmado"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [CostGuardError("cap"), ProviderBalanceError("google_places"), SoftTimeLimitExceeded(),
     SessionExpiredError("x")],
    ids=lambda e: type(e).__name__,
)
async def test_run_level_errors_escape_the_places_locate_fallback(error) -> None:
    ingest = _ingest(_wrong_uf_client(), places_agent=_RaisingLocateAgent(error))
    rio = _bulk_rio()

    with pytest.raises(type(error)):
        await ingest.backfill_record(rio)
    assert "ta_backfilled_at" not in rio.normalized


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error", [SessionExpiredError("x"), SoftTimeLimitExceeded(), CostGuardError("x")],
    ids=lambda e: type(e).__name__,
)
async def test_run_level_errors_escape_the_nominatim_fallback(error) -> None:
    class _Geo(FakeGeocoderClient):
        async def geocode(self, location_id, name, uf):
            raise error

    ingest = _ingest(_wrong_uf_client(), places_agent=_Agent(), geocoder=_Geo())
    rio = _bulk_rio()

    with pytest.raises(type(error)):
        await ingest.backfill_record(rio)
    assert "ta_backfilled_at" not in rio.normalized


# ---------------------------------------------------------------------------
# Parent linkage without usable TA geo
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "geo", [None, _geo("X", "Atlantis")], ids=["no-geo", "unknown-state"]
)
async def test_bulk_record_gets_its_parent_even_without_usable_ta_geo(geo) -> None:
    client = FakeTripAdvisorClient(fixture_address={555: geo} if geo else {})
    ingest = _ingest(client)
    rio = _bulk_rio()

    with _ensure(ingest) as ensure:
        status = await ingest.backfill_record(rio)

    assert status == "ta"
    assert rio.normalized["parent_rio_id"] == str(_PARENT[0])
    assert rio.normalized["parent_source_ref"] == _PARENT[1]
    assert ensure.call_args.args[0].ibge_code == "3170107"


@pytest.mark.asyncio
async def test_unconfirmed_record_is_never_linked_to_the_wrong_parent() -> None:
    ingest = _ingest(_wrong_uf_client(), places_agent=_Agent(place=None))
    rio = _bulk_rio()

    with _ensure(ingest) as ensure:
        assert await ingest.backfill_record(rio) == "municipio_nao_confirmado"
    assert not ensure.called and not rio.normalized.get("parent_rio_id")


# ---------------------------------------------------------------------------
# Same-UF: TA's cityName (its parent geo) never beats the record's own evidence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_same_uf_city_does_not_move_a_record_with_a_place_id() -> None:
    # Enriched per-UF record in Uberlândia/MG; TA groups it under another MG town.
    client = FakeTripAdvisorClient(fixture_address={555: _geo("Uberlandia", "State of Minas Gerais")})
    ingest = _ingest(client)
    rio = _bulk_rio(google_enriched=True, place_id_cache="ChIJ-good",
                    municipio_id="3170107", parent_rio_id="p")
    # Pretend the stored município is another MG seat far from TA's city: no coords.
    from brave.domains.tripadvisor.ibge import IbgeMunicipio

    other = IbgeMunicipio("3100000", "Outra", "MG", -21.0, -44.0)
    ingest._ibge_records = [*ingest._ibge_records, other]
    rio.municipio_id = "3100000"
    rio.normalized.update(lat=None, lon=None, municipio_id="3100000")

    await ingest.backfill_record(rio)

    assert rio.municipio_id == "3100000"  # kept
    n = rio.normalized
    assert n["place_id_cache"] == "ChIJ-good" and n["google_enriched"] is True


@pytest.mark.asyncio
async def test_same_uf_city_does_not_move_a_record_whose_coords_sit_near_it() -> None:
    from brave.domains.tripadvisor.ibge import IbgeMunicipio

    # Coords ~20 km from Uberlândia's seat, stored in a neighbour whose seat is nearer.
    neighbour = IbgeMunicipio("3100001", "Vizinha", "MG", -18.95, -48.10)
    client = FakeTripAdvisorClient(fixture_address={555: _geo("Uberlandia", "State of Minas Gerais")})
    ingest = _ingest(client)
    ingest._ibge_records = [*ingest._ibge_records, neighbour]
    rio = _bulk_rio(municipio_id="3100001", lat=-18.96, lon=-48.12, parent_rio_id="p")
    rio.municipio_id = "3100001"

    await ingest.backfill_record(rio)

    assert rio.municipio_id == "3100001"
    assert (rio.normalized["lat"], rio.normalized["lon"]) == (-18.96, -48.12)


@pytest.mark.asyncio
async def test_same_uf_city_moves_a_record_whose_coords_are_a_far_homonym() -> None:
    from brave.domains.tripadvisor.ibge import IbgeMunicipio

    far = IbgeMunicipio("3100002", "Longe", "MG", -15.0, -42.0)  # ~700 km away
    client = FakeTripAdvisorClient(fixture_address={555: _geo("Uberlandia", "State of Minas Gerais")})
    ingest = _ingest(client)
    ingest._ibge_records = [*ingest._ibge_records, far]
    rio = _bulk_rio(municipio_id="3100002", lat=-15.01, lon=-42.01)
    rio.municipio_id = "3100002"

    with _ensure(ingest):
        await ingest.backfill_record(rio)

    assert rio.municipio_id == "3170107"
    assert rio.normalized["lat"] is None


# ---------------------------------------------------------------------------
# A move drops everything written for the old place
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_move_drops_stale_google_fields_and_description() -> None:
    client = FakeTripAdvisorClient(
        fixture_address={555: _geo("Chapada dos Guimaraes", "State of Mato Grosso")}, recent_review=None
    )
    ingest = _ingest(client)
    rio = _bulk_rio(
        google_enriched=True, place_id_cache="ChIJ-homonym", weekday_text=["seg: 8-18"],
        phone="+55 34 0000", website="http://x", address="Rua X, Uberlândia", price_level="1",
        signal={"business_status": "OPERATIONAL"}, google_fetched_at="2026-01-01",
        most_recent_review_at="2026-10-01T00:00:00+00:00", atualidade_value=100.0,
        descricao_editorial="Texto ancorado em Uberlândia.", descricao_rascunho="r",
        descricao_attempts=2,
    )

    with _ensure(ingest):
        await ingest.backfill_record(rio)

    n = rio.normalized
    assert rio.uf == "MT"
    for key in ("place_id_cache", "weekday_text", "phone", "website", "address", "price_level",
                "signal", "google_fetched_at", "most_recent_review_at",
                "descricao_editorial", "descricao_rascunho"):
        assert n[key] is None, key
    assert n["atualidade_value"] == 0.0 and n["descricao_attempts"] == 0
    assert n["google_enriched"] is False


@pytest.mark.asyncio
async def test_move_recounts_completude_and_drops_the_old_description_verdict() -> None:
    """The description had raised completude to 90 and the homonym's coords counted too:
    both are gone after the move, so the score must not keep counting them."""
    client = FakeTripAdvisorClient(
        fixture_address={555: _geo("Chapada dos Guimaraes", "State of Mato Grosso")}, recent_review=None
    )
    session = MagicMock()
    session.get.return_value = MagicMock(payload={
        "name": "Cachoeira Véu de Noiva", "locationId": "555", "lat": -18.92, "lng": -48.28,
        "rating": 4.5, "review_count": 100, "category": "Cachoeiras",
    })
    ingest = _ingest(client, session=session)
    rio = _bulk_rio(completude_value=90.0, descricao_editorial="Texto.", descricao_gate="ok",
                    descricao_groundedness=0.9)

    with _ensure(ingest):
        await ingest.backfill_record(rio)

    n = rio.normalized
    assert rio.uf == "MT"
    # name, uf, location_id, rating, review_count, category — no coords, no description.
    assert n["completude_value"] == 60.0
    assert n["descricao_gate"] is None and n["descricao_groundedness"] is None


# ---------------------------------------------------------------------------
# TA's own page: its point and contact are a floor under Google's
# ---------------------------------------------------------------------------

_CHAPADA_PAGE = {
    "city_name": "Chapada dos Guimarães", "state_name": "Mato Grosso",
    "lat": -15.43, "lng": -55.83, "address": "MT-251, Chapada dos Guimarães, 78195-000",
    "phone": "+55 65 3301 1133", "website": "https://www.icmbio.gov.br",
}


@pytest.mark.asyncio
async def test_backfill_move_takes_the_ta_page_point_and_contact_floor() -> None:
    session = MagicMock()
    session.get.return_value = MagicMock(payload={
        "name": "Cachoeira Véu de Noiva", "locationId": "555", "rating": 4.5,
        "review_count": 100, "category": "Cachoeiras",
    })
    ingest = _ingest(FakeTripAdvisorClient(fixture_address={555: _CHAPADA_PAGE}),
                     session=session)
    rio = _bulk_rio(google_enriched=True, place_id_cache="ChIJ-homonym", phone="+55 34 0000",
                    address="Rua X, Uberlândia")

    with _ensure(ingest):
        await ingest.backfill_record(rio)

    n = rio.normalized
    assert rio.uf == "MT" and rio.municipio_id == "5103007"
    assert (n["lat"], n["lon"]) == (-15.43, -55.83)
    assert n["place_id_cache"] is None
    assert n["address"] == _CHAPADA_PAGE["address"] and n["phone"] == "+55 65 3301 1133"
    assert n["website"] == "https://www.icmbio.gov.br"
    # name, uf, location_id, lat, lng, rating, review_count, address, category.
    assert n["completude_value"] == 90.0


@pytest.mark.asyncio
async def test_backfill_move_prefers_the_ta_point_over_nominatim() -> None:
    page = {"city_name": "Lugar Nenhum", "state_name": "Mato Grosso", "lat": -16.3, "lng": -56.6}
    geocoder = FakeGeocoderClient(fixture_results={"555": {
        "lat": -16.26, "lon": -56.62, "osm_id": 1, "municipio_name": "Poconé"}})
    ingest = _ingest(FakeTripAdvisorClient(fixture_address={555: page}), geocoder=geocoder)
    rio = _bulk_rio()

    with _ensure(ingest):
        await ingest.backfill_record(rio)

    assert rio.municipio_id == "5106505"
    assert (rio.normalized["lat"], rio.normalized["lon"]) == (-16.3, -56.6)


@pytest.mark.asyncio
async def test_backfill_kept_record_without_place_id_gets_the_ta_point() -> None:
    # The bulk's name geocode put it in Uberlândia (-18.92, -48.28); TA agrees on the
    # município and has its own point.
    page = {"city_name": "Uberlandia", "state_name": "Minas Gerais", "lat": -18.95, "lng": -48.3}
    ingest = _ingest(FakeTripAdvisorClient(fixture_address={555: page}))
    rio = _bulk_rio(parent_rio_id="p")

    await ingest.backfill_record(rio)

    assert rio.municipio_id == "3170107"
    assert (rio.normalized["lat"], rio.normalized["lon"]) == (-18.95, -48.3)


@pytest.mark.asyncio
async def test_backfill_floor_never_overwrites_google_fields() -> None:
    page = {"city_name": "Uberlandia", "state_name": "Minas Gerais", "lat": -18.95, "lng": -48.3,
            "address": "Rua TA, 1", "phone": "+55 34 2222"}
    ingest = _ingest(FakeTripAdvisorClient(fixture_address={555: page}))
    rio = _bulk_rio(parent_rio_id="p", place_id_cache="ChIJ-good", phone="+55 34 1111")

    await ingest.backfill_record(rio)

    n = rio.normalized
    assert (n["lat"], n["lon"]) == (-18.92, -48.28) and n["phone"] == "+55 34 1111"
    assert n["address"] == "Rua TA, 1"  # empty before: the floor fills it


@pytest.mark.asyncio
async def test_backfill_page_without_block_withholds_the_marker() -> None:
    from brave.shared.exceptions import SourceRecordError

    client = FakeTripAdvisorClient(address_error=SourceRecordError("no block"))
    ingest = _ingest(client)
    rio = _bulk_rio(parent_rio_id="p")

    assert await ingest.backfill_record(rio) == "ta"
    assert client.address_calls == [555] and client.recent_review_calls == [555]
    assert "ta_backfilled_at" not in rio.normalized  # the next backfill redoes the page


@pytest.mark.asyncio
async def test_backfill_off_coords_are_not_written() -> None:
    # Locality Chapada dos Guimarães, point next to Poconé's seat (~128 km away).
    page = {**_CHAPADA_PAGE, "lat": -16.26, "lng": -56.62}
    ingest = _ingest(FakeTripAdvisorClient(fixture_address={555: page}))
    rio = _bulk_rio()

    with _ensure(ingest):
        await ingest.backfill_record(rio)

    assert rio.municipio_id == "5103007"
    assert rio.normalized["lat"] is None and rio.normalized["lon"] is None


# ---------------------------------------------------------------------------
# C5 — the backfill re-score keeps deliberate verdicts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["closed_temporarily", "descricao_nao_fundamentada"])
async def test_backfill_rescore_keeps_a_deliberate_dlq_reason(reason) -> None:
    client = FakeTripAdvisorClient(fixture_address={555: _geo("Uberlandia", "State of Minas Gerais")})
    ingest = _ingest(client)
    rio = _bulk_rio(google_enriched=True, parent_rio_id="p", origem_value=100.0,
                    completude_value=100.0, corroboracao_value=100.0, atualidade_value=100.0,
                    validacao_humana_value=100.0)  # would score into Mar
    rio.dlq_reason = reason

    await ingest.backfill_record(rio)

    assert rio.routing == "dlq" and rio.dlq_reason == reason


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["steward_sent_to_review", "community_error_report"])
async def test_unplaced_hold_keeps_another_deliberate_verdict(reason) -> None:
    """TA puts the card in a UF nobody confirms: the hold must not overwrite a steward /
    community verdict (a later placement would then clear it and lose it for good)."""
    ingest = _ingest(_wrong_uf_client(), places_agent=None)
    rio = _bulk_rio()
    rio.dlq_reason = reason

    assert await ingest.backfill_record(rio) == "municipio_pendente"

    assert rio.routing == "dlq" and rio.dlq_reason == reason


@pytest.mark.asyncio
async def test_unplaced_hold_never_resurrects_a_descarte() -> None:
    """Discarded (e.g. CLOSED_PERMANENTLY) between the batch select and the row lock."""
    ingest = _ingest(_wrong_uf_client(), places_agent=None)
    rio = _bulk_rio()
    rio.routing, rio.dlq_reason = "descarte", "closed_permanently"

    await ingest.backfill_record(rio)

    assert rio.routing == "descarte" and rio.dlq_reason == "closed_permanently"


# ---------------------------------------------------------------------------
# The Places step runs in a savepoint — its failure leaves the TA side intact
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_places_step_runs_in_a_savepoint() -> None:
    session = MagicMock()
    client = FakeTripAdvisorClient(fixture_address={555: _geo("Uberlandia", "State of Minas Gerais")})
    ingest = _ingest(client, session=session, places_agent=_Agent(run_error=CostGuardError("cap")))
    rio = _bulk_rio(parent_rio_id="p")

    with pytest.raises(CostGuardError):
        await ingest.backfill_record(rio)

    assert session.begin_nested.call_count == 1
    exit_args = session.begin_nested.return_value.__exit__.call_args.args
    assert exit_args[0] is CostGuardError  # only the savepoint is rolled back
    assert rio.normalized["ta_backfilled_at"]  # the caller commits the TA side


# ---------------------------------------------------------------------------
# Bulk: cards the name-only national geocode misses still reach Rio via TA geo
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_unmatched_card_is_placed_by_ta_geo() -> None:
    client = FakeTripAdvisorClient(fixture_geo={9: _geo("Chapada dos Guimaraes",
                                                        "State of Mato Grosso")})
    geo = FakeGeocoderClient(fixture_national_results={})  # national geocode: nothing
    with (
        patch(f"{_MOD}.store_raw") as store,
        patch(f"{_MOD}.quarantine_poison") as poison,
    ):
        ingest = _ingest(client, geocoder=geo)
        with _ensure(ingest):
            assert await ingest._ingest_one_bulk(_card(9, name="Passeio de Barco"),
                                                 run_rio=False)

    assert not poison.called
    assert client.geo_calls == [9]
    assert store.call_args.kwargs["uf"] == "MT"
    assert store.call_args.kwargs["payload"]["municipio_id"] == "5103007"


@pytest.mark.asyncio
async def test_bulk_unmatched_card_is_placed_by_nominatim_inside_ta_uf() -> None:
    client = FakeTripAdvisorClient(fixture_geo={9: _geo(None, "State of Mato Grosso")})
    geo = FakeGeocoderClient(
        fixture_national_results={},
        fixture_results={"9": {"lat": -16.26, "lon": -56.62, "osm_id": 1,
                               "municipio_name": "Poconé"}},
    )
    with patch(f"{_MOD}.store_raw") as store:
        ingest = _ingest(client, geocoder=geo)
        with _ensure(ingest):
            assert await ingest._ingest_one_bulk(_card(9, name="Trilha X"), run_rio=False)

    payload = store.call_args.kwargs["payload"]
    assert payload["municipio_id"] == "5106505"
    assert (payload["lat"], payload["lng"]) == (-16.26, -56.62)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "geocoder", [FakeGeocoderClient(fixture_national_results={}), None],
    ids=["nominatim-answered", "no-geocoder"],
)
async def test_bulk_card_the_whole_chain_missed_is_stamped_for_bulk_skip(geocoder):
    from brave.domains.tripadvisor.atrativos import IBGE_RESOLVER_VERSION

    client = FakeTripAdvisorClient(fixture_geo={9: _geo(None, "State of Mato Grosso")})
    with patch(f"{_MOD}.quarantine_poison") as poison:
        ingest = _ingest(client, geocoder=geocoder)
        assert not await ingest._ingest_one_bulk(_card(9, name="Trilha X"), run_rio=False)

    payload = poison.call_args.kwargs["payload"]
    assert payload["bulk_resolver_version"] == IBGE_RESOLVER_VERSION
    assert "resolver_version" not in payload  # the per-UF sweep (with Places) still retries


@pytest.mark.asyncio
async def test_bulk_card_missed_while_nominatim_failed_is_retried() -> None:
    client = FakeTripAdvisorClient(fixture_geo={9: _geo(None, "State of Mato Grosso")})

    class _Geo(_BannedGeocoder):
        async def geocode_national(self, location_id, name):
            return None

    with patch(f"{_MOD}.quarantine_poison") as poison:
        ingest = _ingest(client, geocoder=_Geo())
        assert not await ingest._ingest_one_bulk(_card(9, name="Trilha X"), run_rio=False)
    assert poison.call_args.kwargs["payload"]["bulk_resolver_version"] == 0


@pytest.mark.asyncio
async def test_bulk_skips_cards_its_chain_already_missed() -> None:
    """produce_paginated asks _already_synced for the bulk stamp too (convergence)."""
    session = MagicMock()
    session.scalars.side_effect = [[], ["9"]]  # not in Rio; quarantined under the stamp
    client = FakeTripAdvisorClient(gql_pages=[(0, [_card(9)])])
    ingest = _ingest(client, session=session, geocoder=FakeGeocoderClient())

    await ingest.produce_paginated(294280, 1, 1, fakeredis.FakeRedis(), run_rio=False)

    assert client.geo_calls == []
    from sqlalchemy.dialects import postgresql

    stmt = session.scalars.call_args_list[1].args[0]
    sql = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "bulk_resolver_version" in sql
