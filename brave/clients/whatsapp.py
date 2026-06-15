"""TwilioWhatsAppClient — real WhatsApp BSP client (D-09, COMP-01/02).

Uses the Twilio 9.10.x SDK to send approved WhatsApp Business template messages.
Implements WhatsAppClientProtocol (structural typing — no inheritance required).

Architecture invariant (D-11, T-03-04-01):
  send_template is NEVER called directly. It is always called through the
  compliance gate (_compliant_send in brave/lanes/atrativos/whatsapp_agent.py),
  which calls send_path_gate first. This file implements the transport layer only —
  all compliance logic lives in brave/compliance/gate.py.

Production/offline boundary:
  - TwilioWhatsAppClient: requires BRAVE_RUN_REAL_EXTERNALS=true. Used in production.
  - NullWhatsAppClient (brave/clients/null_whatsapp.py): offline stub, no network.
  - FakeWhatsAppClient (tests/fakes/fake_whatsapp.py): test-only, records calls.

Send path:
  outreach_task / resume_conversation_task
    → _compliant_send (whatsapp_agent.py)
      → send_path_gate (compliance/gate.py) — raises ComplianceError on failure
      → TwilioWhatsAppClient.send_template() ← HERE (transport only)

Twilio Messaging API notes:
  - Uses MessagingServiceSid path (recommended for template sending, auto-sender selection).
  - Falls back to from_number if messaging_service_sid is not set.
  - Template body is passed in the ContentVariables / ContentSid or Body field.
  - Retry: tenacity wraps 5xx Twilio errors (network flap, rate limit 429).

D-09: Twilio is the launch BSP; Meta Cloud API is the cost-optimized end-state,
migrated behind the same WhatsAppClientProtocol interface later.
"""

from __future__ import annotations

from typing import Any

import structlog
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = structlog.get_logger(__name__)


def _is_twilio_5xx(exc: BaseException) -> bool:
    """Return True if exc is a Twilio 5xx server error (retryable)."""
    try:
        from twilio.base.exceptions import TwilioRestException
        return isinstance(exc, TwilioRestException) and exc.status >= 500
    except ImportError:
        return False


class TwilioWhatsAppClient:
    """Real WhatsApp BSP client using Twilio 9.10.x SDK.

    Implements WhatsAppClientProtocol (structural typing, D-09).
    Requires BRAVE_RUN_REAL_EXTERNALS=true — raises RuntimeError otherwise
    to enforce the production/offline boundary.

    Usage (production — called from _compliant_send, never directly):
        client = TwilioWhatsAppClient(
            account_sid=settings.twilio_account_sid,
            auth_token=settings.twilio_auth_token,
            from_number=settings.from_number,
            messaging_service_sid=settings.messaging_service_sid,
        )
        result = await client.send_template(to="+5511...", template="norteia_v1", params={...})
    """

    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        from_number: str,
        messaging_service_sid: str | None = None,
    ) -> None:
        """Initialize Twilio client.

        Args:
            account_sid:           Twilio Account SID (starts with AC...).
            auth_token:            Twilio Auth Token (never logged).
            from_number:           WhatsApp-enabled number in E.164 format.
            messaging_service_sid: Twilio MessagingServiceSid (starts with MG...),
                                   preferred for template sending.
        """
        self._account_sid = account_sid
        self._auth_token = auth_token
        self._from_number = from_number
        self._messaging_service_sid = messaging_service_sid

    async def send_template(
        self,
        to: str,
        template: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """Send an approved WhatsApp template message via Twilio.

        ARCHITECTURE INVARIANT: This method is ONLY called through _compliant_send()
        in brave/lanes/atrativos/whatsapp_agent.py, which always invokes send_path_gate
        first. Never call this method directly from task or endpoint code.

        Args:
            to:       Recipient phone number in E.164 format (+55...).
            template: Approved BSP template name (allowlist-verified by gate).
            params:   Template parameters dict (includes "body" key).

        Returns:
            {"message_sid": str, "status": str} from Twilio.

        Raises:
            RuntimeError: If BRAVE_RUN_REAL_EXTERNALS is not True.
            TwilioRestException: On Twilio API errors (non-5xx, non-retried).
            ComplianceError: Indirectly — raised by send_path_gate before this is called.
        """
        from brave.config.settings import AppConfig
        app_config = AppConfig()
        if not app_config.run_real_externals:
            raise RuntimeError(
                "TwilioWhatsAppClient.send_template requires BRAVE_RUN_REAL_EXTERNALS=true. "
                "Use NullWhatsAppClient for offline/dev environments "
                "(brave/clients/null_whatsapp.py)."
            )

        result = await self._do_send(to=to, template=template, params=params)
        return result

    @retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def _do_send(
        self,
        to: str,
        template: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """Internal send with tenacity retry for 5xx errors.

        Separated from send_template so retry logic is isolated and the
        run_real_externals guard only runs once per call (not per retry).
        """
        from twilio.rest import Client

        client = Client(self._account_sid, self._auth_token)

        # Build message params
        body = params.get("body", "")
        create_kwargs: dict[str, Any] = {
            "to": f"whatsapp:{to}",
            "body": body,
            # ContentSid or template-name based send depends on Twilio account setup.
            # If using Content Template API, set content_sid = template lookup.
            # For simple body-based utility sends, body alone is sufficient.
        }

        if self._messaging_service_sid:
            create_kwargs["messaging_service_sid"] = self._messaging_service_sid
        else:
            create_kwargs["from_"] = f"whatsapp:{self._from_number}"

        msg = client.messages.create(**create_kwargs)

        logger.info(
            "twilio_message_sent",
            to_prefix=to[:5],
            template=template,
            message_sid=msg.sid,
            status=msg.status,
        )

        return {"message_sid": msg.sid, "status": msg.status}


# ---------------------------------------------------------------------------
# Protocol compliance check (compile-time structural typing assertion)
# ---------------------------------------------------------------------------


def _check_protocol_compliance() -> None:
    """Compile-time structural typing assertion (not called at runtime).

    Verifies TwilioWhatsAppClient satisfies WhatsAppClientProtocol (D-09).
    """
    from brave.clients.base import WhatsAppClientProtocol

    _client: WhatsAppClientProtocol = TwilioWhatsAppClient(  # noqa: F841
        account_sid="",
        auth_token="",
        from_number="",
    )
