"""Domain models for the ``mtur`` source (Phase G).

The Mtur/default domain has no ORM tables of its own — it persists through the
shared kernel entities (``NascenteRecord`` / ``RioRecord`` / ``MarRecord`` in
``brave.core.models``). Re-exported here so callers can depend on the domain
surface (``brave.domains.mtur.models``) rather than reaching into the kernel
directly. The LLM extraction schemas live in ``brave.domains.mtur.dtos``.
"""

from __future__ import annotations

from brave.core.models import MarRecord, NascenteRecord, RioRecord

__all__ = ["MarRecord", "NascenteRecord", "RioRecord"]
