"""Fake WhatsApp client for offline testing.

FakeWhatsAppClient implements WhatsAppClientProtocol (structural typing, D-09).
Records sends — never transmits. Used by compliance gate tests and
WhatsAppAgent tests to assert outreach behavior without real Twilio calls.

This file is TEST-ONLY. Production code uses NullWhatsAppClient (brave/clients/)
for offline operation and TwilioWhatsAppClient (brave/clients/whatsapp.py) for
real sends. Never import FakeWhatsAppClient from production code.

Usage:
    from tests.fakes.fake_whatsapp import FakeWhatsAppClient

    client = FakeWhatsAppClient()
    result = await client.send_template(to="+5573...", template="norteia_v1", params={})
    assert len(client.sent_messages) == 1
    assert result["status"] == "sent"

    # Failure mode for testing graceful error handling:
    bad_client = FakeWhatsAppClient(should_fail=True)
    with pytest.raises(RuntimeError):
        await bad_client.send_template(...)
"""

from typing import Any

from brave.clients.base import WhatsAppClientProtocol


class FakeWhatsAppClient:
    """Fake WhatsApp client that records sends without transmitting.

    Structurally satisfies WhatsAppClientProtocol (D-09).
    Tracks all send_template calls for assertion in tests.

    CRITICAL: send_template in production is ALWAYS called through the
    send_path_gate (brave/compliance/gate.py), never directly. This fake
    is for testing the gate behavior and agent behavior — not for bypassing
    the gate in test setup.
    """

    def __init__(
        self,
        should_fail: bool = False,
    ) -> None:
        """Initialize with optional failure mode.

        Args:
            should_fail: If True, send_template raises RuntimeError to test
                         error-handling paths in the WhatsAppAgent.
        """
        self._should_fail = should_fail
        self.sent_messages: list[dict[str, Any]] = []

    async def send_template(
        self,
        to: str,
        template: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """Record a template send (no network I/O).

        Args:
            to:       Recipient phone in E.164 format.
            template: Approved template name.
            params:   Template parameters dict.

        Returns:
            Synthetic {"message_sid": "fake-sid-001", "status": "sent"}.

        Raises:
            RuntimeError: If should_fail=True was set at construction.
        """
        if self._should_fail:
            raise RuntimeError("FakeWhatsAppClient: simulated send failure")
        record = {"to": to, "template": template, "params": params}
        self.sent_messages.append(record)
        return {"message_sid": "fake-sid-001", "status": "sent"}


# Structural type check: FakeWhatsAppClient must satisfy WhatsAppClientProtocol
def _check_protocol_compliance() -> None:
    """Compile-time structural typing assertion (not called at runtime)."""
    _client: WhatsAppClientProtocol = FakeWhatsAppClient()  # noqa: F841
