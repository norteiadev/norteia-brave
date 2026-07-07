"""MturRepository — data-access seam for the ``mtur`` domain (Phase G).

Two responsibilities, both lifted from where they were inlined before:
  - ``build_destino_rio_map(session, uf)``: the parent-destino lookup the TA
    atrativos producer needs (was inlined in ``brave.tasks.pipeline`` ~L1034).
    Maps ``municipio_id`` (7-digit IBGE) → ``(rio_id, source_ref)`` for every
    *destination* Rio record in the UF — Mtur/IBGE ``origem=100`` records are the
    authoritative parents, so a destinos/default sweep must precede an atrativos one.
  - ``seed_csv_dir`` / ``latest_seed_csv``: the bundled Mtur CSV seed path
    (``data/mtur/municipios_mtur_*.csv``), surfaced from the offline ``MturClient``.

Import posture (D-18): kernel + ``brave.clients`` only.
"""

from __future__ import annotations

import pathlib
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import select

from brave.core.models import NascenteRecord, RioRecord

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class MturRepository:
    """Stateless data-access helpers for the Mtur/default domain.

    The ``Session`` is passed per call and the caller owns the transaction
    (mirrors ``brave.core.repositories`` conventions — reads only, never commits).
    """

    def build_destino_rio_map(
        self, session: Session, uf: str
    ) -> dict[str, tuple[uuid.UUID, str]]:
        """Map ``municipio_id`` (IBGE) → ``(rio_id, source_ref)`` for a UF's destinos.

        Queries every *destination* RioRecord in ``uf`` joined to its Nascente row.
        Rows without a ``municipio_id`` are skipped (they cannot anchor an atrativo).

        Args:
            session: SQLAlchemy Session (caller owns the transaction).
            uf: Two-letter Brazilian state code.

        Returns:
            ``{ibge_code: (rio_id, source_ref)}`` — the parent-destino map consumed
            by the TripAdvisor atrativos producer.
        """
        rows = session.execute(
            select(RioRecord.id, NascenteRecord.source_ref, RioRecord.municipio_id)
            .join(NascenteRecord, RioRecord.nascente_id == NascenteRecord.id)
            .where(
                NascenteRecord.entity_type == "destination",
                RioRecord.uf == uf,
            )
        ).all()
        return {
            row.municipio_id: (row.id, row.source_ref)
            for row in rows
            if row.municipio_id
        }

    def seed_csv_dir(self) -> pathlib.Path:
        """Directory holding the bundled Mtur CSV seeds (``data/mtur/``)."""
        from brave.clients.mtur import DATA_PATH

        return DATA_PATH

    def latest_seed_csv(self) -> pathlib.Path | None:
        """Newest ``municipios_mtur_*.csv`` under the seed dir, or None if absent."""
        candidates = sorted(self.seed_csv_dir().glob("municipios_mtur_*.csv"), reverse=True)
        return candidates[0] if candidates else None
