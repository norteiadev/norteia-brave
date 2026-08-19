"""`participates_mtur` survives the whole path, seeded CSV → norteia-api.

The MTur categorization has been in the repo since the destino-seed lane was retired
(data/mtur/municipios_mtur_2025.csv → municipios.categoria, 2922 of 5571 rows), but it
stopped there: no destino canonical carried it and the push payload never sent it, so
`destinations.participates_mtur` stayed at its `false` default for every município in
the Mapa do Turismo.

Two links, tested here:
  ensure_destino  — reads municipios.categoria and stamps the canonical.
  build_push_payload — sends the flag for entity_type="destination".

The third link is in the Laravel repo: without a rule in IngestDestinationRequest the
controller's `$request->validated()` drops the key silently, with no error.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from brave.core.mar.service import build_push_payload


def _destino_mar(canonical: dict) -> SimpleNamespace:
    return SimpleNamespace(
        entity_type="destination",
        source_ref="ibge:BA:2927408",
        canonical={"name": "Porto Seguro", "ibge_code": "2927408", **canonical},
        provenance={"score_breakdown": {}},
        reliability_score=87.5,
    )


# --------------------------------------------------------------------------- push


def test_payload_sends_the_flag_for_a_mapa_do_turismo_municipio():
    payload = build_push_payload(_destino_mar({"participates_mtur": True}), rio_record=None)
    assert payload["participates_mtur"] is True


def test_payload_sends_false_and_never_null_when_the_canonical_is_silent():
    """The API column is `boolean default false` — a null would 422 or wipe it.

    A canonical with no key at all is the state of every destino created before this
    change, so the coercion is what keeps a re-push of an old record valid.
    """
    payload = build_push_payload(_destino_mar({}), rio_record=None)
    assert payload["participates_mtur"] is False


def test_attraction_payload_does_not_carry_the_flag():
    """It is a destination column; sending it on an attraction would 422 on an unknown key."""
    attraction = SimpleNamespace(
        entity_type="attraction",
        source_ref="tripadvisor:attraction:1",
        canonical={"name": "Cachoeira", "municipio_id": "2927408", "participates_mtur": True},
        provenance={"score_breakdown": {}},
        reliability_score=87.5,
    )
    assert "participates_mtur" not in build_push_payload(attraction, rio_record=None)


# ------------------------------------------------------------------- ensure_destino


def _session_returning(row):
    """Session mock whose Municipio query yields `row` (a (categoria, regiao) tuple or None)."""
    session = MagicMock()
    session.query.return_value.filter.return_value.one_or_none.return_value = row
    # The parent_mar_id lookup at the end of ensure_destino goes through .scalar().
    session.query.return_value.filter.return_value.scalar.return_value = None
    return session


def _canonical_from_ensure_destino(row) -> dict:
    from brave.shared import destino as destino_mod

    session = _session_returning(row)
    captured: dict = {}

    def _fake_store_raw(**kwargs):
        captured.update(kwargs["payload"]["canonical"])
        return MagicMock()

    with (
        patch.object(destino_mod, "store_raw", side_effect=_fake_store_raw),
        patch.object(destino_mod, "process_nascente_record", return_value=MagicMock()),
    ):
        destino_mod.ensure_destino(
            session,
            MagicMock(),
            ibge_code="2927408",
            nome="Porto Seguro",
            uf="BA",
        )
    return captured


def test_ensure_destino_stamps_the_mtur_signal_from_the_reference_table():
    canonical = _canonical_from_ensure_destino(("Alto", "Costa do Descobrimento"))

    assert canonical["participates_mtur"] is True
    assert canonical["mtur_categoria"] == "Alto"
    assert canonical["regiao_turistica"] == "Costa do Descobrimento"


def test_a_municipio_outside_the_mapa_do_turismo_is_false_not_null():
    """2649 of 5571 IBGE municípios have no MTur row — absence is a real False, not unknown."""
    canonical = _canonical_from_ensure_destino((None, None))

    assert canonical["participates_mtur"] is False
    assert canonical["mtur_categoria"] is None


def test_an_unseeded_reference_table_degrades_to_false_instead_of_raising():
    """A DB where seed_reference_data.py never ran must not break destino creation."""
    canonical = _canonical_from_ensure_destino(None)

    assert canonical["participates_mtur"] is False
    assert canonical["regiao_turistica"] is None
