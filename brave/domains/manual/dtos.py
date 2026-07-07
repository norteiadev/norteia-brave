"""DTOs for the ``manual`` source domain (Phase G)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ManualRecordInput(BaseModel):
    """Validated input for an operator-authored territorial record.

    ``origem`` and ``validação humana`` are fixed at 100 by the domain (not
    accepted here) — a manual record is human-authoritative by construction. The
    remaining §7.6 criteria default to sensible manual values but may be overridden.
    """

    entity_type: Literal["destination", "attraction"] = Field(
        ..., description="Kind of territorial record being authored."
    )
    uf: str = Field(..., min_length=2, max_length=2, description="Two-letter UF code (e.g. 'BA').")
    name: str = Field(..., min_length=2, description="Display name of the destino/atrativo.")
    municipio_id: str | None = Field(
        default=None, description="7-digit IBGE municipality code, when known."
    )
    canonical: dict[str, Any] | None = Field(
        default=None, description="Extra canonical fields to merge (name/uf are filled in)."
    )
    completude_value: float = Field(
        default=100.0, ge=0.0, le=100.0, description="§7.6 completude (default 100 for manual)."
    )
    corroboracao_value: float = Field(
        default=0.0, ge=0.0, le=100.0, description="§7.6 corroboração (default 0)."
    )
    atualidade_value: float = Field(
        default=100.0, ge=0.0, le=100.0, description="§7.6 atualidade (default 100 for manual)."
    )
