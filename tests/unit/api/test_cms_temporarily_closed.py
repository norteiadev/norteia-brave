"""The "Fechado Temporariamente" Painel pill: derived from the DLQ reason or the Places signal."""

from types import SimpleNamespace

from brave.api.routers.cms import _temporarily_closed


def _rio(dlq_reason=None, business_status=None):
    normalized = {"signal": {"business_status": business_status}} if business_status else {}
    return SimpleNamespace(dlq_reason=dlq_reason, normalized=normalized)


def test_temporarily_closed_reads_the_dlq_reason_or_the_places_signal():
    assert _temporarily_closed(_rio(dlq_reason="closed_temporarily"))
    # still flagged after a steward moved it out of the DLQ (reason changed)
    assert _temporarily_closed(_rio(dlq_reason=None, business_status="CLOSED_TEMPORARILY"))
    assert not _temporarily_closed(_rio(business_status="OPERATIONAL"))
    assert not _temporarily_closed(_rio(dlq_reason="closed_place"))
