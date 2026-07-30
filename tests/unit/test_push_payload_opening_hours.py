"""build_push_payload carries BOTH hours shapes for attractions.

- ``place.opening_hours`` — the raw Google lines, untouched (frozen by the Pact).
- top-level ``opening_hours`` — the language-neutral day→hours map, OMITTED (never
  null) when the lines don't parse, so a re-push can't wipe a curated value.

Also covers the other payload fields the API validates strictly: ``place.price_level``
(integer column — an enum string 422s the whole push) and the address/signal
pass-throughs that the enrichment lanes fill.
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


def test_price_level_enum_name_becomes_the_legacy_integer():
    payload = build_push_payload(_mar({"price_level": "PRICE_LEVEL_MODERATE"}), rio_record=None)

    assert payload["place"]["price_level"] == 2


def test_price_level_free_is_zero_not_dropped():
    # 0 is falsy — a truthy guard anywhere on this path would silently null it.
    payload = build_push_payload(_mar({"price_level": "PRICE_LEVEL_FREE"}), rio_record=None)

    assert payload["place"]["price_level"] == 0


def test_unknown_price_level_is_null_instead_of_a_422():
    payload = build_push_payload(_mar({"price_level": "PRICE_LEVEL_UNSPECIFIED"}), rio_record=None)

    assert payload["place"]["price_level"] is None


def test_numeric_price_level_is_dropped_not_forwarded():
    # A bare number is ambiguous: 3 is EXPENSIVE on the legacy 0-4 scale but
    # MODERATE as a proto ordinal. No producer emits one — drop it rather than
    # guess a tier. 5 is out of the legacy range entirely.
    for raw in (3, 5, "3"):
        payload = build_push_payload(_mar({"price_level": raw}), rio_record=None)

        assert payload["place"]["price_level"] is None


def test_address_and_signal_fields_reach_the_payload():
    payload = build_push_payload(
        _mar(
            {
                "address": "Praça Central, Porto Seguro - BA",
                "signal": {"business_status": "OPERATIONAL", "reviews_recent_count": 1},
            }
        ),
        rio_record=None,
    )

    assert payload["address"] == "Praça Central, Porto Seguro - BA"
    assert payload["place"]["business_status"] == "OPERATIONAL"
    assert payload["place"]["reviews_recent_count"] == 1
