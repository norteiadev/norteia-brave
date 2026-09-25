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
  - ``brave.domains.tripadvisor.client`` → SessionExpiredError / SessionMissingError
    (defined there as subclasses of SourceSessionError to keep the exact,
    already-imported class objects — and thus every ``except`` tuple — intact)

Hierarchy::

    BraveError
    ├── TransientError      — retry with backoff (network flap, DB timeout)
    │   └── ApiDown         — norteia-api down (cached probe); Mar push stays pending
    ├── PermanentError      — quarantine, do not retry (malformed payload)
    ├── ComplianceError     — D-11 compliance gate failure (LGPD/BSP)
    ├── CostGuardError      — daily USD budget exceeded (operational halt)
    ├── ProviderBalanceError — paid provider billing wall (no credit/quota left)
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


class ApiDown(TransientError):  # noqa: N818 — name locked by the design (Q11)
    """norteia-api confirmed down by the cached health probe; the push stays pending."""


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


class ProviderBalanceError(BraveError):
    """Raised when a paid external provider reports a billing wall (no credit/quota left).

    Distinct from CostGuardError: CostGuardError is OUR internal daily-budget ceiling,
    checked before dispatch. ProviderBalanceError is the PROVIDER telling us it has no
    money/quota left, discovered only after a real call. Callers must let this propagate
    uncaught (never degrade to a "no attempt" floor) — the motor pauses on it instead.
    """

    def __init__(self, provider: str, message: str = "") -> None:
        self.provider = provider
        super().__init__(message or f"{provider}: sem saldo/quota")


# Status codes that mean "billing wall" across the paid providers this plan covers
# (OpenRouter 402, Tavily 432/433 plan/credit limit).
_BALANCE_STATUS_CODES = frozenset({402, 432, 433})
_BALANCE_MESSAGE_MARKERS = ("credit balance is too low", "insufficient_quota", "billing")


def raise_if_balance_wall(provider: str, *, status_code: int | None = None, message: str = "") -> None:
    """Raise ProviderBalanceError(provider) if status_code/message signal a billing wall.

    No-op (returns) otherwise — the caller's normal error handling continues unchanged.
    """
    if status_code in _BALANCE_STATUS_CODES:
        raise ProviderBalanceError(provider, message)
    lowered = message.lower()
    if any(marker in lowered for marker in _BALANCE_MESSAGE_MARKERS):
        raise ProviderBalanceError(provider, message)


class SourceError(BraveError):
    """External-source failure (fetch, scrape, or session problem)."""


class SourceSessionError(SourceError):
    """External-source session is missing or expired.

    Lane-specific session errors (e.g. TripAdvisor's SessionExpiredError and
    SessionMissingError) subclass this so generic callers may catch the whole
    session-failure family with ``except SourceSessionError``.
    """
