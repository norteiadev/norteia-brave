"""Exceptions for the ``manual`` source domain (Phase G)."""

from __future__ import annotations


class ManualDomainError(Exception):
    """Base class for errors in the manual (operator-authored) domain."""


class EditingLockedError(ManualDomainError):
    """A manual mutation was attempted while the editing lock is engaged.

    Raised by the domain-layer equivalent of the Phase C
    ``require_editing_unlocked`` gate: the engine mode is LIGADO, so a steward may
    not hand-edit records out from under an in-flight sweep. Pause the engine
    (PAUSADO) to unlock. The HTTP edge maps this onto ``423 Locked``.
    """
