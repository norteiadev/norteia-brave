"""Unit tests for the D-11 compliance send-path gate (COMP-01/02).

Every gate condition has its own test proving it BLOCKS (raises ComplianceError).
Tests run 100% offline — no real DB, Redis, or WhatsApp.

Uses fakeredis for Redis conditions.
Uses in-memory mocks (MagicMock/dataclasses) for Session and RioRecord.

Tests (9 total — 8 block + 1 happy path):
  test_gate_blocks_when_no_legal_basis
  test_gate_blocks_when_norteia_not_in_message
  test_gate_blocks_when_opted_out
  test_gate_blocks_when_template_not_approved
  test_gate_blocks_when_window_closed_and_utility_window_required
  test_gate_blocks_when_sub_state_not_whatsapp_in_progress
  test_gate_blocks_when_ramp_exceeded
  test_gate_blocks_when_quality_red
  test_gate_passes_when_all_conditions_met

D-11 gate conditions in order:
  1. legal basis recorded    — consent_log has row for contact_phone
  2. norteia identified      — "Norteia" in params["body"]
  3. opt-out honored         — consent_log.opted_out is False
  4. approved template       — template_name in settings.approved_templates
  5. 24h window              — if window_open=False, non-utility templates blocked
  6. human gate approved     — rio.sub_state == "whatsapp_in_progress"
  7. ramp not exceeded       — Redis INCR + daily cap (CR-04 atomic reserve-before-call)
  8. quality not red         — Redis flag wa:quality_red not set
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock
import uuid

import fakeredis
import pytest

from brave.compliance.gate import ComplianceError, send_path_gate, check_and_increment_ramp
from brave.compliance.quality_rating import is_quality_red, set_quality_flag


# ---------------------------------------------------------------------------
# Test helpers — lightweight stand-ins for DB objects (no real DB required)
# ---------------------------------------------------------------------------


@dataclass
class FakeConsentLog:
    """Minimal ConsentLog stand-in for gate tests."""
    phone_e164: str
    rio_id: uuid.UUID
    opted_out: bool = False
    legal_basis: str = "legitimate_interest_commercial_verification"
    norteia_identified: bool = True


@dataclass
class FakeRioRecord:
    """Minimal RioRecord stand-in for gate tests."""
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    sub_state: str | None = "whatsapp_in_progress"
    uf: str = "BA"
    normalized: dict[str, Any] = field(default_factory=lambda: {"window_open": True})


def _make_session_with_consent(phone: str, rio_id: uuid.UUID, opted_out: bool = False) -> MagicMock:
    """Return a mock Session that returns a FakeConsentLog for the given phone."""
    session = MagicMock()
    consent_row = FakeConsentLog(phone_e164=phone, rio_id=rio_id, opted_out=opted_out)

    # scalar() is called by is_opted_out and by the legal-basis check inside send_path_gate
    # We need to differentiate between the two calls:
    # - legal basis check: SELECT ConsentLog where phone_e164 == phone (any row)
    # - opted_out check: SELECT ConsentLog where phone_e164 == phone AND opted_out IS True
    # Gate does: consent_log.is_opted_out(session, phone) → calls session.scalar(select where opted_out=True)
    # and separately checks for any consent row
    # We return the row always; is_opted_out checks opted_out field in the returned row
    session.scalar.return_value = consent_row
    return session


def _make_session_no_consent() -> MagicMock:
    """Return a mock Session with NO ConsentLog row for any phone."""
    session = MagicMock()
    session.scalar.return_value = None
    return session


class FakeWhatsAppSettings:
    """Minimal settings stand-in for gate tests."""
    def __init__(self, approved_templates: list[str] | None = None, ramp_cap: int = 100):
        self.approved_templates = approved_templates if approved_templates is not None else ["norteia_validation_v1"]
        self.ramp_cap = ramp_cap


# ---------------------------------------------------------------------------
# Test 1: Gate blocks when no legal basis (no ConsentLog row for this phone)
# ---------------------------------------------------------------------------


def test_gate_blocks_when_no_legal_basis() -> None:
    """Condition 1: no consent_log row for contact_phone → ComplianceError."""
    session = _make_session_no_consent()
    redis = fakeredis.FakeRedis()
    rio = FakeRioRecord()
    settings = FakeWhatsAppSettings()

    with pytest.raises(ComplianceError, match=r"(?i)(legal_basis|consent_log|consent)"):
        send_path_gate(
            session=session,
            redis_client=redis,
            rio=rio,
            contact_phone="+5511999990001",
            template_name="norteia_validation_v1",
            params={"body": "Olá, sou da Norteia e gostaria de validar informações."},
            settings=settings,
        )


# ---------------------------------------------------------------------------
# Test 2: Gate blocks when "Norteia" not in message body
# ---------------------------------------------------------------------------


def test_gate_blocks_when_norteia_not_in_message() -> None:
    """Condition 2: 'Norteia' not in params['body'] → ComplianceError."""
    phone = "+5511999990002"
    rio_id = uuid.uuid4()
    session = _make_session_with_consent(phone, rio_id, opted_out=False)
    redis = fakeredis.FakeRedis()
    rio = FakeRioRecord()
    settings = FakeWhatsAppSettings()

    with pytest.raises(ComplianceError, match=r"(?i)norteia"):
        send_path_gate(
            session=session,
            redis_client=redis,
            rio=rio,
            contact_phone=phone,
            template_name="norteia_validation_v1",
            params={"body": "Olá, informações sobre seu negócio?"},  # No "Norteia"
            settings=settings,
        )


# ---------------------------------------------------------------------------
# Test 3: Gate blocks when contact is opted out
# ---------------------------------------------------------------------------


def test_gate_blocks_when_opted_out() -> None:
    """Condition 3: consent_log.opted_out = True → ComplianceError."""
    phone = "+5511999990003"
    rio_id = uuid.uuid4()
    # For opted_out check: is_opted_out uses session.scalar with opted_out=True filter
    # Our mock returns the consent row (with opted_out=True) for is_opted_out query
    session = MagicMock()

    # First scalar call: legal basis check → returns a row (legal basis exists)
    # Second scalar call: is_opted_out → returns a row (opted_out=True exists)
    consent_active = FakeConsentLog(phone_e164=phone, rio_id=rio_id, opted_out=False)
    consent_opted = FakeConsentLog(phone_e164=phone, rio_id=rio_id, opted_out=True)
    session.scalar.side_effect = [consent_active, consent_opted]

    redis = fakeredis.FakeRedis()
    rio = FakeRioRecord()
    settings = FakeWhatsAppSettings()

    with pytest.raises(ComplianceError, match=r"(?i)opted_out"):
        send_path_gate(
            session=session,
            redis_client=redis,
            rio=rio,
            contact_phone=phone,
            template_name="norteia_validation_v1",
            params={"body": "Olá, sou da Norteia e gostaria de validar informações."},
            settings=settings,
        )


# ---------------------------------------------------------------------------
# Test 4: Gate blocks when template not in approved list
# ---------------------------------------------------------------------------


def test_gate_blocks_when_template_not_approved() -> None:
    """Condition 4: template_name not in settings.approved_templates → ComplianceError."""
    phone = "+5511999990004"
    rio_id = uuid.uuid4()
    session = _make_session_with_consent(phone, rio_id, opted_out=False)
    redis = fakeredis.FakeRedis()
    rio = FakeRioRecord()
    settings = FakeWhatsAppSettings(approved_templates=[])  # empty list → nothing approved

    with pytest.raises(ComplianceError, match=r"(?i)template"):
        send_path_gate(
            session=session,
            redis_client=redis,
            rio=rio,
            contact_phone=phone,
            template_name="unapproved_template",
            params={"body": "Olá, sou da Norteia e gostaria de validar informações."},
            settings=settings,
        )


# ---------------------------------------------------------------------------
# Test 5: Gate blocks when window closed and utility window required
# ---------------------------------------------------------------------------


def test_gate_blocks_when_window_closed_and_utility_window_required() -> None:
    """Condition 5: window_open=False with non-utility template category → ComplianceError."""
    phone = "+5511999990005"
    rio_id = uuid.uuid4()
    session = _make_session_with_consent(phone, rio_id, opted_out=False)
    redis = fakeredis.FakeRedis()
    rio = FakeRioRecord(normalized={"window_open": False})  # window closed
    settings = FakeWhatsAppSettings(approved_templates=["norteia_validation_v1"])

    with pytest.raises(ComplianceError, match=r"(?i)(24h|window)"):
        send_path_gate(
            session=session,
            redis_client=redis,
            rio=rio,
            contact_phone=phone,
            template_name="norteia_validation_v1",
            params={"body": "Olá, sou da Norteia e gostaria de validar informações."},
            settings=settings,
        )


# ---------------------------------------------------------------------------
# Test 6: Gate blocks when sub_state != "whatsapp_in_progress"
# ---------------------------------------------------------------------------


def test_gate_blocks_when_sub_state_not_whatsapp_in_progress() -> None:
    """Condition 6: rio.sub_state != 'whatsapp_in_progress' → ComplianceError."""
    phone = "+5511999990006"
    rio_id = uuid.uuid4()
    session = _make_session_with_consent(phone, rio_id, opted_out=False)
    redis = fakeredis.FakeRedis()
    rio = FakeRioRecord(sub_state="aguardando_consulta_whatsapp")  # not yet approved
    settings = FakeWhatsAppSettings()

    with pytest.raises(ComplianceError, match=r"(?i)(sub_state|gate)"):
        send_path_gate(
            session=session,
            redis_client=redis,
            rio=rio,
            contact_phone=phone,
            template_name="norteia_validation_v1",
            params={"body": "Olá, sou da Norteia e gostaria de validar informações."},
            settings=settings,
        )


# ---------------------------------------------------------------------------
# Test 7: Gate blocks when ramp cap exceeded — AND counter is decremented back
# ---------------------------------------------------------------------------


def test_gate_blocks_when_ramp_exceeded() -> None:
    """Condition 7: ramp counter at cap → INCR exceeds cap → ComplianceError + DECR.

    CR-04 pattern: reserve-before-call (INCR first), then check, then DECR on cap breach.
    After ComplianceError, the counter must be back to the pre-call value (cap, not cap+1).
    """
    phone = "+5511999990007"
    rio_id = uuid.uuid4()
    session = _make_session_with_consent(phone, rio_id, opted_out=False)
    redis = fakeredis.FakeRedis()

    cap = 1
    settings = FakeWhatsAppSettings(ramp_cap=cap)
    rio = FakeRioRecord()

    # Pre-seed ramp counter to cap (1) — the next INCR will push it to 2 > cap
    from datetime import datetime, timezone
    date_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    ramp_key = f"wa:ramp:{date_key}"
    redis.set(ramp_key, cap)

    with pytest.raises(ComplianceError, match=r"(?i)ramp cap"):
        send_path_gate(
            session=session,
            redis_client=redis,
            rio=rio,
            contact_phone=phone,
            template_name="norteia_validation_v1",
            params={"body": "Olá, sou da Norteia e gostaria de validar informações."},
            settings=settings,
        )

    # CRITICAL: verify counter was decremented back after raise (undo reserve — CR-04)
    counter_value = int(redis.get(ramp_key))
    assert counter_value == cap, (
        f"Ramp counter should be decremented back to {cap} after cap breach, "
        f"but got {counter_value}"
    )


# ---------------------------------------------------------------------------
# Test 8: Gate blocks when quality rating is RED
# ---------------------------------------------------------------------------


def test_gate_blocks_when_quality_red() -> None:
    """Condition 8: Redis wa:quality_red flag set → ComplianceError."""
    phone = "+5511999990008"
    rio_id = uuid.uuid4()
    session = _make_session_with_consent(phone, rio_id, opted_out=False)
    redis = fakeredis.FakeRedis()

    # Set the quality red flag
    redis.set("wa:quality_red", "1")

    rio = FakeRioRecord()
    settings = FakeWhatsAppSettings()

    with pytest.raises(ComplianceError, match=r"(?i)(quality|RED)"):
        send_path_gate(
            session=session,
            redis_client=redis,
            rio=rio,
            contact_phone=phone,
            template_name="norteia_validation_v1",
            params={"body": "Olá, sou da Norteia e gostaria de validar informações."},
            settings=settings,
        )


# ---------------------------------------------------------------------------
# Test 9: Happy path — all conditions met, gate passes, ramp incremented
# ---------------------------------------------------------------------------


def test_gate_passes_when_all_conditions_met() -> None:
    """Happy path: all 8 conditions satisfied → send_path_gate returns None.

    Verifies ramp counter is incremented to 1 after call (INCR happened, check passed).
    """
    phone = "+5511999990009"
    rio_id = uuid.uuid4()
    session = _make_session_with_consent(phone, rio_id, opted_out=False)
    redis = fakeredis.FakeRedis()  # fresh — no quality_red, ramp at 0

    rio = FakeRioRecord(
        sub_state="whatsapp_in_progress",
        normalized={"window_open": True},
    )
    settings = FakeWhatsAppSettings(
        approved_templates=["norteia_validation_v1"],
        ramp_cap=100,
    )

    # Must NOT raise
    result = send_path_gate(
        session=session,
        redis_client=redis,
        rio=rio,
        contact_phone=phone,
        template_name="norteia_validation_v1",
        params={"body": "Olá, sou da Norteia e gostaria de validar informações."},
        settings=settings,
    )
    assert result is None, "send_path_gate should return None on success"

    # Ramp counter should be 1 after successful gate passage
    from datetime import datetime, timezone
    date_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    ramp_key = f"wa:ramp:{date_key}"
    counter_value = redis.get(ramp_key)
    assert counter_value is not None, "Ramp counter key should exist after gate passage"
    assert int(counter_value) == 1, (
        f"Ramp counter should be 1 after successful gate passage, got {int(counter_value)}"
    )


# ---------------------------------------------------------------------------
# Standalone tests for quality_rating module
# ---------------------------------------------------------------------------


def test_is_quality_red_returns_true_when_flag_set() -> None:
    """is_quality_red returns True when wa:quality_red is set."""
    redis = fakeredis.FakeRedis()
    redis.set("wa:quality_red", "1")
    assert is_quality_red(redis) is True


def test_is_quality_red_returns_false_when_flag_cleared() -> None:
    """is_quality_red returns False after GREEN rating clears the flag."""
    redis = fakeredis.FakeRedis()
    redis.set("wa:quality_red", "1")
    set_quality_flag(redis, "GREEN")
    assert is_quality_red(redis) is False


def test_check_and_increment_ramp_decrements_on_cap_breach() -> None:
    """check_and_increment_ramp: INCR raises on cap breach AND decrements counter back."""
    redis = fakeredis.FakeRedis()
    cap = 3
    from datetime import datetime, timezone
    date_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    key = f"wa:ramp:{date_key}"
    redis.set(key, cap)  # pre-seed to cap

    with pytest.raises(ComplianceError, match=r"(?i)ramp cap"):
        check_and_increment_ramp(redis, cap)

    assert int(redis.get(key)) == cap, "Counter must be decremented back to cap after breach"
