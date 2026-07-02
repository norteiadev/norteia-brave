"""Repository layer — the data-access seam for the Brave medallion tables.

Import a Protocol (for type hints / test injection) or a concrete SQLAlchemy
implementation (for wiring) from here.
"""

from brave.core.repositories.base import (
    DlqRepository,
    MarRepository,
    NascenteRepository,
    RioRepository,
)
from brave.core.repositories.sqlalchemy import (
    SqlAlchemyDlqRepository,
    SqlAlchemyMarRepository,
    SqlAlchemyNascenteRepository,
    SqlAlchemyRioRepository,
)

__all__ = [
    "DlqRepository",
    "MarRepository",
    "NascenteRepository",
    "RioRepository",
    "SqlAlchemyDlqRepository",
    "SqlAlchemyMarRepository",
    "SqlAlchemyNascenteRepository",
    "SqlAlchemyRioRepository",
]
