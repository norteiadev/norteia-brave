"""ManualService — CRUD over Nascente/Rio for operator-authored records (Phase G).

Every mutation (create/update) is gated by :func:`require_editing_unlocked`, the
domain-layer twin of the Phase C ``require_editing_unlocked`` FastAPI dependency:
it reads the live engine mode (``brave.core.engine``) and refuses to write while
the mode is LIGADO. Reads (``get``) are ungated.

``store_raw`` and ``process_nascente_record`` are imported at module scope so they
are patchable at ``brave.domains.manual.services.<name>`` in unit tests (no DB).

Import posture (D-18): kernel only (core.engine, core.nascente, core.rio,
config). No other domain is imported.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from brave.config.settings import ScoreConfig
from brave.core import engine as collection_engine
from brave.core.nascente.service import store_raw
from brave.core.rio.routing import process_nascente_record
from brave.domains.manual.exceptions import EditingLockedError
from brave.domains.manual.repositories import SOURCE, ManualRepository

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from brave.core.models import NascenteRecord, RioRecord


def require_editing_unlocked(redis: Any, *, session: Session | None = None) -> None:
    """Domain-layer edit-lock gate (Phase C parity).

    Raises :class:`EditingLockedError` when the engine mode is LIGADO (lock
    engaged). PAUSADO/DESLIGADO unlock editing and this is a no-op. ``session`` is
    forwarded to :func:`brave.core.engine.is_editing_unlocked` so a mode read after
    a Redis flush self-heals from the durable ``config_settings`` row (Phase D).
    """
    if not collection_engine.is_editing_unlocked(redis, session=session):
        raise EditingLockedError(
            "Edição travada: o motor está LIGADO. Pause o motor (PAUSADO) para editar."
        )


class ManualService:
    """Create / update / read operator-authored territorial records."""

    def __init__(self, repository: ManualRepository | None = None) -> None:
        self._repo = repository or ManualRepository()

    def create(
        self,
        session: Session,
        redis: Any,
        *,
        entity_type: str,
        uf: str,
        name: str,
        municipio_id: str | None = None,
        canonical: dict[str, Any] | None = None,
        completude_value: float = 100.0,
        corroboracao_value: float = 0.0,
        atualidade_value: float = 100.0,
        config: ScoreConfig | None = None,
        source_ref: str | None = None,
        run_rio: bool = True,
    ) -> RioRecord | NascenteRecord:
        """Author a new manual record (mutation — edit-lock gated).

        Writes a ``source="manual"`` Nascente row (origem=100, validação humana=100)
        then, when ``run_rio`` is True, runs the Rio pipeline and returns the
        RioRecord; otherwise returns the NascenteRecord.

        Raises:
            EditingLockedError: when the engine mode is LIGADO.
        """
        require_editing_unlocked(redis, session=session)

        payload = self._repo.build_payload(
            entity_type=entity_type,
            uf=uf,
            name=name,
            municipio_id=municipio_id,
            canonical=canonical,
            completude_value=completude_value,
            corroboracao_value=corroboracao_value,
            atualidade_value=atualidade_value,
        )
        ref = source_ref or self._repo.make_source_ref(entity_type, uf, municipio_id, name)
        nascente = store_raw(
            session=session,
            source=SOURCE,
            source_ref=ref,
            entity_type=entity_type,
            uf=uf.upper(),
            payload=payload,
        )
        if run_rio:
            return process_nascente_record(session, nascente, config or ScoreConfig())
        return nascente

    def update(
        self,
        session: Session,
        redis: Any,
        *,
        source_ref: str,
        entity_type: str,
        uf: str,
        name: str,
        municipio_id: str | None = None,
        canonical: dict[str, Any] | None = None,
        completude_value: float = 100.0,
        corroboracao_value: float = 0.0,
        atualidade_value: float = 100.0,
        config: ScoreConfig | None = None,
        run_rio: bool = True,
    ) -> RioRecord | NascenteRecord:
        """Revise a manual record under a known ``source_ref`` (mutation — gated).

        Re-saving the same ``source_ref`` with a new payload supersedes the prior
        Nascente version (D-03) and re-runs Rio. Same edit-lock guard as create.
        """
        return self.create(
            session,
            redis,
            entity_type=entity_type,
            uf=uf,
            name=name,
            municipio_id=municipio_id,
            canonical=canonical,
            completude_value=completude_value,
            corroboracao_value=corroboracao_value,
            atualidade_value=atualidade_value,
            config=config,
            source_ref=source_ref,
            run_rio=run_rio,
        )

    def get(self, session: Session, source_ref: str) -> NascenteRecord | None:
        """Read the active manual Nascente row for a ref (ungated)."""
        return self._repo.get_active(session, source_ref)
