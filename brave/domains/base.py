"""SourceDomain protocol — the entity-agnostic Brave collection-source contract (Phase G).

A *source domain* is one collection lane packaged as a self-contained unit under
``brave/domains/<fonte>/``. Adding a new source = a new package here + one line in
the registry (``brave/domains/__init__.py``). Concrete domains today: ``mtur``
(the "default" Mtur-seed + Google Places track), ``tripadvisor``, and ``manual``.

Import posture (generalized D-18, docs/ultraplan-refactor-brave.md §Phase G):
  - kernel = ``brave.core`` + ``brave.shared``; the kernel NEVER imports
    ``brave.domains`` or ``brave.tasks``.
  - a domain imports the kernel + ``brave.clients`` ONLY; domains NEVER import
    each other. ``brave/domains/base.py`` (this file) and the registry
    (``brave/domains/__init__.py``) are the two exceptions the registry needs.

The protocol is intentionally small: ``name``/``produces`` describe the domain,
``discover`` is the per-UF produce entry (wraps the lane's ``produce`` — the deps
it needs are injected as keyword-only args by the task layer), ``enrich`` returns
a domain-shaped enrichment view of a Rio record, and ``score_input`` maps a raw
payload/normalized dict onto the shared :class:`ScoreInput`.

Orchestration is registry-driven (Phase G STEP 3): the task layer never names a
source. Instead it resolves ``get_domain(source)`` and asks the domain how to run:
  - ``sweep_plan`` returns the ordered producer dispatches for one UF+depth+lane as
    :class:`SweepDispatch` descriptors (celery task name + args/kwargs). The task
    layer resolves each ``task_name`` to the registered producer and ``.delay()``s
    it on the single ``celery`` queue — so a domain owns its lane→producer routing
    WITHOUT importing ``brave.tasks`` (kept pure for the import posture / D-18).
  - ``beat_entries`` returns this domain's celery-beat rows (name → entry dict), so
    ``build_beat_schedule`` just unions the entries of the enabled domains.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from brave.core.score.schemas import ScoreInput


@dataclass(frozen=True)
class SweepDispatch:
    """One producer-task dispatch: a celery task name plus its ``.delay`` args.

    Returned by :meth:`SourceDomain.sweep_plan`. The task layer maps ``task_name``
    (a stable ``brave.*`` celery task name, e.g. ``"brave.sweep_uf"``) to the
    producer task object in ``brave.tasks.pipeline`` and calls
    ``producer.delay(*args, **kwargs)`` on the default ``celery`` queue. Keeping the
    dispatch as inert data (not a bound task) is what lets a domain describe its
    fan-out without importing ``brave.tasks`` — preserving the kernel/domain import
    posture (D-18) and the single-source-per-run + single-queue invariants.
    """

    task_name: str
    args: tuple[Any, ...] = ()
    kwargs: Mapping[str, Any] = field(default_factory=dict)


def build_score_input(payload: Mapping[str, Any]) -> ScoreInput:
    """Build a :class:`ScoreInput` from a payload / normalized dict.

    Mirrors the construction in ``brave.core.rio.routing.route_by_score`` (safe
    ``float(...)`` coercion, 0.0 default per criterion) so a domain's
    ``score_input`` is byte-identical to what the Rio normalizer would compute.

    Args:
        payload: A Nascente payload or Rio ``normalized`` dict carrying the five
            ``*_value`` reliability criterion keys (missing keys default to 0.0).

    Returns:
        A validated :class:`ScoreInput` (each field clamped to 0–100 by Pydantic).
    """
    return ScoreInput(
        origem_value=float(payload.get("origem_value", 0.0)),
        completude_value=float(payload.get("completude_value", 0.0)),
        corroboracao_value=float(payload.get("corroboracao_value", 0.0)),
        atualidade_value=float(payload.get("atualidade_value", 0.0)),
        validacao_humana_value=float(payload.get("validacao_humana_value", 0.0)),
    )


@runtime_checkable
class SourceDomain(Protocol):
    """Entity-agnostic contract every Brave collection source implements.

    Structural (``Protocol``) — concrete domains satisfy it by shape, no explicit
    subclassing. ``@runtime_checkable`` so the registry / tests can assert
    ``isinstance(domain, SourceDomain)``.

    Attributes:
        name: Canonical source identifier (e.g. ``"mtur"``, ``"tripadvisor"``,
            ``"manual"``). Also the primary registry key.
        produces: The entity types this domain can populate, e.g.
            ``("destination", "attraction")``.
    """

    name: str
    produces: tuple[str, ...]

    async def discover(self, uf: str, run_rio: bool = True) -> None:
        """Run one per-UF produce for this domain.

        Wraps the lane ``produce`` currently inlined in the sweep tasks. Concrete
        implementations accept the resources they need (``session=``, ``config=``,
        clients) as keyword-only arguments injected by the task layer; the
        positional ``(uf, run_rio)`` interface is what callers depend on.

        Args:
            uf: Two-letter Brazilian state code (e.g. ``"BA"``).
            run_rio: Depth gate — when ``False``, ingest to Nascente only
                (no Rio/Places/LLM). Defaults to ``True``.
        """
        ...

    def enrich(self, rio: Any) -> dict[str, Any]:
        """Return a domain-shaped enrichment view of a Rio record.

        Args:
            rio: A ``RioRecord`` (or any object exposing ``normalized``).

        Returns:
            A JSON-serializable dict of the enrichment signals this domain owns.
        """
        ...

    def score_input(self, payload: Mapping[str, Any]) -> ScoreInput:
        """Map a raw payload / normalized dict onto the shared ScoreInput."""
        ...

    def sweep_plan(
        self,
        uf: str,
        *,
        depth: str,
        lane: str,
        nascente_only: bool,
        max_per_uf: int | None = None,
    ) -> list[SweepDispatch]:
        """Return the ordered producer dispatches for one UF of this source.

        Owns this source's lane→producer routing and depth gating (the logic the
        engine orchestrator used to hardcode in an ``if source == ...`` ladder). The
        caller threads in the already-resolved ``depth`` (read once at /start) plus
        the derived ``nascente_only`` flag and the requested ``lane`` — the domain
        returns which producer task(s) to fan out. A non-sweep source (``manual``)
        returns ``[]``.

        Args:
            uf: Two-letter Brazilian state code (e.g. ``"BA"``).
            depth: The pipeline depth threaded verbatim to each producer's ``depth`` kwarg.
            lane: ``"destinos" | "atrativos" | "both"`` — the requested entity families.
            nascente_only: ``True`` when ``depth`` is the free NASCENTE reach (no
                Places/LLM) — sources collapse to their free producer only.
            max_per_uf: Optional operator-set cap on attractions ingested per UF
                (test-run throttle). ``None`` = no cap. A domain that supports it
                threads it into the producer's kwargs; others ignore it.
        """
        ...

    def beat_entries(self, uf_list: list[str]) -> dict[str, dict]:
        """Return this domain's celery-beat entries (entry name → entry dict).

        ``build_beat_schedule`` unions the entries of every ENABLED domain. A source
        with no scheduled task (``manual``) returns ``{}``. Entries must NOT pin
        ``options.queue`` (single-queue model — guarded by test_celery_queue_routing).

        Args:
            uf_list: The 27 UF codes, for per-UF fan-out entries.
        """
        ...
