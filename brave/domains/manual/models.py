"""Domain models for the ``manual`` source (Phase G).

Manual records persist through the shared kernel entities; re-exported for a
stable ``brave.domains.manual.models`` surface.
"""

from __future__ import annotations

from brave.core.models import MarRecord, NascenteRecord, RioRecord

__all__ = ["MarRecord", "NascenteRecord", "RioRecord"]
