"""Pydantic v2 schemas for the Destinos lane.

DesmembramentoResult — output schema for the DesmembramentoAgent (§7.4 PLANO-BRAVE.md, D-03).
DestinoItem         — a single tourist destination extracted from a municipality.

These schemas enforce the D-18 interface contract between the DesmembramentoAgent
(brave/lanes/destinos/desmembramento.py) and the Nascente store. They are also
the instructor-validated 2nd-layer output schema (D-09, D-03).

References:
  - PLANO-BRAVE.md §7.4 — Desmembramento agent design and DesmembramentoResult schema
  - 02-CONTEXT.md D-03 — fan-out one LLM call per Oferta Principal município
  - brave/clients/base.py MturClientProtocol — upstream municipality data
"""

from typing import Literal

from pydantic import BaseModel, Field


class DestinoItem(BaseModel):
    """A single tourist destination (sub-municipality) extracted by DesmembramentoAgent.

    Represents a real named place (distrito, praia, vila, etc.) that exists inside
    a Mtur Oferta Principal municipality but has its own tourist identity.
    """

    nome: str = Field(
        ...,
        min_length=2,
        description="Nome turístico do destino (e.g. 'Trancoso', 'Praia do Forte')",
    )
    tipo: Literal[
        "distrito",
        "praia",
        "vila",
        "localidade",
        "ilha",
        "balneario",
        "outros",
    ] = Field(..., description="Tipo geográfico/turístico do destino")
    posicionamento: str = Field(
        ...,
        min_length=5,
        description="Breve posicionamento turístico (e.g. 'Vila histórica com ruas de pedra')",
    )


class DesmembramentoResult(BaseModel):
    """Result of one DesmembramentoAgent call for a single Oferta Principal municipality.

    Contains the IBGE code and nome of the parent municipality plus the list of
    real tourist destinations extracted from it.

    origem_value=40 is applied by the producer when writing each DestinoItem to Nascente.
    The D-06 firewall guarantees origin=40 records cannot auto-promote to Mar without
    human validation (validacao_humana must reach 100 via steward action).
    """

    municipio_ibge: str = Field(
        ...,
        pattern=r"^\d{7}$",
        description="IBGE 7-digit municipality code (e.g. '2927408' for Porto Seguro)",
    )
    municipio_nome: str = Field(
        ...,
        description="Nome do município (e.g. 'Porto Seguro')",
    )
    destinos: list[DestinoItem] = Field(
        default_factory=list,
        description="Lista de destinos turísticos desmembrados do município",
    )
