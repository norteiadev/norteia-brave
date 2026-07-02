"""Wire-shape DTOs shared across layers.

These pydantic v2 models freeze the exact shapes that cross a network boundary,
so the shape lives in one typed place instead of being reconstructed as ad-hoc
dicts at each call site.

:class:`MarPushPayload` reproduces the Mar push dict that norteia-brave POSTs to
norteia-api (the D-16 Pact contract). Field declaration order is load-bearing:
``model_dump()`` preserves it, so the emitted dict is byte-identical to the
hand-built dict it replaces. ``canonical`` is intentionally a free-form
``dict[str, Any]`` (NOT a nested submodel) because its keys vary by entity
(destinations carry ``ibge_code``; attractions do not) and its insertion order
must be preserved verbatim.
"""

from typing import Any

from pydantic import BaseModel


class FlatProvenance(BaseModel):
    """Flat per-criterion §7.6 provenance (the D-16 Pact contract shape).

    All five criteria default to 0.0 and are stored as floats, matching the
    ``float(score_breakdown.get(..., 0.0))`` coercion of the original payload
    builder.
    """

    origem: float = 0.0
    completude: float = 0.0
    corroboracao: float = 0.0
    atualidade: float = 0.0
    validacao_humana: float = 0.0


class MarPushPayload(BaseModel):
    """The Mar push payload POSTed to norteia-api (D-16).

    ``model_dump()`` returns a dict whose keys, order, and value types are
    byte-identical to the dict produced by the original ``_build_push_payload``.
    """

    source: str
    source_ref: str
    entity_type: str
    canonical: dict[str, Any]
    reliability_score: float
    score_version: str
    provenance: FlatProvenance
