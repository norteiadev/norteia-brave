"""PlacesDomain — SourceDomain implementation for the "default" Google Places track.

The retired Mtur destino-seed is gone: parent destinos are now resolved from the DB
reference tables (``brave.shared.destino.ensure_destino``), so this domain fans out
ONLY the Google Places attraction chain (``discover_atrativo`` → find_contacts →
gather_signals). ``discover`` wraps the Places :class:`DiscoveryAgent`; ``score_input``
/ ``enrich`` are pure and dependency-free.

Dormant by default: the ``"default"`` lane ships with ``source.default.enabled=false``
(see ``brave.config.settings._default_sources``), so ``build_beat_schedule`` emits no
``sweep-atrativos-{uf}-daily`` entries until it is re-enabled via config. All wiring is
present and re-enablable — nothing Places runs now.

Import posture (D-18): kernel + clients only, lazily. Nothing here imports another
domain. The heavy client / agent imports live inside ``discover`` so the registry
(and the ``brave.lanes`` re-export shims) can import this module cheaply.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from brave.core.score.schemas import ScoreInput
from brave.domains.base import SweepDispatch, build_score_input

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from brave.clients.base import LLMClientProtocol, PlacesClientProtocol
    from brave.config.settings import ScoreConfig


class PlacesDomain:
    """The ``default`` (Google Places attractions) collection source.

    Satisfies :class:`brave.domains.base.SourceDomain` structurally.
    """

    name = "default"
    produces: tuple[str, ...] = ("attraction",)

    async def discover(
        self,
        uf: str,
        run_rio: bool = True,
        *,
        session: Session | None = None,
        config: ScoreConfig | None = None,
        places_client: PlacesClientProtocol | None = None,
        llm_client: LLMClientProtocol | None = None,
    ) -> None:
        """Run one Google Places attraction sweep for a UF (dormant lane).

        The task layer injects ``session=`` / ``config=`` and the Places + LLM
        clients. Raises ``ValueError`` if the mandatory deps are missing — the
        registry descriptor is default-constructed and cannot discover without
        them. Kept for protocol conformance + re-enablement; the live attraction
        fan-out is task-driven (``brave.discover_atrativo``).
        """
        if session is None or config is None or places_client is None or llm_client is None:
            raise ValueError(
                "PlacesDomain.discover requires session=, config=, places_client= and "
                "llm_client= (injected by the task layer)"
            )
        from brave.lanes.atrativos.discovery_agent import DiscoveryAgent

        await DiscoveryAgent(places_client, llm_client, session, config).produce(uf)

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
        """Map a Nascente payload / Rio normalized dict onto the ScoreInput."""
        return build_score_input(payload)

    def sweep_plan(
        self, uf: str, *, depth: str, lane: str, nascente_only: bool
    ) -> list[SweepDispatch]:
        """Route the ``default`` lane to the Google Places attraction producer.

        The Mtur destino seed is retired — there is NO free NASCENTE producer for
        this lane (Places always costs), so ``nascente_only`` and the ``destinos``
        lane both fan out nothing:
          - NASCENTE (``nascente_only``): ``[]`` — no free source.
          - ``destinos``: ``[]`` — destinos come from the DB reference tables now.
          - ``atrativos`` / ``both``: ``brave.discover_atrativo`` (Google Places).
        ``depth`` is threaded verbatim to the producer's ``depth`` kwarg.
        """
        if nascente_only:
            return []
        if lane in ("atrativos", "both"):
            return [SweepDispatch("brave.discover_atrativo", (uf,), {"depth": depth})]
        return []

    def beat_entries(self, uf_list: list[str]) -> dict[str, dict]:
        """Per-UF daily beat rows: discover_atrativo @ 3 AM UTC only.

        One entry per UF, no ``options.queue`` (single-queue model). The retired
        Mtur ``sweep_uf`` entry is gone. Gated by ``enabled_sources`` in
        ``build_beat_schedule`` — emitted only when the ``default`` lane is enabled.
        """
        from celery.schedules import crontab  # noqa: PLC0415

        schedule: dict[str, dict] = {}
        for _uf in uf_list:
            _u = _uf.lower()
            schedule[f"sweep-atrativos-{_u}-daily"] = {
                "task": "brave.discover_atrativo",
                "schedule": crontab(hour=3, minute=0),  # 3 AM UTC daily
                "args": (_uf,),
                "kwargs": {},
            }
        return schedule


# Registry descriptor singleton — stateless, cheap to construct (no I/O).
PLACES_DOMAIN = PlacesDomain()
