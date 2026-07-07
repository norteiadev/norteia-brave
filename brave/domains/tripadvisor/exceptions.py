"""Exceptions for the ``tripadvisor`` source domain (Phase G).

Canonical re-export of the TA session errors (the real definitions live in
``brave.domains.tripadvisor.client``) plus the shared base they derive from, so
callers can catch them via ``brave.domains.tripadvisor.exceptions``.
"""

from __future__ import annotations

from brave.domains.tripadvisor.client import SessionExpiredError, SessionMissingError
from brave.shared.exceptions import SourceSessionError

__all__ = ["SessionExpiredError", "SessionMissingError", "SourceSessionError"]
