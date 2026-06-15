"""Unit tests for consent_log upsert / opt-out safety (WR-09, COMP-01).

write_consent_record must:
  - refuse to create an active row for an already-opted-out phone (raise
    OptedOutError, create nothing)
  - reuse an existing active row instead of inserting a duplicate (retry-safe)
  - insert a new active row when none exists
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

from brave.compliance.consent_log import OptedOutError, write_consent_record


def _phone() -> str:
    return "+5573999990001"


def test_write_consent_refuses_when_opted_out() -> None:
    """WR-09: opted-out phone → OptedOutError, no row created."""
    session = MagicMock()
    # is_opted_out() -> scalar returns a row (opted-out exists)
    session.scalar.return_value = MagicMock()  # truthy → opted out

    with pytest.raises(OptedOutError):
        write_consent_record(
            session=session,
            phone_e164=_phone(),
            rio_id=uuid.uuid4(),
            legal_basis="legitimate_interest_commercial_verification",
            norteia_identified=True,
        )

    session.add.assert_not_called()


def test_write_consent_reuses_existing_active_row() -> None:
    """WR-09: an existing active row is reused (no duplicate insert) under retry."""
    session = MagicMock()
    existing = MagicMock()
    existing.legal_basis = "legitimate_interest_commercial_verification"
    # 1st scalar (is_opted_out) → None (not opted out)
    # 2nd scalar (existing active row) → existing
    session.scalar.side_effect = [None, existing]

    result = write_consent_record(
        session=session,
        phone_e164=_phone(),
        rio_id=uuid.uuid4(),
        legal_basis="legitimate_interest_commercial_verification",
        norteia_identified=True,
    )

    assert result is existing
    session.add.assert_not_called()  # reused, not inserted
    assert existing.last_contact_at is not None


def test_write_consent_inserts_new_row_when_none_exists() -> None:
    """WR-09: no opted-out row and no existing active row → insert a new active row."""
    session = MagicMock()
    # is_opted_out → None; existing-active lookup → None
    session.scalar.side_effect = [None, None]

    result = write_consent_record(
        session=session,
        phone_e164=_phone(),
        rio_id=uuid.uuid4(),
        legal_basis="legitimate_interest_commercial_verification",
        norteia_identified=True,
    )

    session.add.assert_called_once()
    assert result.opted_out is False
    assert result.phone_e164 == _phone()
