"""ContactFinderAgent — advances sub_state from 'discovered' to 'contacts_found'.

Fetches phone, website, and social handles via Places Details using the place_id
stored in rio.normalized["place_id_cache"] (D-04).

Sub-state transition (D-01, D-02):
  discovered → contacts_found
  Idempotency guard: if rio.sub_state != "discovered", returns immediately.
  Writes audit row on every successful transition.
  Uses flag_modified for JSONB normalized column mutation (Phase 2 lesson).

D-18 boundary: no imports from brave.lanes.destinos or brave.tasks.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from brave.lanes.atrativos.schemas import ContactResult
from brave.observability.audit import write_audit

if TYPE_CHECKING:
    from brave.clients.base import PlacesClientProtocol
    from brave.core.models import RioRecord

logger = structlog.get_logger(__name__)


class ContactFinderAgent:
    """ContactFinderAgent — finds phone, website, IG for an atrativo.

    Reads the place_id from rio.normalized["place_id_cache"] (stored by
    DiscoveryAgent, D-04) and calls places_client.place_details to fetch
    contact information.

    Sub-state transition: discovered → contacts_found.
    Idempotency guard: returns immediately if sub_state != "discovered".
    Writes audit row and uses flag_modified on the JSONB normalized column.

    D-18 boundary: no imports from brave.lanes.destinos.

    Args:
        places_client: PlacesClientProtocol implementation (real or fake).
        session:       SQLAlchemy synchronous Session.
    """

    def __init__(
        self,
        places_client: "PlacesClientProtocol",
        session: Session,
    ) -> None:
        self._places_client = places_client
        self._session = session

    async def run(self, rio: "RioRecord") -> None:
        """Advance sub_state from 'discovered' to 'contacts_found'.

        Idempotency guard: if rio.sub_state != "discovered", returns immediately.
        On success: fetches place details, builds ContactResult, mutates
        normalized JSONB dict with flag_modified, advances sub_state.

        Args:
            rio: RioRecord to advance. Must have sub_state="discovered"
                 and normalized["place_id_cache"] set by DiscoveryAgent.
        """
        # Idempotency guard (D-01)
        if rio.sub_state != "discovered":
            return

        # Retrieve place_id from normalized cache (D-04)
        normalized = rio.normalized or {}
        place_id: str = normalized.get("place_id_cache", "")
        if not place_id:
            logger.warning(
                "contact_finder_no_place_id",
                rio_id=str(rio.id),
            )
            return

        # Fetch contact details from Places
        details = await self._places_client.place_details(place_id)

        # Build ContactResult from Places details
        phone_raw: str | None = details.get("formatted_phone_number") or \
                                details.get("international_phone_number")

        # Normalize phone to E.164 if it looks like a Brazilian number
        phone_e164 = _normalize_phone_e164(phone_raw)

        contact = ContactResult(
            phone_e164=phone_e164,
            website=details.get("website"),
            ig_handle=_extract_ig_handle(details),
            email=None,  # Places API doesn't provide email
        )

        # Mutate normalized JSONB with flag_modified (Phase 2 lesson — T-02-06-04)
        new_normalized = dict(normalized)
        new_normalized["contacts"] = contact.model_dump()
        rio.normalized = new_normalized
        flag_modified(rio, "normalized")
        rio.sub_state = "contacts_found"

        # Write audit row (D-02)
        write_audit(
            session=self._session,
            action="sub_state_advanced",
            entity_type="attraction",
            record_id=rio.id if isinstance(rio.id, uuid.UUID) else None,
            before_state={"sub_state": "discovered"},
            after_state={"sub_state": "contacts_found"},
            actor="contact_finder_agent",
        )

        self._session.flush()

        logger.info(
            "contacts_found",
            rio_id=str(rio.id),
            has_phone=bool(phone_e164),
            has_website=bool(contact.website),
        )


# ---------------------------------------------------------------------------
# Phone normalization helper
# ---------------------------------------------------------------------------


def _normalize_phone_e164(phone_raw: str | None) -> str | None:
    """Attempt to normalize a raw phone string to E.164 format.

    Strips whitespace, dashes, parens, and dots. Adds +55 Brazil country code
    if the number looks like a local BR number.

    Args:
        phone_raw: Raw phone string from Places API (may be None).

    Returns:
        E.164-formatted string (e.g. "+5571999998888"), or None if not parseable.
    """
    if not phone_raw:
        return None

    # Strip formatting characters
    digits = "".join(c for c in phone_raw if c.isdigit() or c == "+")

    if not digits:
        return None

    # Already in E.164 format
    if digits.startswith("+"):
        return digits

    # Brazilian numbers: 10 or 11 digits → add +55
    if len(digits) in (10, 11):
        return f"+55{digits}"

    # Starts with 55 (BR country code without +)
    if digits.startswith("55") and len(digits) in (12, 13):
        return f"+{digits}"

    return None


def _extract_ig_handle(details: dict[str, Any]) -> str | None:
    """Extract IG handle from Places details if available.

    Google Places doesn't return IG handles directly, but may include
    them in 'url', 'website', or custom attributes. Returns None if
    no IG handle is found.

    Args:
        details: Places place_details dict.

    Returns:
        IG handle string (e.g. "@praiatest"), or None.
    """
    # Check for website containing instagram.com
    website = details.get("website", "") or ""
    if "instagram.com" in website:
        # Extract handle from URL like instagram.com/handle
        parts = website.rstrip("/").split("/")
        for i, part in enumerate(parts):
            if part in ("instagram.com", "www.instagram.com") and i + 1 < len(parts):
                handle = parts[i + 1].split("?")[0]
                if handle:
                    return f"@{handle}" if not handle.startswith("@") else handle

    return None
