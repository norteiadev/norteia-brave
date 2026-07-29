"""build_push_payload carries BOTH hours shapes for attractions.

- ``place.opening_hours`` — the raw Google lines, untouched (frozen by the Pact).
- top-level ``opening_hours`` — the language-neutral day→hours map, OMITTED (never
  null) when the lines don't parse, so a re-push can't wipe a curated value.
"""

from types import SimpleNamespace

from brave.core.mar.service import build_push_payload

_RAW_LINES = [
    "Monday: 9:00 AM – 5:00 PM",
    "Tuesday: 9:00 AM – 5:00 PM",
    "Wednesday: 9:00 AM – 5:00 PM",
    "Thursday: 9:00 AM – 5:00 PM",
    "Friday: 9:00 AM – 5:00 PM",
    "Saturday: 9:00 AM – 1:00 PM",
    "Sunday: Closed",
]


def _mar(canonical: dict) -> SimpleNamespace:
    """build_push_payload only reads these five attributes off the Mar record."""
    return SimpleNamespace(
        entity_type="attraction",
        source_ref="tripadvisor:attraction:2408107",
        canonical={"name": "Convento da Penha", "municipio_id": "3205200", **canonical},
        provenance={"score_breakdown": {}},
        reliability_score=87.5,
    )


def test_parseable_hours_ride_in_both_shapes():
    payload = build_push_payload(_mar({"weekday_text": _RAW_LINES}), rio_record=None)

    assert payload["opening_hours"] == {
        "mon_fri": "09:00-17:00",
        "sat": "09:00-13:00",
        "sun": "closed",
    }
    # The raw array is untouched — the Pact contract depends on it.
    assert payload["place"]["opening_hours"] == _RAW_LINES


def test_hours_read_from_the_legacy_signal_sub_dict():
    payload = build_push_payload(
        _mar({"signal": {"weekday_text": _RAW_LINES}}), rio_record=None
    )
    assert payload["opening_hours"]["sun"] == "closed"


def test_unparseable_hours_omit_the_key_instead_of_nulling_it():
    payload = build_push_payload(_mar({"weekday_text": ["Mon: 08:00-18:00"]}), rio_record=None)

    assert "opening_hours" not in payload
    assert payload["place"]["opening_hours"] == ["Mon: 08:00-18:00"]


def test_absent_hours_omit_the_key():
    payload = build_push_payload(_mar({}), rio_record=None)

    assert "opening_hours" not in payload
    assert payload["place"]["opening_hours"] is None
