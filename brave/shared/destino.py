"""Shared parent-destino ensure helper.

Both the TripAdvisor attractions lane and the Places attractions lane need to
materialize a parent "destino" record on demand from an IBGE município so the
attraction can be linked (destino-first). This module is the single home for
that logic (shared, not domain-owned, avoiding a domain→domain edge — D-18).

``ensure_destino`` synthesizes an authoritative IBGE destino
(source="ibge", source_ref="ibge:{uf}:{ibge}", origem=100) into Nascente and
promotes it to Rio, idempotently, then reports whether that destino has already
reached Mar so callers can carry a ``parent_mar_id`` when present.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from brave.core.models import MarRecord
from brave.core.nascente.service import store_raw
from brave.core.rio.routing import process_nascente_record

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from brave.config.settings import ScoreConfig


def ensure_destino(
    session: "Session",
    config: "ScoreConfig",
    *,
    ibge_code: str,
    nome: str,
    uf: str,
) -> tuple[uuid.UUID, str, uuid.UUID | None]:
    """Create (or fetch) the parent destino for an IBGE município on demand.

    Synthesizes an authoritative IBGE destino (source="ibge", origem=100) so an
    attraction can be linked to it (destino-first) rather than dropped when the
    parent is missing.

    Idempotent + safe to call repeatedly for the same município: ``store_raw``
    dedups by (source, source_ref, content_hash) and ``process_nascente_record``
    is idempotent.

    Args:
        session:   SQLAlchemy synchronous Session.
        config:    ScoreConfig with reliability weights and thresholds.
        ibge_code: The IBGE municipality code (7 digits).
        nome:      The município display name.
        uf:        Two-letter Brazilian state code.

    Returns:
        (parent_rio_id, parent_source_ref, parent_mar_id_or_None):
            - parent_rio_id: id of the created (or existing) destino RioRecord.
            - parent_source_ref: "ibge:{uf}:{ibge_code}".
            - parent_mar_id: id of the ACTIVE MarRecord for this destino
              (matching source_ref, superseded_by_id IS NULL) if it already
              reached Mar, else None.
    """
    source_ref = f"ibge:{uf}:{ibge_code}"
    payload: dict[str, Any] = {
        "name": nome,
        "municipio_id": ibge_code,
        "uf": uf,
        "origem_value": 100.0,
        "completude_value": 40.0,
        "corroboracao_value": 0.0,
        "atualidade_value": 0.0,
        "validacao_humana_value": 0.0,
        "canonical": {
            "name": nome,
            "uf": uf,
            "municipio": nome,
            "ibge_code": ibge_code,
            # Reserved distrito/subdistrito keys — uniform wire shape across lanes.
            # TA/IBGE cards carry no sub-município text → stay None (see Places lane).
            "distrito_name": None,
            "distrito_code": None,
            "distrito_municipio_ibge": None,
            "subdistrito_name": None,
            "subdistrito_code": None,
        },
    }
    nascente = store_raw(
        session=session,
        source="ibge",
        source_ref=source_ref,
        entity_type="destination",
        uf=uf,
        payload=payload,
    )
    # The parent linkage contract needs a rio_id, and there is none without Rio —
    # so the synthesized destino is ALWAYS promoted to Rio, even when the caller's
    # sweep runs Nascente-only. This is intentional: a parent destino must exist
    # for the attraction to reference. Idempotent per município.
    rio = process_nascente_record(
        session=session,
        nascente=nascente,
        config=config,
    )

    # Did this destino already reach Mar? Only an ACTIVE row (superseded_by_id
    # IS NULL) counts — supersession leaves stale rows behind. None otherwise.
    parent_mar_id: uuid.UUID | None = (
        session.query(MarRecord.id)
        .filter(
            MarRecord.source_ref == source_ref,
            MarRecord.superseded_by_id.is_(None),
        )
        .scalar()
    )

    return (rio.id, source_ref, parent_mar_id)
