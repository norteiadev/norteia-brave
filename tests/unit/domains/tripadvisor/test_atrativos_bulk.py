"""Unit tests for the TripAdvisor BULK national ingest path (plan 15-06, TA-12).

The bulk lane resolves the operator-locked A1 blocker: it writes all-Brazil
attractions to Nascente without per-UF context, deriving uf + município from the
national geocode (geocode_national) + nearest-IBGE-seat resolution
(resolve_municipio_national). This is a DISTINCT path:

  - _ingest_one_bulk    — single-card ingest; links the parent destino of the derived
                          município (cached per sweep, F43-b)
  - produce_paginated   — drives fetch_attractions_paginated, commits PER PAGE,
                          records progress + a live error counter

The existing per-UF _ingest_one parent-linkage path stays byte-for-byte intact
(asserted by git diff in the plan acceptance, not here).

All tests are 100% offline: RUN_REAL_EXTERNALS unset, fake client + fake geocoder
+ fakeredis; store_raw / process_nascente_record are monkeypatched (no DB).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import MagicMock, patch

import fakeredis
import pytest
from pydantic import ValidationError

from brave.config.settings import ScoreConfig
from brave.domains.tripadvisor import sweep_progress
from brave.domains.tripadvisor.client import SessionExpiredError
from brave.domains.tripadvisor.ibge import IbgeMunicipio
from brave.domains.tripadvisor.schemas import TripAdvisorReviewSignals
from tests.fakes.fake_nominatim import FakeGeocoderClient
from tests.fakes.fake_tripadvisor import FakeTripAdvisorClient


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

# Uberlândia (MG) IBGE seat — bulk-path target município.
_IBGE_RECORDS = [
    IbgeMunicipio("3170107", "Uberlândia", "MG", -18.9186, -48.2772),
    IbgeMunicipio("3550308", "São Paulo", "SP", -23.5505, -46.6333),
]

_GEO_ID_BR = 294280  # all-Brazil geoId

_PARENT_ID = uuid.uuid4()


def _real_ensure():
    from brave.domains.tripadvisor.atrativos import TripAdvisorAtrativosIngest

    return TripAdvisorAtrativosIngest._ensure_destino


_REAL_ENSURE = _real_ensure()  # captured before the autouse fixture patches it


@pytest.fixture(autouse=True)
def ensure_destino_mock():
    """The bulk links the parent destino via _ensure_destino (lookup + ensure) — DB-free."""
    from brave.domains.tripadvisor.atrativos import TripAdvisorAtrativosIngest

    with patch.object(
        TripAdvisorAtrativosIngest,
        "_ensure_destino",
        side_effect=lambda m: (_PARENT_ID, f"ibge:{m.uf}:{m.ibge_code}"),
    ) as mock:
        yield mock


def _make_config() -> ScoreConfig:
    return ScoreConfig(
        weight_origem=30.0,
        weight_completude=20.0,
        weight_corroboracao=20.0,
        weight_atualidade=15.0,
        weight_validacao_humana=15.0,
        threshold_mar=85.0,
        score_version="v1.1",
    )


def _make_card(
    location_id: int = 312332,
    name: str = "Parque do Sabiá",
    review_count: int = 100,
    rating: float = 4.0,
    category: str = "Parks",
) -> dict[str, Any]:
    """A normalized AttractionsFusion listing card (no lat/lng — geocoded nationally)."""
    return {
        "locationId": location_id,
        "name": name,
        "review_count": review_count,
        "rating": rating,
        "category": category,
    }


def _geo_near_uberlandia(location_id: str) -> tuple[str, dict[str, Any]]:
    """A national geocode fixture entry resolving close to the Uberlândia seat."""
    return location_id, {
        "lat": -18.92,
        "lon": -48.28,
        "osm_id": 1,
        "municipio_name": "Uberlândia",
    }


# ---------------------------------------------------------------------------
# Task 1 — _ingest_one_bulk
# ---------------------------------------------------------------------------


class TestIngestBulk:
    """_ingest_one_bulk: parent-less Nascente ingest with derived uf/município."""

    @pytest.mark.asyncio
    async def test_ingest_bulk_writes_nascente_row_linked_to_parent(self) -> None:
        """A geocodable card → exactly one Nascente row, parent destino linked, derived uf."""
        from brave.domains.tripadvisor.atrativos import TripAdvisorAtrativosIngest

        card = _make_card(location_id=312332)
        fake_geo = FakeGeocoderClient(
            fixture_national_results=dict([_geo_near_uberlandia("312332")])
        )

        with (
            patch("brave.domains.tripadvisor.atrativos.store_raw") as mock_store_raw,
            patch("brave.domains.tripadvisor.atrativos.process_nascente_record"),
        ):
            mock_nascente = MagicMock()
            mock_nascente.id = uuid.uuid4()
            mock_store_raw.return_value = mock_nascente

            ingest = TripAdvisorAtrativosIngest(
                ta_client=FakeTripAdvisorClient(),
                session=MagicMock(),
                config=_make_config(),
                ibge_records=_IBGE_RECORDS,
                destino_rio_map={},
                geocoder=fake_geo,
            )
            await ingest._ingest_one_bulk(card, run_rio=False)

        assert mock_store_raw.call_count == 1, "exactly one Nascente row must be written"
        kwargs = mock_store_raw.call_args.kwargs
        assert kwargs["entity_type"] == "attraction"
        # uf is DERIVED from the matched IBGE record, not any input arg.
        assert kwargs["uf"] == "MG"
        payload = kwargs["payload"]
        # F43-b: the bulk links the parent destino exactly like the per-UF path.
        assert payload["parent_rio_id"] == str(_PARENT_ID)
        assert payload["parent_source_ref"] == "ibge:MG:3170107"
        assert payload["municipio_id"] == "3170107"
        assert payload["canonical"]["municipio"] == "Uberlândia"
        assert payload["canonical"]["uf"] == "MG"
        # National geocode used (not the per-UF geocode).
        assert fake_geo.geocode_national_calls == [
            {"location_id": "312332", "name": "Parque do Sabiá"}
        ]
        assert fake_geo.geocode_calls == []

    @pytest.mark.asyncio
    async def test_ingest_bulk_unresolvable_quarantines_ibge_unmatched(self) -> None:
        """Geocoder cannot resolve → ibge_unmatched quarantine, zero Nascente rows."""
        from brave.domains.tripadvisor.atrativos import TripAdvisorAtrativosIngest

        card = _make_card(location_id=999)
        fake_geo = FakeGeocoderClient(fixture_national_results={})  # all misses

        with (
            patch("brave.domains.tripadvisor.atrativos.store_raw") as mock_store_raw,
            patch("brave.domains.tripadvisor.atrativos.quarantine_poison") as mock_q,
        ):
            ingest = TripAdvisorAtrativosIngest(
                ta_client=FakeTripAdvisorClient(),
                session=MagicMock(),
                config=_make_config(),
                ibge_records=_IBGE_RECORDS,
                destino_rio_map={},
                geocoder=fake_geo,
            )
            await ingest._ingest_one_bulk(card, run_rio=False)

        assert not mock_store_raw.called, "no Nascente row for an unresolvable card"
        unmatched = [
            c for c in mock_q.call_args_list
            if c.kwargs.get("task_name") == "brave.ta.atrativos.ibge_unmatched"
        ]
        assert len(unmatched) == 1
        # The dropped parent gate must NEVER fire in the bulk path.
        parent_absent = [
            c for c in mock_q.call_args_list
            if c.kwargs.get("task_name") == "brave.ta.atrativos.parent_destino_absent"
        ]
        assert parent_absent == []

    @pytest.mark.asyncio
    async def test_ingest_bulk_no_ibge_seat_within_radius_quarantines(self) -> None:
        """Geocode succeeds but no IBGE seat within 50 km → ibge_unmatched, no row."""
        from brave.domains.tripadvisor.atrativos import TripAdvisorAtrativosIngest

        card = _make_card(location_id=555)
        # Geocode lands in the mid-Atlantic — far from every BR seat.
        fake_geo = FakeGeocoderClient(
            fixture_national_results={
                "555": {"lat": 0.0, "lon": -30.0, "osm_id": 9, "municipio_name": None}
            }
        )

        with (
            patch("brave.domains.tripadvisor.atrativos.store_raw") as mock_store_raw,
            patch("brave.domains.tripadvisor.atrativos.quarantine_poison") as mock_q,
        ):
            ingest = TripAdvisorAtrativosIngest(
                ta_client=FakeTripAdvisorClient(),
                session=MagicMock(),
                config=_make_config(),
                ibge_records=_IBGE_RECORDS,
                destino_rio_map={},
                geocoder=fake_geo,
            )
            await ingest._ingest_one_bulk(card, run_rio=False)

        assert not mock_store_raw.called
        unmatched = [
            c for c in mock_q.call_args_list
            if c.kwargs.get("task_name") == "brave.ta.atrativos.ibge_unmatched"
        ]
        assert len(unmatched) == 1

    def test_review_signals_reject_extra_fields_lgpd(self) -> None:
        """LGPD guard intact: review-author/text fields are rejected (extra='forbid')."""
        # Aggregate-only construction succeeds.
        TripAdvisorReviewSignals(review_count=10, rating=4.0)
        # Any author/text drift is rejected at parse time.
        with pytest.raises(ValidationError):
            TripAdvisorReviewSignals(
                review_count=10,
                rating=4.0,
                author="Jane Doe",  # type: ignore[call-arg]
                text="great place",  # type: ignore[call-arg]
            )


# ---------------------------------------------------------------------------
# Task 2 — produce_paginated
# ---------------------------------------------------------------------------


def _resolvable_geo_fixture(location_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Build a national-geocode fixture resolving every id near Uberlândia."""
    return {lid: _geo_near_uberlandia(lid)[1] for lid in location_ids}


class _RaisingPaginatedClient:
    """Fake client whose paginated iterator raises SessionExpiredError mid-run."""

    def __init__(self, pages_before_raise: int = 1) -> None:
        self._pages_before_raise = pages_before_raise

    async def fetch_attractions_paginated_gql(
        self, geo_id: int, start_page: int = 1, max_pages: int = 334
    ) -> AsyncIterator[tuple[int, list[dict[str, Any]]]]:
        for i in range(self._pages_before_raise):
            yield i * 30, [_make_card(location_id=1000 + i)]
        raise SessionExpiredError("TripAdvisor GraphQL returned 403 — session expired.")


class TestProducePaginated:
    """produce_paginated: per-page commit + progress + live error counter."""

    @pytest.mark.asyncio
    async def test_produce_paginated_ingests_all_pages(self) -> None:
        """2 pages × 30 cards → all ingested; progress shows pages_done/attractions/offset."""
        from brave.domains.tripadvisor.atrativos import TripAdvisorAtrativosIngest

        # Two pages of 30 cards; page 1 offset 0, page 2 offset 30.
        page1 = [_make_card(location_id=10_000 + i) for i in range(30)]
        page2 = [_make_card(location_id=20_000 + i) for i in range(30)]
        all_ids = [str(c["locationId"]) for c in page1 + page2]
        fake_client = FakeTripAdvisorClient(
            gql_pages=[(0, page1), (30, page2)]
        )
        fake_geo = FakeGeocoderClient(
            fixture_national_results=_resolvable_geo_fixture(all_ids)
        )
        rc = fakeredis.FakeRedis()
        sweep_progress.start(rc, pages_total=2)

        with (
            patch("brave.domains.tripadvisor.atrativos.store_raw") as mock_store_raw,
            patch("brave.domains.tripadvisor.atrativos.process_nascente_record"),
        ):
            mock_nascente = MagicMock()
            mock_nascente.id = uuid.uuid4()
            mock_store_raw.return_value = mock_nascente

            ingest = TripAdvisorAtrativosIngest(
                ta_client=fake_client,
                session=MagicMock(),
                config=_make_config(),
                ibge_records=_IBGE_RECORDS,
                destino_rio_map={},
                geocoder=fake_geo,
            )
            await ingest.produce_paginated(
                _GEO_ID_BR, start_page=1, max_pages=2, redis=rc, run_rio=False
            )

        assert mock_store_raw.call_count == 60, "all 60 cards must reach Nascente"
        snap = sweep_progress.get_progress(rc)
        assert snap["pages_done"] == 2
        assert snap["attractions_ingested"] == 60
        assert snap["current_offset"] == 30
        assert snap["error_count"] == 0
        assert sweep_progress.get_resume_offset(rc) == 30

    @pytest.mark.asyncio
    async def test_produce_paginated_commits_per_page_before_record_page(self) -> None:
        """Session.commit fires once per page, BEFORE record_page (resume integrity)."""
        from brave.domains.tripadvisor.atrativos import TripAdvisorAtrativosIngest

        page1 = [_make_card(location_id=30_000)]
        page2 = [_make_card(location_id=30_001)]
        all_ids = ["30000", "30001"]
        fake_client = FakeTripAdvisorClient(
            gql_pages=[(0, page1), (30, page2)]
        )
        fake_geo = FakeGeocoderClient(
            fixture_national_results=_resolvable_geo_fixture(all_ids)
        )
        rc = fakeredis.FakeRedis()
        mock_session = MagicMock()

        with (
            patch("brave.domains.tripadvisor.atrativos.store_raw") as mock_store_raw,
            patch("brave.domains.tripadvisor.atrativos.process_nascente_record"),
            patch(
                "brave.domains.tripadvisor.atrativos.sweep_progress.record_page"
            ) as mock_record_page,
        ):
            mock_nascente = MagicMock()
            mock_nascente.id = uuid.uuid4()
            mock_store_raw.return_value = mock_nascente

            # Order spy: commit must precede record_page within each page.
            manager = MagicMock()
            manager.attach_mock(mock_session.commit, "commit")
            manager.attach_mock(mock_record_page, "record_page")

            ingest = TripAdvisorAtrativosIngest(
                ta_client=fake_client,
                session=mock_session,
                config=_make_config(),
                ibge_records=_IBGE_RECORDS,
                destino_rio_map={},
                geocoder=fake_geo,
            )
            await ingest.produce_paginated(
                _GEO_ID_BR, start_page=1, max_pages=2, redis=rc, run_rio=False
            )

        assert mock_session.commit.call_count == 2, "one commit per page"
        assert mock_record_page.call_count == 2
        # Verify ordering: every record_page is immediately preceded by a commit.
        names = [c[0] for c in manager.mock_calls]
        assert names == ["commit", "record_page", "commit", "record_page"]

    @pytest.mark.asyncio
    async def test_produce_paginated_session_expiry_propagates(self) -> None:
        """SessionExpiredError from the client iterator is NOT swallowed."""
        from brave.domains.tripadvisor.atrativos import TripAdvisorAtrativosIngest

        fake_geo = FakeGeocoderClient(
            fixture_national_results=_resolvable_geo_fixture(["1000"])
        )
        rc = fakeredis.FakeRedis()

        with (
            patch("brave.domains.tripadvisor.atrativos.store_raw") as mock_store_raw,
            patch("brave.domains.tripadvisor.atrativos.process_nascente_record"),
        ):
            mock_store_raw.return_value = MagicMock(id=uuid.uuid4())
            ingest = TripAdvisorAtrativosIngest(
                ta_client=_RaisingPaginatedClient(pages_before_raise=1),
                session=MagicMock(),
                config=_make_config(),
                ibge_records=_IBGE_RECORDS,
                destino_rio_map={},
                geocoder=fake_geo,
            )
            with pytest.raises(SessionExpiredError):
                await ingest.produce_paginated(
                    _GEO_ID_BR, start_page=1, max_pages=334, redis=rc, run_rio=False
                )

    @pytest.mark.asyncio
    async def test_produce_paginated_unmatched_card_increments_error_count(self) -> None:
        """A card that cannot ingest (ibge_unmatched) increments the live error_count."""
        from brave.domains.tripadvisor.atrativos import TripAdvisorAtrativosIngest

        # One resolvable card + one unresolvable card on the same page.
        good = _make_card(location_id=40_000)
        bad = _make_card(location_id=40_001)
        fake_client = FakeTripAdvisorClient(
            gql_pages=[(0, [good, bad])]
        )
        # Only the good id resolves; the bad id is a national-geocode miss.
        fake_geo = FakeGeocoderClient(
            fixture_national_results=_resolvable_geo_fixture(["40000"])
        )
        rc = fakeredis.FakeRedis()
        sweep_progress.start(rc, pages_total=1)

        with (
            patch("brave.domains.tripadvisor.atrativos.store_raw") as mock_store_raw,
            patch("brave.domains.tripadvisor.atrativos.process_nascente_record"),
        ):
            mock_store_raw.return_value = MagicMock(id=uuid.uuid4())
            ingest = TripAdvisorAtrativosIngest(
                ta_client=fake_client,
                session=MagicMock(),
                config=_make_config(),
                ibge_records=_IBGE_RECORDS,
                destino_rio_map={},
                geocoder=fake_geo,
            )
            await ingest.produce_paginated(
                _GEO_ID_BR, start_page=1, max_pages=1, redis=rc, run_rio=False
            )

        snap = sweep_progress.get_progress(rc)
        assert snap["error_count"] > 0, (
            "ibge_unmatched failures must increment the live panel error counter "
            "(record_error wired, not orphaned)"
        )
        assert snap["attractions_ingested"] == 1, "only the resolvable card landed"
        assert mock_store_raw.call_count == 1


# ---------------------------------------------------------------------------
# F43-b — parent destino linkage on the bulk, one ensure_destino per município
# ---------------------------------------------------------------------------


class TestBulkParentDestino:
    @pytest.mark.asyncio
    async def test_parent_source_ref_uses_the_real_ensure_destino_format(self) -> None:
        """Unmocked ensure_destino (DB layer patched) → 'ibge:{UF}:{ibge7}'."""
        from brave.domains.tripadvisor.atrativos import TripAdvisorAtrativosIngest
        from brave.shared.destino import ensure_destino

        parent_rio = MagicMock(id=uuid.uuid4())
        session = MagicMock()
        fake_geo = FakeGeocoderClient(
            fixture_national_results=dict([_geo_near_uberlandia("312332")])
        )
        with (
            patch.object(TripAdvisorAtrativosIngest, "_ensure_destino", _REAL_ENSURE),
            patch("brave.domains.tripadvisor.atrativos.ensure_destino", ensure_destino),
            patch("brave.shared.destino.store_raw"),
            patch("brave.shared.destino.process_nascente_record", return_value=parent_rio),
            patch("brave.domains.tripadvisor.atrativos.store_raw") as mock_store_raw,
        ):
            ingest = TripAdvisorAtrativosIngest(
                ta_client=FakeTripAdvisorClient(),
                session=session,
                config=_make_config(),
                ibge_records=_IBGE_RECORDS,
                geocoder=fake_geo,
            )
            assert await ingest._ingest_one_bulk(_make_card(location_id=312332), run_rio=False)

        payload = mock_store_raw.call_args.kwargs["payload"]
        assert payload["parent_rio_id"] == str(parent_rio.id)
        assert payload["parent_source_ref"] == "ibge:MG:3170107"

    @pytest.mark.asyncio
    async def test_one_ensure_destino_per_municipio_across_the_run(
        self, ensure_destino_mock: MagicMock
    ) -> None:
        """30 cards in the same município → ensure_destino runs once, not 30 times."""
        from brave.domains.tripadvisor.atrativos import TripAdvisorAtrativosIngest

        cards = [_make_card(location_id=50_000 + i) for i in range(30)]
        ids = [str(c["locationId"]) for c in cards]
        with patch("brave.domains.tripadvisor.atrativos.store_raw"):
            ingest = TripAdvisorAtrativosIngest(
                ta_client=FakeTripAdvisorClient(gql_pages=[(0, cards)]),
                session=MagicMock(),
                config=_make_config(),
                ibge_records=_IBGE_RECORDS,
                geocoder=FakeGeocoderClient(fixture_national_results=_resolvable_geo_fixture(ids)),
            )
            await ingest.produce_paginated(
                _GEO_ID_BR, start_page=1, max_pages=1, redis=fakeredis.FakeRedis(),
                run_rio=False,
            )

        assert ensure_destino_mock.call_count == 1

    @pytest.mark.asyncio
    async def test_failed_card_evicts_the_destino_its_savepoint_rolled_back(
        self, ensure_destino_mock: MagicMock
    ) -> None:
        """Card 1 fails after ensuring the destino → card 2 re-ensures it (no ghost parent)."""
        from brave.domains.tripadvisor.atrativos import TripAdvisorAtrativosIngest

        cards = [_make_card(location_id=60_000), _make_card(location_id=60_001)]
        with (
            patch(
                "brave.domains.tripadvisor.atrativos.store_raw",
                side_effect=[RuntimeError("flush failed"), MagicMock()],
            ),
            patch("brave.domains.tripadvisor.atrativos.quarantine_poison"),
            patch("brave.domains.tripadvisor.atrativos.record_event_once"),
        ):
            ingest = TripAdvisorAtrativosIngest(
                ta_client=FakeTripAdvisorClient(gql_pages=[(0, cards)]),
                session=MagicMock(),
                config=_make_config(),
                ibge_records=_IBGE_RECORDS,
                geocoder=FakeGeocoderClient(
                    fixture_national_results=_resolvable_geo_fixture(["60000", "60001"])
                ),
            )
            await ingest.produce_paginated(
                _GEO_ID_BR, start_page=1, max_pages=1, redis=fakeredis.FakeRedis(),
                run_rio=False,
            )

        assert ensure_destino_mock.call_count == 2
        assert "3170107" in ingest._destino_rio_map  # card 2's (committed) parent is cached


class TestEnsureDestinoKeepsIbgeContract:
    """The parent link is always ibge:{UF}:{ibge7} — never another destino of the município.

    A manual sub-destino (manual:destination:{UF}:{ibge}) or a TA destino sharing the
    município_id must not capture its atrativos: the push sends parent_source_ref as the
    API's Destination source_ref, so a foreign ref would overwrite that destino's name or
    create a ghost Destination next to the ibge: one.
    """

    def test_other_destino_of_the_municipio_does_not_capture_the_link(self) -> None:
        from brave.domains.tripadvisor.atrativos import TripAdvisorAtrativosIngest

        ibge_id = uuid.uuid4()
        session = MagicMock()
        # Any lookup would find a manual destino of the same município.
        session.execute.return_value.first.return_value = (
            uuid.uuid4(), "manual:destination:MG:3170107",
        )
        ingest = TripAdvisorAtrativosIngest(
            ta_client=FakeTripAdvisorClient(), session=session, config=_make_config(),
            ibge_records=_IBGE_RECORDS,
        )
        with (
            patch.object(TripAdvisorAtrativosIngest, "_ensure_destino", _REAL_ENSURE),
            patch(
                "brave.domains.tripadvisor.atrativos.ensure_destino",
                return_value=(ibge_id, "ibge:MG:3170107", None),
            ) as ensure,
        ):
            assert ingest._parent_destino(_IBGE_RECORDS[0]) == (ibge_id, "ibge:MG:3170107")
        assert ensure.call_count == 1

    @pytest.mark.integration
    def test_db_links_ibge_destino_and_is_idempotent(self, db_session) -> None:
        from brave.core.models import RioRecord
        from brave.core.nascente.service import store_raw
        from brave.domains.tripadvisor.atrativos import TripAdvisorAtrativosIngest

        muni = IbgeMunicipio("9999901", "Cidade Teste", "ZZ", 0.0, 0.0)
        for key in ("manual:destination:ZZ:9999901", "tripadvisor:destination:123"):
            nascente = store_raw(db_session, "test", key, "destination", "ZZ", {"name": key})
            db_session.add(RioRecord(
                nascente_id=nascente.id, entity_type="destination", uf="ZZ",
                municipio_id=muni.ibge_code, routing="dlq", canonical_key=key, normalized={},
            ))
        db_session.flush()

        def fresh_ingest() -> TripAdvisorAtrativosIngest:  # empty cache, like bulk/backfill
            return TripAdvisorAtrativosIngest(
                ta_client=FakeTripAdvisorClient(), session=db_session, config=_make_config(),
                ibge_records=[muni],
            )

        with patch.object(TripAdvisorAtrativosIngest, "_ensure_destino", _REAL_ENSURE):
            rio_id, ref = fresh_ingest()._parent_destino(muni)
            again = fresh_ingest()._parent_destino(muni)
        assert ref == "ibge:ZZ:9999901"
        assert again == (rio_id, ref)  # no ibge: twin from a second empty-cache run
        assert db_session.query(RioRecord).filter(
            RioRecord.canonical_key == "ibge:ZZ:9999901"
        ).count() == 1
