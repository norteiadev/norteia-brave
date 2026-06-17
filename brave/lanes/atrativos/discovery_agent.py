"""DiscoveryAgent — sweeps Google Places by UF, resolves parent destino, writes to Nascente.

Pipeline per municipality-level sweep:
  1. text_search Places for attractions in UF
  2. Resolve parent destino from Mar (hard precondition D-03):
       - Query MarRecord for entity_type="destination", uf=UF, canonical.municipio_ibge matches
       - If no MarRecord found → quarantine_poison(error="parent_destino_absent") + continue
  3. LLM extraction via instructor Mode.Tools (D-09): Places result → AtrativoResult
  4. store_raw with source="places_discovery", source_ref="places:{uf}:{place_id}" (D-04)
     - Only place_id persisted as cache key (COMP-03): stored in payload["canonical"]["place_id"]
       and payload["place_id_cache"]
  5. Write audit row for ingest (D-02)

D-03: parent_destino_absent → quarantine + continue (never raises)
D-04 / COMP-03: only place_id stored from Google; canonical data = AtrativoResult (first-party)
D-18 boundary: no imports from brave.lanes.destinos or brave.tasks
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from brave.config.settings import ScoreConfig
from brave.core.models import MarRecord
from brave.core.nascente.service import store_raw
from brave.core.quarantine import quarantine_poison
from brave.core.rio.routing import process_nascente_record
from brave.lanes.atrativos.schemas import AtrativoResult
from brave.lanes.atrativos.state_machine import advance_sub_state
from brave.observability.audit import write_audit

if TYPE_CHECKING:
    from brave.clients.base import LLMClientProtocol, PlacesClientProtocol

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Prompt constant for LLM extraction
# ---------------------------------------------------------------------------

DISCOVERY_PROMPT = """Você é um especialista em turismo brasileiro.

Dado o seguinte resultado do Google Places para o estado {uf}, extraia as informações
estruturadas do atrativo turístico conforme o schema solicitado.

Dados do Google Places:
  Nome: {place_name}
  Endereço: {formatted_address}
  Place ID: {place_id}
  Município: {municipio_nome}
  Código IBGE: {municipio_ibge}
  UF: {uf}

Instruções:
- Classifique o tipo do atrativo (praia, parque, museu, cachoeira, trilha, mirante,
  centro_historico, experiencia_gastronomica, show_cultural, esporte_aventura, outros).
- O posicionamento deve descrever o diferencial do atrativo (mínimo 10 caracteres).
- Retorne o place_id exatamente como fornecido: {place_id}
- Retorne o municipio_ibge exatamente como fornecido: {municipio_ibge}
- Retorne a UF exatamente como fornecida: {uf}
"""


# ---------------------------------------------------------------------------
# Completude helper
# ---------------------------------------------------------------------------


def _compute_completude(result: AtrativoResult) -> float:
    """Compute completude_value from AtrativoResult field coverage.

    Fields contributing: nome, tipo, posicionamento, municipio_ibge, place_id.
    Returns 75.0 if all five present (LLM-generated ceiling before contact/signal agents).
    Returns 50.0 if nome + tipo + posicionamento only.
    Returns 25.0 otherwise.
    """
    has_nome = bool(result.nome and len(result.nome) >= 2)
    has_tipo = bool(result.tipo)
    has_posicionamento = bool(result.posicionamento and len(result.posicionamento) >= 10)
    has_ibge = bool(result.municipio_ibge)
    has_place_id = bool(result.place_id)

    if has_nome and has_tipo and has_posicionamento and has_ibge and has_place_id:
        return 75.0
    elif has_nome and has_tipo and has_posicionamento:
        return 50.0
    else:
        return 25.0


# ---------------------------------------------------------------------------
# Parent destino resolution
# ---------------------------------------------------------------------------


def _resolve_parent_destino(
    session: Session,
    uf: str,
    municipio_ibge: str,
) -> MarRecord | None:
    """Query Mar for the parent destino matching uf + municipio_ibge.

    Implements D-03: parent destino resolution from Mar is a hard precondition.
    Queries MarRecord where:
      - entity_type = "destination"
      - source_ref contains the municipio_ibge (mtur:{uf}:{ibge} format)
      - superseded_by_id IS NULL (active record only)

    Uses source_ref pattern matching because the canonical column uses SQLAlchemy
    JSON type (not JSONB), making JSON path expressions dialect-specific.
    The mtur source_ref format is "mtur:{UF}:{ibge_code}" or "desm:{uf}:{ibge}:{slug}".

    Args:
        session:       SQLAlchemy Session.
        uf:            Two-letter UF code.
        municipio_ibge: 7-digit IBGE code of the municipality.

    Returns:
        Active MarRecord for the parent destino, or None if not in Mar.
    """
    from sqlalchemy import and_

    # Primary lookup: source_ref contains ibge code (mtur:{UF}:{ibge} or desm:{uf}:{ibge}:*)
    result = session.scalar(
        select(MarRecord).where(
            and_(
                MarRecord.entity_type == "destination",
                MarRecord.superseded_by_id.is_(None),
                MarRecord.source_ref.contains(municipio_ibge),
            )
        )
    )

    if result is not None:
        return result

    # Fallback: any active destination record for the same UF
    # This handles cases where source_ref format doesn't include ibge directly
    return session.scalar(
        select(MarRecord).where(
            and_(
                MarRecord.entity_type == "destination",
                MarRecord.superseded_by_id.is_(None),
                MarRecord.source_ref.startswith(f"mtur:{uf}:"),
            )
        )
    )


# ---------------------------------------------------------------------------
# DiscoveryAgent
# ---------------------------------------------------------------------------


class DiscoveryAgent:
    """DiscoveryAgent — sweeps Google Places for attractions in a UF.

    For each municipality in the UF:
      1. Calls places_client.text_search to find attractions.
      2. Resolves parent destino from Mar (hard precondition D-03).
         If no parent → quarantine_poison(error="parent_destino_absent") + continue.
      3. Extracts via llm_client.extract(schema=AtrativoResult, mode="tools").
      4. Calls store_raw with source="places_discovery", entity_type="attraction".
         Payload canonical includes only AtrativoResult fields + place_id cache (D-04).

    D-18 boundary: no imports from brave.lanes.destinos.
    COMP-03 / D-04: only place_id from Google persisted; all canonical data from LLM extraction.

    Args:
        places_client: PlacesClientProtocol implementation (real or fake).
        llm_client:    LLMClientProtocol implementation (real or fake).
        session:       SQLAlchemy synchronous Session.
        config:        ScoreConfig with §7.6 weights.
    """

    def __init__(
        self,
        places_client: "PlacesClientProtocol",
        llm_client: "LLMClientProtocol",
        session: Session,
        config: ScoreConfig,
    ) -> None:
        self._places_client = places_client
        self._llm_client = llm_client
        self._session = session
        self._config = config

    async def produce(self, uf: str) -> None:
        """Sweep Google Places for attractions in a UF and write to Nascente.

        Each Places result goes through: parent-destino check → LLM extraction →
        store_raw. Failures are quarantined (never propagated).

        Args:
            uf: Two-letter Brazilian state code (e.g. "BA", "RJ", "SP").
        """
        # Build a broad sweep query for the UF
        # The query covers all types of attractions in the UF
        search_queries = [f"atrativos em {uf}", f"pontos turísticos em {uf}"]

        for query in search_queries:
            try:
                places_results = await self._places_client.text_search(query=query, uf=uf)
            except Exception as exc:
                quarantine_poison(
                    session=self._session,
                    nascente_id=None,
                    task_name="brave.discover_atrativo",
                    error=f"places_search_failed: {exc}",
                    payload={"uf": uf, "query": query},
                )
                continue

            for place in places_results:
                place_id: str = place.get("place_id", "")
                if not place_id:
                    continue

                place_name: str = place.get("name", "")
                formatted_address: str = place.get("formatted_address", "")
                municipio_ibge: str = place.get("municipio_ibge", "")
                municipio_nome: str = place.get("municipio_nome", "")

                # D-03: Resolve parent destino from Mar (hard precondition)
                parent_mar = _resolve_parent_destino(
                    session=self._session,
                    uf=uf,
                    municipio_ibge=municipio_ibge,
                )
                if parent_mar is None:
                    # No parent destino in Mar → quarantine and skip (D-03)
                    logger.warning(
                        "parent_destino_absent",
                        place_id=place_id,
                        municipio_ibge=municipio_ibge,
                        uf=uf,
                    )
                    quarantine_poison(
                        session=self._session,
                        nascente_id=None,
                        task_name="brave.discover_atrativo",
                        error="parent_destino_absent",
                        payload={
                            "place_id": place_id,
                            "municipio_ibge": municipio_ibge,
                            "uf": uf,
                        },
                    )
                    continue

                # Step 3: LLM extraction — validate-or-quarantine (D-11)
                prompt = DISCOVERY_PROMPT.format(
                    place_name=place_name,
                    formatted_address=formatted_address,
                    place_id=place_id,
                    municipio_nome=municipio_nome,
                    municipio_ibge=municipio_ibge,
                    uf=uf,
                )

                try:
                    result: AtrativoResult = await self._llm_client.extract(
                        prompt=prompt,
                        schema=AtrativoResult,
                        mode="tools",  # instructor Mode.TOOLS — D-09
                    )
                except Exception as exc:
                    quarantine_poison(
                        session=self._session,
                        nascente_id=None,
                        task_name="brave.discover_atrativo",
                        error=f"llm_extraction_failed: {exc}",
                        payload={"place_id": place_id, "uf": uf},
                    )
                    continue

                # Step 4: store_raw
                # D-04 / COMP-03: only place_id from Google persisted as cache key;
                # canonical data = AtrativoResult (first-party extracted)
                source_ref = f"places:{uf}:{place_id}"
                completude = _compute_completude(result)

                payload: dict[str, Any] = {
                    # §7.6 criterion values (read by route_by_score from normalized)
                    "origem_value": 60.0,  # Google Places = authoritative but not official gov
                    "completude_value": completude,
                    "corroboracao_value": 0.0,
                    "atualidade_value": 0.0,
                    "validacao_humana_value": 0.0,
                    # place_id cache key — only Google field persisted (D-04)
                    "place_id_cache": place_id,
                    # Canonical dict contains only AtrativoResult data + place_id
                    "canonical": {
                        "place_id": place_id,  # D-04: cache key reference
                        "nome": result.nome,
                        "tipo": result.tipo,
                        "posicionamento": result.posicionamento,
                        "municipio_nome": result.municipio_nome,
                        "municipio_ibge": result.municipio_ibge,
                        "uf": result.uf,
                    },
                    # Linking to parent destino in Mar
                    "parent_mar_id": str(parent_mar.id),
                    "municipio_id": municipio_ibge,
                    "name": result.nome,
                    "entity_type": "attraction",
                    "source_note": "LLM-extracted, pending contact/signal/validation",
                }

                nascente = store_raw(
                    session=self._session,
                    source="places_discovery",
                    source_ref=source_ref,
                    entity_type="attraction",
                    uf=uf,
                    payload=payload,
                )

                # Write audit row for discovery ingest (D-02)
                write_audit(
                    session=self._session,
                    action="atrativo_discovered",
                    entity_type="attraction",
                    record_id=nascente.id,
                    before_state=None,
                    after_state={"source_ref": source_ref, "place_id": place_id},
                    actor="discovery_agent",
                )

                # Step 5: Initialize the FSM substrate (finding #1, ORCH-02/D-03).
                # Create the Rio record from this Nascente and seed sub_state="discovered"
                # so the auto-chain's `sub_state='discovered'` query has an anchor and the
                # contact_finder precondition can ever be met. Mirrors how the destinos
                # producers (mtur.py) call process_nascente_record inline (D-18: a lane
                # imports core + the lane's own state_machine, never brave.tasks).
                #
                # Idempotency (D-04): process_nascente_record is idempotent by
                # canonical_key (returns the existing Rio on a replayed produce), and
                # advance_sub_state(expected_state=None) returns False — a no-op — for a
                # record already advanced past the NULL anchor. So a replayed sweep neither
                # duplicates the Rio nor resets a record already in flight.
                rio = process_nascente_record(self._session, nascente, self._config)
                advance_sub_state(
                    session=self._session,
                    rio=rio,
                    expected_state=None,
                    next_state="discovered",
                    actor="discovery_agent",
                )

                logger.info(
                    "atrativo_ingested",
                    source_ref=source_ref,
                    nome=result.nome,
                    uf=uf,
                    rio_id=str(rio.id),
                    sub_state=rio.sub_state,
                )
