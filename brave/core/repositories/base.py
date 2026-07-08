"""Repository interfaces (typing.Protocol) for the Brave medallion layers.

A pure data-access seam: each Protocol declares the query methods extracted from
the inline SQLAlchemy statements in the core services (and the DLQ read in the
router), so those queries live in one place and callers can inject a fake for
tests.

Session ownership (invariant): every method receives a caller-owned SQLAlchemy
Session. Repositories may ``flush()`` but MUST NEVER ``commit()`` — the caller
(Celery task / FastAPI endpoint) owns the transaction boundary.
"""

import uuid
from typing import Protocol

from sqlalchemy.orm import Session

from brave.core.models import MarRecord, NascenteRecord, RioRecord


class NascenteRepository(Protocol):
    """Read access to the Nascente (raw payload) layer."""

    def find_by_hash_scoped(
        self,
        session: Session,
        content_hash: str,
        uf: str,
        entity_type: str,
    ) -> NascenteRecord | None:
        """Exact content-hash match, scoped by territorial key (UF + entity_type)."""
        ...


class RioRepository(Protocol):
    """Read/write access to the Rio (working) layer."""

    def get(self, session: Session, rio_id: uuid.UUID) -> RioRecord | None:
        """Fetch a RioRecord by primary key."""
        ...

    def get_by_canonical_key(
        self, session: Session, canonical_key: str
    ) -> RioRecord | None:
        """Fetch a RioRecord by its unique canonical_key."""
        ...

    def get_by_nascente_id(
        self, session: Session, nascente_id: uuid.UUID
    ) -> RioRecord | None:
        """Fetch a RioRecord by its source nascente_id."""
        ...

    def add(self, session: Session, rio: RioRecord) -> None:
        """Persist a new RioRecord and flush (never commit)."""
        ...

    def find_dedup_candidates(
        self,
        session: Session,
        uf: str,
        municipio_id: str,
        entity_type: str,
        embedding: list[float],
        limit: int = 10,
    ) -> list[RioRecord]:
        """Territorial-key-blocked pgvector nearest neighbours, cosine-ordered."""
        ...


class MarRepository(Protocol):
    """Read/write access to the Mar (canonical) layer."""

    def get_active_by_source_ref(
        self, session: Session, source_ref: str
    ) -> MarRecord | None:
        """Fetch the active (non-superseded) MarRecord for a source_ref."""
        ...

    def add(self, session: Session, mar: MarRecord) -> None:
        """Persist a new MarRecord and flush (never commit)."""
        ...

    def supersede(
        self, session: Session, old_mar: MarRecord, new_mar: MarRecord
    ) -> None:
        """Point old_mar at new_mar, add new_mar, and flush — one atomic step."""
        ...


class DlqRepository(Protocol):
    """Read access to the reliability review DLQ (routing='dlq' rows in the Rio layer)."""

    def list_dlq(
        self,
        session: Session,
        uf: str | None = None,
        entity_type: str | None = None,
        limit: int = 50,
    ) -> list[RioRecord]:
        """List DLQ RioRecords, optionally filtered by uf and entity_type."""
        ...
