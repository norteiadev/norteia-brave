"""Unit tests for promote_override service (plan 11-03, TA-05).

Tests the core promote-override gate:
  - PromoteNotAllowed raised when rio.mar_ready=False (T-11-03-01)
  - MarRecord created with provenance["promotion_reason"] when mar_ready=True
  - Engine source set/get functions
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

from brave.config.settings import ScoreConfig


# ---------------------------------------------------------------------------
# promote_override tests
# ---------------------------------------------------------------------------


def _make_rio(mar_ready: bool, routing: str = "dlq") -> MagicMock:
    """Build a minimal RioRecord-like mock for unit testing."""
    rio = MagicMock()
    rio.id = uuid.uuid4()
    rio.mar_ready = mar_ready
    rio.routing = routing
    rio.normalized = {"origem_value": 65.0, "completude_value": 100.0}
    rio.score_breakdown = {}
    rio.score_version = "v1.0"
    rio.score = 67.05
    rio.nascente_id = uuid.uuid4()
    rio.canonical_key = "tripadvisor:attraction:12345"
    rio.entity_type = "attraction"
    rio.provenance = None
    return rio


def test_promote_override_not_mar_ready_raises() -> None:
    """PromoteNotAllowed raised when rio.mar_ready=False (T-11-03-01 guard)."""
    from brave.core.promote.service import PromoteNotAllowed, promote_override

    session = MagicMock()
    rio = _make_rio(mar_ready=False)

    with pytest.raises(PromoteNotAllowed) as exc_info:
        promote_override(session, rio, reason="steward_override_review_validated")

    # Error message must include the rio id.
    assert str(rio.id) in str(exc_info.value)


def test_promote_override_creates_mar_record_when_mar_ready() -> None:
    """promote_override on mar_ready=True returns a MarRecord with promotion_reason."""
    from brave.core.promote.service import promote_override

    session = MagicMock()
    rio = _make_rio(mar_ready=True)
    config = ScoreConfig()

    fake_mar = MagicMock()
    fake_mar.provenance = {}

    with (
        patch("brave.core.promote.service.reprocess_record"),
        patch("brave.core.promote.service.promote_to_mar", return_value=fake_mar) as mock_promote,
        patch("brave.core.promote.service.flag_modified"),
    ):
        result = promote_override(
            session,
            rio,
            reason="steward_override_review_validated",
            config=config,
        )

    # Must call promote_to_mar
    mock_promote.assert_called_once()

    # Must return a MarRecord
    assert result is not None

    # MarRecord provenance must include promotion_reason
    assert result.provenance is not None
    assert result.provenance.get("promotion_reason") == "steward_override_review_validated"


def test_promote_override_forces_routing_to_mar() -> None:
    """promote_override sets rio.routing='mar' before calling promote_to_mar."""
    from brave.core.promote.service import promote_override

    session = MagicMock()
    rio = _make_rio(mar_ready=True, routing="dlq")
    config = ScoreConfig()

    captured_rio_routing: list[str] = []

    fake_mar = MagicMock()
    fake_mar.provenance = {}

    def _capture_promote(sess, rio_arg):
        captured_rio_routing.append(rio_arg.routing)
        return fake_mar

    with (
        patch("brave.core.promote.service.reprocess_record"),
        patch("brave.core.promote.service.promote_to_mar", side_effect=_capture_promote),
        patch("brave.core.promote.service.flag_modified"),
    ):
        promote_override(
            session,
            rio,
            reason="steward_override_review_validated",
            config=config,
        )

    # routing must be "mar" when promote_to_mar is called
    assert captured_rio_routing == ["mar"]
