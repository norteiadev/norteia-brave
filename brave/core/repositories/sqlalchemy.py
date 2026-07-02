"""Concrete SQLAlchemy repositories — one per Protocol in ``base``.

Each method is a pure extraction of an inline query that previously lived in a
core service (routing, dedup, mar) or the DLQ router. The exact statement,
filters, ordering, and flush behaviour are preserved verbatim; the caller still
owns the transaction (these repos flush but never commit).

Note: this module is ``brave.core.repositories.sqlalchemy``; ``from sqlalchemy
import ...`` below is an absolute import that resolves to the installed
SQLAlchemy package, not this module.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from brave.core.models import MarRecord, NascenteRecord, RioRecord


class SqlAlchemyNascenteRepository:
    """SQLAlchemy implementation of the NascenteRepository Protocol."""

    def find_by_hash_scoped(
        self,
        session: Session,
        content_hash: str,
        uf: str,
        entity_type: str,
    ) -> NascenteRecord | None:
        return session.scalar(
            select(NascenteRecord).where(
                NascenteRecord.content_hash == content_hash,
                NascenteRecord.uf == uf,
                NascenteRecord.entity_type == entity_type,
            )
        )


class SqlAlchemyRioRepository:
    """SQLAlchemy implementation of the RioRepository Protocol."""

    def get(self, session: Session, rio_id: uuid.UUID) -> RioRecord | None:
        return session.get(RioRecord, rio_id)

    def get_by_canonical_key(
        self, session: Session, canonical_key: str
    ) -> RioRecord | None:
        return session.scalar(
            select(RioRecord).where(RioRecord.canonical_key == canonical_key)
        )

    def get_by_nascente_id(
        self, session: Session, nascente_id: uuid.UUID
    ) -> RioRecord | None:
        return session.scalar(
            select(RioRecord).where(RioRecord.nascente_id == nascente_id)
        )

    def add(self, session: Session, rio: RioRecord) -> None:
        session.add(rio)
        session.flush()

    def find_dedup_candidates(
        self,
        session: Session,
        uf: str,
        municipio_id: str,
        entity_type: str,
        embedding: list[float],
        limit: int = 10,
    ) -> list[RioRecord]:
        return list(
            session.scalars(
                select(RioRecord)
                .where(
                    RioRecord.uf == uf,
                    RioRecord.municipio_id == municipio_id,
                    RioRecord.entity_type == entity_type,
                    RioRecord.embedding.isnot(None),
                )
                .order_by(RioRecord.embedding.cosine_distance(embedding))
                .limit(limit)
            )
        )


class SqlAlchemyMarRepository:
    """SQLAlchemy implementation of the MarRepository Protocol."""

    def get_active_by_source_ref(
        self, session: Session, source_ref: str
    ) -> MarRecord | None:
        return session.scalar(
            select(MarRecord).where(
                MarRecord.source_ref == source_ref,
                MarRecord.superseded_by_id.is_(None),
            )
        )

    def add(self, session: Session, mar: MarRecord) -> None:
        session.add(mar)
        session.flush()

    def supersede(
        self, session: Session, old_mar: MarRecord, new_mar: MarRecord
    ) -> None:
        old_mar.superseded_by_id = new_mar.id
        session.add(new_mar)
        session.flush()


class SqlAlchemyDlqRepository:
    """SQLAlchemy implementation of the DlqRepository Protocol."""

    def list_dlq(
        self,
        session: Session,
        uf: str | None = None,
        entity_type: str | None = None,
        limit: int = 50,
    ) -> list[RioRecord]:
        query = select(RioRecord).where(RioRecord.routing == "dlq")
        if uf:
            query = query.where(RioRecord.uf == uf)
        if entity_type:
            query = query.where(RioRecord.entity_type == entity_type)
        query = query.limit(limit)
        return list(session.scalars(query).all())
