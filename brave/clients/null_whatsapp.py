"""In-package offline WhatsApp client stub (production-safe).

Used when AppConfig.run_real_externals is False (local dev, CI, and any environment
without Twilio credentials). Records sends locally without any network I/O.

Lives in brave/ (NOT tests/) so production code never imports from the test tree
(design rule D-09, mirrors null_norteia_api.py placement).

Production code selects between NullWhatsAppClient and TwilioWhatsAppClient (Phase 3)
via the WhatsAppClientProtocol seam. This stub is production-safe: it cannot leak
credentials or transmit messages.

FakeWhatsAppClient (tests/fakes/fake_whatsapp.py) is the test-only counterpart;
tests should import FakeWhatsAppClient, not this module.
"""

from __future__ import annotations

import uuid
from typing import Any


class NullWhatsAppClient:
    """No-network WhatsApp client stub (structural protocol match).

    Production-safe: never transmits. Records calls in sent_messages for
    observability/debugging without leaking to any external service.

    Docstring intent: This is for production code (brave/) only.
    Never import FakeWhatsAppClient (tests/fakes/) from production code.
    """

    def __init__(self) -> None:
        self.sent_messages: list[dict[str, Any]] = []

    async def send_template(
        self,
        to: str,
        template: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """Record a template send without network transmission.

        Args:
            to:       Recipient phone number in E.164 format.
            template: BSP-approved template name.
            params:   Template parameter dict.

        Returns:
            Synthetic delivery status dict (message_sid is a local UUID, status='queued').
        """
        message_sid = str(uuid.uuid4())
        record = {
            "to": to,
            "template": template,
            "params": params,
            "sid": message_sid,
        }
        self.sent_messages.append(record)
        return {"message_sid": message_sid, "status": "queued"}
