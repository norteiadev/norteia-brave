"""Exceptions for the ``mtur`` source domain (Phase G).

The Mtur/default producers are resilient by design — a bad record is quarantined
(``brave.core.quarantine.quarantine_poison``) rather than raised, so a single
poison row never discards a UF sweep. ``MturDomainError`` is the domain's base
class for the few genuinely-fatal conditions (e.g. missing injected deps).
"""

from __future__ import annotations


class MturDomainError(Exception):
    """Base class for fatal errors in the Mtur/default domain."""
