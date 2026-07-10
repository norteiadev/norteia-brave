"""TripAdvisorDomain — SourceDomain implementation for the ``tripadvisor`` lane.

``discover`` wraps :class:`TripAdvisorAtrativosIngest` (the per-UF path the
``brave.sweep_tripadvisor`` task drives). TA's bespoke session/bootstrap
lifecycle (SessionExpired fail-fast, needs-bootstrap marker, sweep_progress,
engine idle latch) stays owned by the task — the domain does not flatten it into a
generic path. ``score_input`` / ``enrich`` are pure.

Import posture (D-18): kernel + clients only; the heavy ingest/client imports are
lazy inside ``discover`` so the registry can import this module cheaply. Nothing
here imports another domain.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from brave.core.score.schemas import ScoreInput
from brave.domains.base import SweepDispatch, build_score_input

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.orm import Session

    from brave.clients.base import TripAdvisorClientProtocol
    from brave.config.settings import ScoreConfig, TripAdvisorConfig
    from brave.domains.tripadvisor.ibge import IbgeMunicipio


class TripAdvisorDomain:
    """The ``tripadvisor`` collection source.

    Satisfies :class:`brave.domains.base.SourceDomain` structurally.
    """

    name = "tripadvisor"
    produces: tuple[str, ...] = ("destination", "attraction")

    async def discover(
        self,
        uf: str,
        run_rio: bool = True,
        *,
        ta_client: TripAdvisorClientProtocol | None = None,
        session: Session | None = None,
        config: ScoreConfig | None = None,
        ibge_records: list[IbgeMunicipio] | None = None,
        destino_rio_map: dict[str, tuple[uuid.UUID, str]] | None = None,
        ta_config: TripAdvisorConfig | None = None,
    ) -> None:
        """Run one per-UF TripAdvisor attractions sweep.

        The task layer injects the TA client, session, config and the pre-built
        IBGE + parent-destino maps. Raises ``ValueError`` when the mandatory deps
        are missing (the registry descriptor is default-constructed).
        """
        if ta_client is None or session is None or config is None or ibge_records is None:
            raise ValueError(
                "TripAdvisorDomain.discover requires ta_client=, session=, config= and "
                "ibge_records= (injected by the task layer)"
            )
        from brave.domains.tripadvisor.atrativos import TripAdvisorAtrativosIngest

        ingest = TripAdvisorAtrativosIngest(
            ta_client=ta_client,
            session=session,
            config=config,
            ibge_records=ibge_records,
            destino_rio_map=destino_rio_map,
            ta_config=ta_config,
        )
        await ingest.produce(uf, run_rio=run_rio)

    def enrich(self, rio: Any) -> dict[str, Any]:
        """Return the TA review-signal enrichment carried on a Rio record."""
        normalized = getattr(rio, "normalized", None) or {}
        return {
            "ta_url": normalized.get("ta_url"),
            "review_signals": normalized.get("review_signals"),
            "num_reviews": normalized.get("num_reviews"),
            "rating": normalized.get("rating"),
        }

    def score_input(self, payload: Mapping[str, Any]) -> ScoreInput:
        """Map a Nascente payload / Rio normalized dict onto the ScoreInput."""
        return build_score_input(payload)

    def sweep_plan(
        self,
        uf: str,
        *,
        depth: str,
        lane: str,
        nascente_only: bool,
        max_per_uf: int | None = None,
    ) -> list[SweepDispatch]:
        """Route TripAdvisor to its single per-UF producer (atrativos-only).

        TA is attractions-only (parent destinos must be seeded via the Mtur sweep
        first — the oa3 fix), so ``lane`` does not branch the plan. The depth gate is
        applied INSIDE ``sweep_tripadvisor`` (``run_rio`` derived from ``depth``), and
        TA's bespoke session/bootstrap lifecycle stays owned by that task — the domain
        does not flatten it into a generic path.

        ``max_per_uf`` (operator test-run throttle, ``None`` = no cap) is threaded into
        the producer kwargs so ``sweep_tripadvisor`` can stop each UF after N attractions.
        """
        return [
            SweepDispatch(
                "brave.sweep_tripadvisor",
                (uf,),
                {"depth": depth, "max_per_uf": max_per_uf},
            )
        ]

    def beat_entries(self, uf_list: list[str]) -> dict[str, dict]:
        """The TA session keep-alive beat — TA's ONLY scheduled task.

        TA sweeps are start-only (dispatched on demand via /engine/start), so there
        are no per-UF beat rows. Fires every ``keepalive_interval_seconds`` to
        maintain the sliding TTL on active TA sessions (260629-p2v). No
        ``options.queue`` (single-queue model). Byte-identical to the former
        ``build_beat_schedule`` ``"tripadvisor"`` branch.
        """
        from datetime import timedelta  # noqa: PLC0415

        from brave.config.settings import TripAdvisorConfig  # noqa: PLC0415

        return {
            "ta-keepalive": {
                "task": "brave.ta_keepalive",
                "schedule": timedelta(
                    seconds=TripAdvisorConfig().keepalive_interval_seconds
                ),
            }
        }


# Registry descriptor singleton — stateless, cheap to construct (no I/O).
TRIPADVISOR_DOMAIN = TripAdvisorDomain()
