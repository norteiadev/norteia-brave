"""Unit tests for outreach contact-phone extraction (CR-03).

ContactFinderAgent stores the owner phone at
normalized["contacts"]["phone_e164"]. The outreach task must read the SAME
canonical path; reading a non-existent top-level "contact_phone" key produced ""
in production, which keyed all LGPD consent/opt-out/suppression rows and the
inbound-routing lookup on the empty string.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from brave.tasks.pipeline import _extract_contact_phone


def _rio(normalized: dict | None):
    return SimpleNamespace(id=uuid.uuid4(), normalized=normalized)


def test_extract_phone_from_canonical_contacts_key() -> None:
    rio = _rio({"contacts": {"phone_e164": "+5573999990001", "ig_handle": "@x"}})
    assert _extract_contact_phone(rio) == "+5573999990001"


def test_extract_phone_missing_contacts_returns_empty() -> None:
    rio = _rio({"some_other_key": 1})
    assert _extract_contact_phone(rio) == ""


def test_extract_phone_none_normalized_returns_empty() -> None:
    rio = _rio(None)
    assert _extract_contact_phone(rio) == ""


def test_extract_phone_does_not_read_top_level_contact_phone() -> None:
    """CR-03 regression: the buggy top-level "contact_phone" key is NOT honored."""
    rio = _rio({"contact_phone": "+5573999990009"})
    assert _extract_contact_phone(rio) == ""


def test_extract_phone_null_phone_e164_returns_empty() -> None:
    rio = _rio({"contacts": {"phone_e164": None}})
    assert _extract_contact_phone(rio) == ""
