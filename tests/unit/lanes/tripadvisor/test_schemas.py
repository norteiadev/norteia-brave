"""Tests for TripAdvisor LGPD-boundary schemas (TA-08).

TripAdvisorReviewSignals enforces the LGPD boundary via model_config=extra='forbid'.
No author, text, or reviewer_id fields may enter a TripAdvisorReviewSignals object.
"""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from brave.lanes.tripadvisor.schemas import (
    TripAdvisorAtrativoPayload,
    TripAdvisorDestinoPayload,
    TripAdvisorReviewSignals,
)


class TestTripAdvisorReviewSignals:
    """TripAdvisorReviewSignals LGPD enforcement tests (T-11-02-01)."""

    def test_review_signals_valid_fields(self) -> None:
        """Valid review signals with only aggregate fields should construct successfully."""
        signals = TripAdvisorReviewSignals(
            review_count=200,
            rating=4.5,
            most_recent_review_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        assert signals.review_count == 200
        assert signals.rating == 4.5
        assert signals.most_recent_review_at is not None

    def test_review_signals_defaults(self) -> None:
        """TripAdvisorReviewSignals should work with defaults (no args)."""
        signals = TripAdvisorReviewSignals()
        assert signals.review_count == 0
        assert signals.rating == 0.0
        assert signals.most_recent_review_at is None

    def test_review_signals_rejects_author_field(self) -> None:
        """LGPD boundary: extra='forbid' must reject the 'author' field (T-11-02-01)."""
        with pytest.raises(ValidationError):
            TripAdvisorReviewSignals.model_validate({"author": "X", "review_count": 1})

    def test_review_signals_rejects_text_field(self) -> None:
        """LGPD boundary: extra='forbid' must reject the 'text' field."""
        with pytest.raises(ValidationError):
            TripAdvisorReviewSignals.model_validate({"text": "Great place!", "review_count": 1})

    def test_review_signals_rejects_reviewer_id_field(self) -> None:
        """LGPD boundary: extra='forbid' must reject the 'reviewer_id' field."""
        with pytest.raises(ValidationError):
            TripAdvisorReviewSignals.model_validate({"reviewer_id": "usr123"})

    def test_destino_payload_valid(self) -> None:
        """TripAdvisorDestinoPayload should accept a well-formed destino."""
        payload = TripAdvisorDestinoPayload(
            name="Salvador",
            uf="BA",
            location_id="303506",
            lat=-12.9714,
            lng=-38.5014,
            review_signals=TripAdvisorReviewSignals(review_count=500, rating=4.7),
            origem_value=65.0,
            completude_value=80.0,
            corroboracao_value=77.0,
            atualidade_value=70.0,
            validacao_humana_value=0.0,
        )
        assert payload.location_id == "303506"
        assert payload.origem_value == 65.0

    def test_atrativo_payload_valid(self) -> None:
        """TripAdvisorAtrativoPayload should accept a well-formed atrativo."""
        import uuid
        parent_id = str(uuid.uuid4())
        payload = TripAdvisorAtrativoPayload(
            name="Elevador Lacerda",
            uf="BA",
            location_id="99999",
            lat=-12.97,
            lng=-38.51,
            review_signals=TripAdvisorReviewSignals(review_count=200, rating=4.5),
            origem_value=65.0,
            completude_value=100.0,
            corroboracao_value=85.0,
            atualidade_value=70.0,
            validacao_humana_value=0.0,
            parent_rio_id=parent_id,
            parent_source_ref="tripadvisor:destination:303506",
        )
        assert payload.parent_rio_id == parent_id
