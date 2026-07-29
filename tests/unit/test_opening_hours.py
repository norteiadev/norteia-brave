"""Unit tests for the Google weekdayDescriptions → hours-map converter.

The output contract is language-neutral (English keys, ASCII hyphen, status tokens) —
labels are the frontend's i18n job. Input is accepted in English OR PT-BR because
GetPlaceRequest currently sends no language_code (clients/places.py:369) and a future
locale pin must not change the stored map.
"""

import pytest

from brave.shared.opening_hours import to_hours_map


def _week(mon_fri: str, sat: str, sun: str) -> list[str]:
    days = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")
    return [f"{d}: {mon_fri}" for d in days] + [f"Saturday: {sat}", f"Sunday: {sun}"]


def test_golden_case_english():
    assert to_hours_map(_week("9:00 AM – 5:00 PM", "9:00 AM – 1:00 PM", "Closed")) == {
        "mon_fri": "09:00-17:00",
        "sat": "09:00-13:00",
        "sun": "closed",
    }


def test_all_seven_identical_collapses_to_daily():
    # Real captured sample (Parque Ibirapuera, atrativos_e2e_5.json).
    lines = [
        f"{d}: 5:00 AM – 11:00 PM"
        for d in ("Monday", "Tuesday", "Wednesday", "Thursday",
                  "Friday", "Saturday", "Sunday")
    ]
    assert to_hours_map(lines) == {"daily": "05:00-23:00"}


def test_ptbr_input_yields_the_same_english_map():
    ptbr = [
        "segunda-feira: 09:00 – 17:00",
        "terça-feira: 09:00 – 17:00",
        "quarta-feira: 09:00 – 17:00",
        "quinta-feira: 09:00 – 17:00",
        "sexta-feira: 09:00 – 17:00",
        "sábado: 09:00 – 13:00",
        "domingo: Fechado",
    ]
    assert to_hours_map(ptbr) == to_hours_map(
        _week("9:00 AM – 5:00 PM", "9:00 AM – 1:00 PM", "Closed")
    )


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("12:00 AM – 11:59 PM", "00:00-23:59"),
        ("12:00 PM – 12:30 PM", "12:00-12:30"),
        ("5:00 PM – 2:00 AM", "17:00-02:00"),  # overnight: attributed to the opening day
    ],
)
def test_twelve_hour_boundaries(raw, expected):
    assert to_hours_map(_week(raw, raw, raw)) == {"daily": expected}


@pytest.mark.parametrize("raw", ["Open 24 hours", "Aberto 24 horas", "24 horas"])
def test_open_24h_token(raw):
    assert to_hours_map(_week(raw, raw, raw)) == {"daily": "open_24h"}


def test_split_shift_is_comma_joined():
    assert to_hours_map(_week(
        "8:00 AM – 12:00 PM, 1:00 PM – 5:00 PM", "Closed", "Closed"
    )) == {"mon_fri": "08:00-12:00, 13:00-17:00", "sat_sun": "closed"}


def test_hyphen_input_matches_en_dash_and_output_is_ascii_hyphen():
    hyphen = to_hours_map(_week("08:00-18:00", "08:00-18:00", "08:00-18:00"))
    en_dash = to_hours_map(_week("08:00 – 18:00", "08:00 – 18:00", "08:00 – 18:00"))
    assert hyphen == en_dash == {"daily": "08:00-18:00"}
    # Guard against U+2013 leaking into the DB.
    assert "–" not in hyphen["daily"]


def test_non_contiguous_runs_are_not_merged():
    lines = [
        "Monday: Closed",
        "Tuesday: 8:00 AM – 6:00 PM",
        "Wednesday: Closed",
        "Thursday: 8:00 AM – 6:00 PM",
        "Friday: 8:00 AM – 6:00 PM",
        "Saturday: 8:00 AM – 6:00 PM",
        "Sunday: 8:00 AM – 6:00 PM",
    ]
    assert to_hours_map(lines) == {
        "mon": "closed",
        "tue": "08:00-18:00",
        "wed": "closed",
        "thu_sun": "08:00-18:00",
    }


@pytest.mark.parametrize(
    "lines",
    [
        None,
        [],
        _week("9:00 AM – 5:00 PM", "Closed", "Closed")[:6],   # only 6 days
        ["Mon: 08:00-18:00"],                                  # abbreviated day name
        _week("por agendamento", "Closed", "Closed"),          # unparseable value
        ["Monday 08:00-18:00"] + _week("08:00-18:00", "Closed", "Closed")[1:],  # no colon
        _week("25:00-26:00", "Closed", "Closed"),              # impossible hour
    ],
)
def test_returns_none_when_anything_is_unparseable(lines):
    assert to_hours_map(lines) is None
