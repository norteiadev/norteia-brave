"""build_push_payload fixes that change what norteia-api receives.

- F43: a TripAdvisor ref ("tripadvisor:attraction:<id>") is not "source:UF:id" — the destino
  fallback must use the Rio's UF, never "ibge:attraction:<ibge>" (a ghost Destination).
- F93: no all-null ``place`` block (the API stamps enriched_at=now() for any non-empty one).
- C6: the TripAdvisor card category reaches ``type`` instead of "outros".
- F47: no ``visibility`` by default — a re-push must not undo a hide applied by the API
  (publish adds hidden/published only when Brave retracts / re-shows).
- A key Brave cleared (move off a homonym) is sent as null once the API holds a copy.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import brave.core.mar.service as ms
from brave.core.mar.service import build_push_payload, promote_to_mar, tipo_from_category


def _mar(canonical: dict, source_ref: str = "tripadvisor:attraction:2408107") -> SimpleNamespace:
    return SimpleNamespace(
        entity_type="attraction",
        source_ref=source_ref,
        canonical={"name": "Praia do Forte", "municipio_id": "2927408", **canonical},
        provenance={"score_breakdown": {}},
        reliability_score=88.0,
    )


def test_ta_bulk_row_falls_back_to_the_rio_uf_not_the_ref():
    payload = build_push_payload(
        _mar({"parent_source_ref": None, "municipio": "Porto Seguro"}),
        SimpleNamespace(uf="BA"),
    )

    assert payload["destino"]["source_ref"] == "ibge:BA:2927408"
    assert payload["destino"]["source"] == "ibge"


def test_no_uf_means_no_ghost_destino():
    payload = build_push_payload(_mar({"municipio": "Porto Seguro"}), rio_record=None)

    assert "destino" not in payload  # API resolves "ibge:<UF>:<ibge>" itself


def test_destino_without_a_name_is_omitted_not_422d():
    payload = build_push_payload(
        _mar({"parent_source_ref": "ibge:BA:2927408"}), SimpleNamespace(uf="BA")
    )

    assert "destino" not in payload


def test_lane_parent_source_ref_still_wins():
    payload = build_push_payload(
        _mar({"parent_source_ref": "ibge:BA:2927408", "municipio": "Porto Seguro"}),
        SimpleNamespace(uf="SP"),
    )

    assert payload["destino"]["source_ref"] == "ibge:BA:2927408"


def test_per_uf_ref_still_yields_its_uf_without_a_rio():
    payload = build_push_payload(
        _mar({"municipio": "Porto Seguro"}, source_ref="places:BA:ChIJx"), rio_record=None
    )

    assert payload["destino"]["source_ref"] == "ibge:BA:2927408"


def test_place_block_keeps_only_real_values():
    payload = build_push_payload(_mar({"google_place_id": "ChIJx"}), SimpleNamespace(uf="BA"))

    assert payload["place"] == {"place_id": "ChIJx"}


def test_no_places_data_means_no_place_block():
    payload = build_push_payload(_mar({}), SimpleNamespace(uf="BA"))

    assert "place" not in payload


def test_a_plain_push_never_overrides_the_api_visibility():
    """Every re-score re-pushes: an explicit "published" re-showed what the API hid."""
    assert "visibility" not in build_push_payload(_mar({}), None)
    destination = SimpleNamespace(
        entity_type="destination", source_ref="ibge:BA:2927408",
        canonical={"name": "Porto Seguro", "ibge_code": "2927408"},
        provenance={}, reliability_score=90.0,
    )
    assert "visibility" not in build_push_payload(destination, None)


_MOVED = {  # what _apply_ta_geo leaves after moving a record off a homonym
    "google_place_id": None, "place_id_cache": None, "weekday_text": None, "signal": None,
    "distrito_name": None, "distrito_code": None, "price_level": None, "address": None,
}


def test_cleared_google_keys_reach_an_api_that_holds_a_copy():
    mar = _mar(_MOVED)
    mar.push_hash = "abc"  # the API accepted an earlier (homonym) version

    payload = build_push_payload(mar, SimpleNamespace(uf="MT"))

    assert payload["place"] == {
        "place_id": None, "business_status": None, "opening_hours": None,
        "price_level": None, "reviews_recent_count": None,
        "distrito_code": None, "distrito_name": None,
    }
    assert payload["opening_hours"] == {}  # overwrites the API's hours map


def test_cleared_keys_are_not_sent_before_the_api_has_anything():
    payload = build_push_payload(_mar(_MOVED), SimpleNamespace(uf="MT"))

    assert "place" not in payload and "opening_hours" not in payload  # F93 still holds


@pytest.mark.parametrize(
    ("category", "tipo"),
    [
        ("Praias", "praia"),
        ("Cachoeiras", "cachoeira"),
        ("Waterfalls", "cachoeira"),
        ("Museus especializados", "museu"),
        ("Igrejas e catedrais", "centro_historico"),
        ("Trilhas para caminhada • Cachoeiras", "trilha"),
        ("Parques aquáticos", "outros"),  # a water park is not a parque
        ("Parques aquáticos • Cachoeiras", "cachoeira"),
        ("Adegas e vinícolas", "experiencia_gastronomica"),
        ("Shopping centers", "outros"),
        ("", "outros"),
        (None, "outros"),
    ],
)
def test_ta_category_maps_onto_the_tipo_vocabulary(category, tipo):
    assert tipo_from_category(category) == tipo


def test_ta_category_reaches_the_push_type():
    assert build_push_payload(_mar({"category": "Praias"}), None)["type"] == "praia"
    # A lane tipo (Places/discovery) is more specific than the card category.
    assert build_push_payload(_mar({"tipo": "museu", "category": "Praias"}), None)["type"] == "museu"


# ---------------------------------------------------------------------------
# promote_to_mar: internal backfill keys stay out of the canonical; injectable now (F41)
# ---------------------------------------------------------------------------


def _rio(normalized: dict) -> SimpleNamespace:
    return SimpleNamespace(
        id="00000000-0000-0000-0000-000000000001", nascente_id="n", canonical_key="tripadvisor:attraction:1",
        entity_type="attraction", normalized=normalized, score=90.0, score_version="v1.0",
        score_breakdown={}, routing="mar", dlq_reason=None,
    )


def test_backfill_markers_are_not_canonical():
    recent = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    rio = _rio({
        "name": "Praia X", "most_recent_review_at": recent, "ta_backfilled_at": recent,
        "ta_uf": "BA", "google_fetched_at": recent, "descricao_batch_expiries": 1,
        "category": "Praias",
    })

    with patch.object(ms, "_mar_repo") as repo:
        repo.get_active_by_source_ref.return_value = None
        mar = promote_to_mar(MagicMock(), rio)

    assert not {"ta_backfilled_at", "ta_uf", "google_fetched_at",
                "descricao_batch_expiries"} & set(mar.canonical)
    assert mar.canonical["category"] == "Praias"  # the push maps it to `type`


def test_recency_backstop_reads_the_injected_now():
    rio = _rio({"name": "Praia X", "most_recent_review_at": "2026-01-01T00:00:00+00:00"})

    with patch.object(ms, "_mar_repo") as repo:
        repo.get_active_by_source_ref.return_value = None
        assert promote_to_mar(MagicMock(), rio, now=datetime(2026, 1, 15, tzinfo=UTC))
        assert promote_to_mar(MagicMock(), rio, now=datetime(2026, 9, 1, tzinfo=UTC)) is None
