"""Unit tests for the ``record_event`` helper (Log-tab timeline write path).

Verifies the helper mirrors ``write_audit``:
  - inserts one correctly-populated ``RecordEvent`` row on the caller's session
    (session.add + session.flush, never a separate session), and
  - emits a single ``record_event`` structlog entry carrying ONLY the
    public-geo/engineering correlation fields (stage/status/source_ref) — never
    the message, the ``data`` payload, or any PII.

100% offline: the session is a MagicMock, so no DB connection is required. The
LGPD assertion pins the structlog surface so a future refactor cannot start
leaking ``message``/``data``/PII into the correlation log.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

from structlog.testing import capture_logs

from brave.core.models import RecordEvent
from brave.observability.record_events import record_event


class TestRecordEventRowWrite:
    """record_event inserts the row on the caller's session and returns it."""

    def test_inserts_row_with_all_fields_and_flushes(self) -> None:
        """The created RecordEvent carries every argument verbatim; add+flush called once."""
        session = MagicMock()
        nascente_id = uuid.uuid4()
        rio_id = uuid.uuid4()

        event = record_event(
            session,
            source="tripadvisor",
            source_ref="tripadvisor:attraction:312332",
            stage="ingested",
            status="ok",
            message="Cachoeira do Tabuleiro",
            entity_type="attraction",
            uf="MG",
            nascente_id=nascente_id,
            rio_id=rio_id,
            data={"municipio": "Conceição do Mato Dentro", "version": 1},
        )

        # Returned row is a RecordEvent with the exact field values passed in.
        assert isinstance(event, RecordEvent)
        assert event.source == "tripadvisor"
        assert event.source_ref == "tripadvisor:attraction:312332"
        assert event.stage == "ingested"
        assert event.status == "ok"
        assert event.message == "Cachoeira do Tabuleiro"
        assert event.entity_type == "attraction"
        assert event.uf == "MG"
        assert event.nascente_id == nascente_id
        assert event.rio_id == rio_id
        assert event.data == {"municipio": "Conceição do Mato Dentro", "version": 1}
        assert event.id is not None  # helper assigns a uuid PK

        # Written to the caller's session (add), then flushed on that same session.
        session.add.assert_called_once_with(event)
        session.flush.assert_called_once_with()

    def test_optional_fields_default_to_none(self) -> None:
        """Omitted optional args land as None (row is still valid for a pre-DB stage)."""
        session = MagicMock()

        event = record_event(
            session,
            source="tripadvisor",
            source_ref="tripadvisor:attraction:999",
            stage="tripadvisor_synced",
            status="ok",
        )

        assert event.message is None
        assert event.entity_type is None
        assert event.uf is None
        assert event.nascente_id is None
        assert event.rio_id is None
        assert event.data is None


class TestRecordEventStructlog:
    """record_event emits exactly one correlation log — LGPD-minimized."""

    def test_emits_single_record_event_log_with_correlation_fields(self) -> None:
        """One 'record_event' entry carries stage/status/source_ref."""
        session = MagicMock()

        with capture_logs() as logs:
            record_event(
                session,
                source="tripadvisor",
                source_ref="tripadvisor:attraction:312332",
                stage="routed",
                status="fail",
                message="dlq: score below threshold",
                entity_type="attraction",
                uf="MG",
                data={"routing": "dlq", "dlq_reason": "score below threshold"},
            )

        entries = [e for e in logs if e.get("event") == "record_event"]
        assert len(entries) == 1, f"expected exactly one record_event log, got {logs}"
        entry = entries[0]
        assert entry["stage"] == "routed"
        assert entry["status"] == "fail"
        assert entry["source_ref"] == "tripadvisor:attraction:312332"

    def test_structlog_never_leaks_message_data_or_pii(self) -> None:
        """LGPD: the correlation log must NOT carry message/data/name/phone/PII.

        The structlog surface is the correlation boundary — only stage/status/
        source_ref (public-geo/engineering) may appear. The DB row keeps the
        message/data (also public-geo only), but they must never ride into the
        JSON log where log aggregators fan them out.
        """
        session = MagicMock()

        with capture_logs() as logs:
            record_event(
                session,
                source="tripadvisor",
                source_ref="tripadvisor:attraction:312332",
                stage="quarantined",
                status="fail",
                message="ibge_unmatched: 'Praia Do Bosque'",
                entity_type="attraction",
                uf="MG",
                data={
                    "reason": "ibge_unmatched",
                    "name": "Praia Do Bosque",
                    "locationId": "312332",
                },
            )

        entry = next(e for e in logs if e.get("event") == "record_event")
        # The message and the data payload must not be present as log fields.
        assert "message" not in entry, f"message leaked into structlog: {entry}"
        assert "data" not in entry, f"data payload leaked into structlog: {entry}"
        assert "name" not in entry
        assert "reason" not in entry
        assert "locationId" not in entry
        # And no substring of the sensitive message/name may appear in any value.
        serialized = str(entry)
        assert "Praia Do Bosque" not in serialized, (
            f"attraction name leaked into structlog fields: {entry}"
        )
        # Only the whitelisted correlation keys (+ structlog's own event/log_level).
        allowed = {"event", "log_level", "stage", "status", "source_ref"}
        assert set(entry).issubset(allowed), (
            f"unexpected keys in record_event log (LGPD surface): {set(entry) - allowed}"
        )
