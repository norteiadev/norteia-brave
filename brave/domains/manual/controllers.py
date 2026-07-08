"""ManualDomain — SourceDomain implementation for operator-authored records.

Manual is not a sweep lane: ``discover`` is a no-op (records are created on demand
via the CRUD facade, not fanned out per UF). ``score_input`` reflects the
human-authoritative inputs; ``enrich`` is empty (nothing to auto-enrich). The
``create``/``update``/``get`` facade delegates to :class:`ManualService`, which
enforces the Phase C editing lock on every mutation.

Import posture (D-18): kernel only; no other domain imported.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from brave.core.score.schemas import ScoreInput
from brave.domains.base import SweepDispatch, build_score_input
from brave.domains.manual.services import ManualService


class ManualDomain:
    """The ``manual`` (operator-authored) collection source."""

    name = "manual"
    produces: tuple[str, ...] = ("destination", "attraction")

    def __init__(self, service: ManualService | None = None) -> None:
        self._service = service or ManualService()

    async def discover(self, uf: str, run_rio: bool = True) -> None:
        """No-op: manual records are authored on demand, never swept per-UF."""
        return None

    def enrich(self, rio: Any) -> dict[str, Any]:
        """Manual records are human-authoritative — no automated enrichment."""
        return {}

    def score_input(self, payload: Mapping[str, Any]) -> ScoreInput:
        """Map a manual payload onto the ScoreInput (origem/validação = 100)."""
        return build_score_input(payload)

    def sweep_plan(
        self, uf: str, *, depth: str, lane: str, nascente_only: bool
    ) -> list[SweepDispatch]:
        """No producers: manual records are authored on demand, never swept per-UF."""
        return []

    def beat_entries(self, uf_list: list[str]) -> dict[str, dict]:
        """No scheduled task: manual is not a sweep lane (never in enabled_sources)."""
        return {}

    # --- CRUD facade (delegates to ManualService; mutations are edit-lock gated) ---
    def create(self, *args: Any, **kwargs: Any) -> Any:
        """Author a manual record. See :meth:`ManualService.create`."""
        return self._service.create(*args, **kwargs)

    def update(self, *args: Any, **kwargs: Any) -> Any:
        """Revise a manual record. See :meth:`ManualService.update`."""
        return self._service.update(*args, **kwargs)

    def get(self, *args: Any, **kwargs: Any) -> Any:
        """Read a manual record. See :meth:`ManualService.get`."""
        return self._service.get(*args, **kwargs)


# Registry descriptor singleton — stateless, cheap to construct (no I/O).
MANUAL_DOMAIN = ManualDomain()
