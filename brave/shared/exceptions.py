"""Central Brave exception hierarchy.

Every Brave-raised exception derives from :class:`BraveError`, so callers can
catch the whole family with a single ``except BraveError`` while still targeting
a specific failure mode when needed.

The concrete classes below are the single source of truth. Their historical
modules re-export them as thin shims so every existing importer and ``except``
clause keeps working unchanged:

  - ``brave.tasks.pipeline``            → TransientError, PermanentError
  - ``brave.observability.cost_guard``  → CostGuardError
  - ``brave.compliance.gate``           → ComplianceError
  - ``brave.lanes.tripadvisor.client``  → SessionExpiredError / SessionMissingError
    (defined there as subclasses of SourceSessionError to keep the exact,
    already-imported class objects — and thus every ``except`` tuple — intact)

Hierarchy::

    BraveError
    ├── TransientError      — retry with backoff (network flap, DB timeout)
    ├── PermanentError      — quarantine, do not retry (malformed payload)
    ├── ComplianceError     — D-11 compliance gate failure (LGPD/BSP)
    ├── CostGuardError      — daily USD budget exceeded (operational halt)
    └── SourceError         — external-source failure (fetch/scrape/session)
        └── SourceSessionError  — source session missing or expired

All classes historically subclassed ``Exception`` directly and had no relation
to one another. Re-parenting them under ``BraveError`` is safe: ``BraveError``
is an ``Exception``, so every existing ``except SpecificError`` and
``except Exception`` clause behaves identically; the only new capability is the
family-wide ``except BraveError``.
"""


class BraveError(Exception):
    """Base class for every Brave-raised exception."""


class TransientError(BraveError):
    """Transient failure — retry with backoff (network, DB timeout, etc.)."""


class PermanentError(BraveError):
    """Permanent failure — quarantine, do not retry (malformed payload, etc.)."""


class ComplianceError(BraveError):
    """Raised when any D-11 compliance gate condition fails.

    Always blocks the send — never advisory. The Celery task or endpoint that
    calls the send-path gate must catch ComplianceError and abort the send
    operation. Do NOT catch ComplianceError and proceed anyway — that defeats
    the gate.

    The error message always identifies which condition failed (for audit).
    """


class CostGuardError(BraveError):
    """Raised by the cost guard when the daily USD budget is exceeded.

    This is an operational halt, not a bug. The Celery task should catch this,
    log appropriately (without leaking budget details), and abort the LLM call.
    """


class SourceError(BraveError):
    """External-source failure (fetch, scrape, or session problem)."""


class SourceSessionError(SourceError):
    """External-source session is missing or expired.

    Lane-specific session errors (e.g. TripAdvisor's SessionExpiredError and
    SessionMissingError) subclass this so generic callers may catch the whole
    session-failure family with ``except SourceSessionError``.
    """
