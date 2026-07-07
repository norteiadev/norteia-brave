"""Unit tests for the Phase F MASKED whatsapp_candidate capture (LGPD R3).

Covers:
  - whatsapp_candidate_from_phone: celular → masked; landline / non-BR / empty → None.
  - ContactFinderAgent (Places lane): writes a MASKED candidate to
    normalized["contact"]["whatsapp_candidate"] and NEVER a raw celular.
  - The CMS board projection (_safe_normalized / _safe_contact): surfaces only the
    masked candidate and drops raw / unexpected keys (defense-in-depth).

100% offline: FakePlacesClient + mock RioRecord, no DB, no network.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

from brave.core.models import whatsapp_candidate_from_phone
from tests.fakes.fake_places import FakePlacesClient

_RAW_CELULAR = "+5573999990001"
_MASKED_CELULAR = "+5573*****01"


# ---------------------------------------------------------------------------
# whatsapp_candidate_from_phone — celular detection + masking
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "+5573999990001",       # E.164 celular
        "+55 73 99999-0001",    # formatted E.164 celular
        "5573999990001",        # 55-prefixed, no +
        "73999990001",          # bare national celular (DDD + 9 + 8)
    ],
)
def test_celular_variants_return_masked(raw: str) -> None:
    result = whatsapp_candidate_from_phone(raw)
    assert result == _MASKED_CELULAR
    # LGPD: the raw subscriber digits must NEVER be present in the stored value.
    assert "99999" not in result
    assert result.count("*") == 5


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "+551133334444",   # landline (10 national digits, no leading 9)
        "1133334444",      # bare national landline
        "+14155551234",    # non-BR
        "abc",             # no digits
    ],
)
def test_non_celular_returns_none(raw) -> None:
    assert whatsapp_candidate_from_phone(raw) is None


def test_masked_value_is_never_the_raw_number() -> None:
    assert whatsapp_candidate_from_phone(_RAW_CELULAR) != _RAW_CELULAR


# ---------------------------------------------------------------------------
# ContactFinderAgent — masked capture into normalized["contact"]
# ---------------------------------------------------------------------------


def _make_rio() -> MagicMock:
    rio = MagicMock()
    rio.id = uuid.uuid4()
    rio.sub_state = "discovered"
    rio.normalized = {"place_id_cache": "ChIJx"}
    return rio


@pytest.mark.asyncio
async def test_contact_finder_captures_masked_whatsapp_candidate() -> None:
    """A celular from Places is stored MASKED at normalized['contact']['whatsapp_candidate'];
    the raw celular lives only in normalized['contacts']['phone_e164']."""
    from brave.lanes.atrativos.contact_finder_agent import ContactFinderAgent

    fake_places = FakePlacesClient(
        fixture_details={"ChIJx": {"international_phone_number": "+55 73 99999-0001"}},
    )
    session = MagicMock()
    rio = _make_rio()

    agent = ContactFinderAgent(places_client=fake_places, session=session)
    with patch("brave.lanes.atrativos.contact_finder_agent.write_audit"):
        await agent.run(rio)

    assert rio.normalized["contact"]["whatsapp_candidate"] == _MASKED_CELULAR
    # The masked candidate must NOT be the raw number.
    assert rio.normalized["contact"]["whatsapp_candidate"] != _RAW_CELULAR
    # Raw E.164 is retained only under the plural 'contacts' key (consent/outreach path).
    assert rio.normalized["contacts"]["phone_e164"] == _RAW_CELULAR


@pytest.mark.asyncio
async def test_contact_finder_no_candidate_for_landline() -> None:
    """A landline is not a WhatsApp celular → no normalized['contact'] written."""
    from brave.lanes.atrativos.contact_finder_agent import ContactFinderAgent

    fake_places = FakePlacesClient(
        fixture_details={"ChIJx": {"international_phone_number": "+55 11 3333-4444"}},
    )
    session = MagicMock()
    rio = _make_rio()

    agent = ContactFinderAgent(places_client=fake_places, session=session)
    with patch("brave.lanes.atrativos.contact_finder_agent.write_audit"):
        await agent.run(rio)

    assert "contact" not in rio.normalized


@pytest.mark.asyncio
async def test_contact_finder_no_candidate_when_no_phone() -> None:
    """NullPlacesClient-style empty details → no phone → no normalized['contact']."""
    from brave.lanes.atrativos.contact_finder_agent import ContactFinderAgent

    fake_places = FakePlacesClient(fixture_details={"ChIJx": {}})
    session = MagicMock()
    rio = _make_rio()

    agent = ContactFinderAgent(places_client=fake_places, session=session)
    with patch("brave.lanes.atrativos.contact_finder_agent.write_audit"):
        await agent.run(rio)

    assert "contact" not in rio.normalized


# ---------------------------------------------------------------------------
# CMS board projection — masked-only, deny-by-default
# ---------------------------------------------------------------------------


def test_safe_normalized_surfaces_masked_candidate() -> None:
    from brave.api.routers.cms import _safe_normalized

    out = _safe_normalized({"contact": {"whatsapp_candidate": _MASKED_CELULAR}})
    assert out["contact"]["whatsapp_candidate"] == _MASKED_CELULAR


def test_safe_normalized_remasks_and_drops_unexpected_keys() -> None:
    """Defense-in-depth: even a RAW celular that leaked into normalized['contact'] is
    re-masked, and any non-allow-listed key is dropped."""
    from brave.api.routers.cms import _safe_normalized

    out = _safe_normalized(
        {"contact": {"whatsapp_candidate": _RAW_CELULAR, "email": "x@y.com"}}
    )
    assert out["contact"]["whatsapp_candidate"] == _MASKED_CELULAR
    assert out["contact"]["whatsapp_candidate"] != _RAW_CELULAR
    assert "email" not in out["contact"]


def test_safe_normalized_drops_empty_contact() -> None:
    from brave.api.routers.cms import _safe_normalized

    out = _safe_normalized({"contact": {"whatsapp_candidate": None}})
    assert "contact" not in out
