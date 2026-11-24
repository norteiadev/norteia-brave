"""Perishable facts of an old post wait for a web check before they can travel.

Decision (user): a claim of a PERISHABLE column whose post is more than a year old (against
today's date) is not pushed straight away; it waits as ``pending_validation``."""

from __future__ import annotations

from datetime import date

import pytest

from brave.domains.editorial.schema import (
    PERENNIAL_FIELDS,
    PERISHABLE_FIELDS,
    needs_validation,
)

TODAY = date(2026, 11, 12)


def test_the_perishable_columns_are_the_ones_the_user_listed() -> None:
    assert PERISHABLE_FIELDS["atrativo"] == frozenset(
        {"local_infrastructure", "tips", "how_to_get_there", "safety_alerts", "accessibility"}
    )
    assert PERISHABLE_FIELDS["destino"] == frozenset(
        {
            "tips",
            "how_to_get_there",
            "safety_alerts",
            "connectivity_level",
            "structure_level",
            "estimated_spending_range",
            "flow_level",
        }
    )


def test_the_perennial_columns_never_need_validation() -> None:
    assert (
        frozenset(
            {
                "curiosities",
                "best_time_to_visit",
                "seasonality",
                "recommended_duration",
                "pace_profile",
                "predominant_audience_profile",
            }
        )
        == PERENNIAL_FIELDS
    )
    for field in PERENNIAL_FIELDS:
        assert not needs_validation("atrativo", field, date(2015, 1, 1), TODAY)
        assert not needs_validation("destino", field, date(2015, 1, 1), TODAY)


@pytest.mark.parametrize(
    ("published", "expected"),
    [
        (date(2025, 11, 12), False),  # exactly one year: not "more than"
        (date(2025, 11, 13), False),
        (date(2025, 11, 11), True),  # a day past the year
        (date(2020, 2, 17), True),
        (None, True),  # an undated post cannot be shown to be fresh
    ],
)
def test_the_age_rule_is_one_year_against_today(published: date | None, expected: bool) -> None:
    assert needs_validation("atrativo", "tips", published, TODAY) is expected


def test_the_same_column_can_be_perishable_for_one_kind_only() -> None:
    assert needs_validation("atrativo", "accessibility", date(2020, 1, 1), TODAY)
    assert not needs_validation("destino", "accessibility", date(2020, 1, 1), TODAY)
    assert needs_validation("destino", "estimated_spending_range", date(2020, 1, 1), TODAY)
    assert not needs_validation("atrativo", "estimated_spending_range", date(2020, 1, 1), TODAY)
