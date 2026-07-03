"""MturDomain — SourceDomain implementation for the "default" Mtur/Places track.

``discover`` wraps the Mtur destino seed (:class:`MturSeedIngest`) exactly as the
``brave.sweep_uf`` task inlines it today; the Google Places attraction chain
(discover_atrativo → find_contacts → gather_signals) stays task-driven and is
untouched by this cut. ``score_input`` / ``enrich`` are pure and dependency-free.

Import posture (D-18): kernel + clients only, lazily. Nothing here imports another
domain. The heavy client / ingest imports live inside ``discover`` so the registry
(and the ``brave.lanes`` re-export shims) can import this module cheaply.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from brave.core.score.schemas import ScoreInput
from brave.domains.base import SweepDispatch, build_score_input

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from brave.clients.base import MturClientProtocol
    from brave.config.settings import ScoreConfig


class MturDomain:
    """The ``mtur`` (a.k.a. ``default``) collection source.

    Satisfies :class:`brave.domains.base.SourceDomain` structurally.
    """

    name = "mtur"
    # Legacy engine source name (brave.core.engine `_VALID_SOURCES`, brave:engine:source).
    aliases: tuple[str, ...] = ("default",)
    produces: tuple[str, ...] = ("destination", "attraction")

    async def discover(
        self,
        uf: str,
        run_rio: bool = True,
        *,
        session: Session | None = None,
        config: ScoreConfig | None = None,
        mtur_client: MturClientProtocol | None = None,
    ) -> None:
        """Run one Mtur destino-seed sweep for a UF (mirrors ``brave.sweep_uf``).

        The task layer injects ``session=`` and ``config=`` (and optionally a fake
        ``mtur_client=`` under test). Raises ``ValueError`` if the mandatory deps
        are missing — the registry descriptor is default-constructed and cannot
        discover without them.
        """
        if session is None or config is None:
            raise ValueError(
                "MturDomain.discover requires session= and config= (injected by the task layer)"
            )
        from brave.domains.mtur.services import MturSeedIngest

        client = mtur_client
        if client is None:
            from brave.clients.mtur import MturClient

            client = MturClient()
        await MturSeedIngest(client, session, config).produce(uf, run_rio=run_rio)

    def enrich(self, rio: Any) -> dict[str, Any]:
        """Return the Places-track enrichment signals carried on a Rio record."""
        normalized = getattr(rio, "normalized", None) or {}
        return {
            "place_id_cache": normalized.get("place_id_cache"),
            "contacts": normalized.get("contacts"),
            "contact": normalized.get("contact"),
            "signal": normalized.get("signal"),
            "weekday_text": normalized.get("weekday_text"),
        }

    def score_input(self, payload: Mapping[str, Any]) -> ScoreInput:
        """Map a Nascente payload / Rio normalized dict onto the §7.6 ScoreInput."""
        return build_score_input(payload)

    def sweep_plan(
        self, uf: str, *, depth: str, lane: str, nascente_only: bool
    ) -> list[SweepDispatch]:
        """Route the ``default`` lane to sweep_uf (destinos) + discover_atrativo.

        Mirrors the former ``engine_sweep_run`` ladder byte-for-byte:
          - NASCENTE (``nascente_only``): the Mtur destino seed ONLY, regardless of
            lane — atrativos are Google Places (no free source), so they are never
            fanned out on the free path.
          - otherwise: honor ``lane`` — destinos→sweep_uf, atrativos→discover_atrativo.
        ``depth`` is threaded verbatim to each producer's ``depth`` kwarg.
        """
        if nascente_only:
            return [SweepDispatch("brave.sweep_uf", (uf,), {"depth": depth})]
        plan: list[SweepDispatch] = []
        if lane in ("destinos", "both"):
            plan.append(SweepDispatch("brave.sweep_uf", (uf,), {"depth": depth}))
        if lane in ("atrativos", "both"):
            plan.append(SweepDispatch("brave.discover_atrativo", (uf,), {"depth": depth}))
        return plan

    def beat_entries(self, uf_list: list[str]) -> dict[str, dict]:
        """Per-UF daily beat rows: sweep_uf @ 2 AM UTC + discover_atrativo @ 3 AM UTC.

        Two entries per UF, no ``options.queue`` (single-queue model). Byte-identical
        to the former ``build_beat_schedule`` ``"default"`` branch.
        """
        from celery.schedules import crontab  # noqa: PLC0415

        schedule: dict[str, dict] = {}
        for _uf in uf_list:
            _u = _uf.lower()
            schedule[f"sweep-{_u}-daily"] = {
                "task": "brave.sweep_uf",
                "schedule": crontab(hour=2, minute=0),  # 2 AM UTC daily
                "args": (_uf,),
                "kwargs": {},
            }
            schedule[f"sweep-atrativos-{_u}-daily"] = {
                "task": "brave.discover_atrativo",
                "schedule": crontab(hour=3, minute=0),  # 3 AM UTC daily
                "args": (_uf,),
                "kwargs": {},
            }
        return schedule


# Registry descriptor singleton — stateless, cheap to construct (no I/O).
MTUR_DOMAIN = MturDomain()
