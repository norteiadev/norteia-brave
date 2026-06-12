"""DesmembramentoAgent — DEST-03.

Fans out one DeepSeek LLM call per Oferta Principal município to discover
sub-destinos (distritos/praias/vilas/localidades). Mandatory validate-or-quarantine
(D-11): malformed LLM output → PoisonQuarantine, never §7.6 DLQ.

D-18 boundary:
  - Imports from brave.core (nascente service, quarantine) and brave.clients (protocols).
  - Does NOT import from brave.tasks or other brave.lanes modules.
  - quarantine_poison is imported from brave.core.quarantine (not brave.tasks.pipeline).

D-06 firewall:
  - All records written here carry origem_value=40.0, meaning they can never auto-promote
    to Mar without steward validation (validacao_humana_value must reach 100).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session

from brave.config.settings import ScoreConfig
from brave.core.nascente.service import store_raw
from brave.core.quarantine import quarantine_poison
from brave.lanes.destinos.schemas import DesmembramentoResult, DestinoItem

if TYPE_CHECKING:
    from brave.clients.base import LLMClientProtocol, MturClientProtocol


# ---------------------------------------------------------------------------
# Prompt constant
# ---------------------------------------------------------------------------

DESMEMBRAMENTO_PROMPT = """Você é um especialista em turismo brasileiro.

Dado o município de {municipio_nome} (UF: {uf}, IBGE: {ibge_code}), identifique os
principais destinos turísticos que existem DENTRO deste município mas possuem
identidade turística própria — por exemplo: distritos, praias, vilas históricas,
localidades, ilhas e balneários reconhecidos.

Instruções:
- Liste apenas localidades reais, reconhecidas turisticamente no Brasil.
- Não inclua o próprio município como um item.
- Para cada destino, forneça: nome turístico, tipo geográfico e um breve posicionamento.
- Tipos válidos: distrito, praia, vila, localidade, ilha, balneario, outros.
- O posicionamento deve ter pelo menos 5 caracteres descrevendo o apelo turístico.
- Retorne o código IBGE do município pai: {ibge_code}
- Retorne o nome do município pai: {municipio_nome}

Município: {municipio_nome}
UF: {uf}
Código IBGE: {ibge_code}
"""

# ---------------------------------------------------------------------------
# Completude helper
# ---------------------------------------------------------------------------


def _completude_desmembramento(destino: DestinoItem) -> float:
    """Compute completude_value from the DestinoItem field coverage.

    Three fields contribute: nome, tipo, posicionamento.
    Returns 75.0 if all three are present and non-empty (highest achievable
    for LLM-generated records without human corroboration).
    Returns 50.0 if nome + tipo only.
    Returns 25.0 otherwise (nome only or any single non-empty field).

    Args:
        destino: A DestinoItem with nome, tipo, and posicionamento.

    Returns:
        Float completude score (25.0 | 50.0 | 75.0).
    """
    has_nome = bool(destino.nome and len(destino.nome) >= 2)
    has_tipo = bool(destino.tipo)
    has_posicionamento = bool(destino.posicionamento and len(destino.posicionamento) >= 5)

    if has_nome and has_tipo and has_posicionamento:
        return 75.0
    elif has_nome and has_tipo:
        return 50.0
    else:
        return 25.0


# ---------------------------------------------------------------------------
# DesmembramentoAgent
# ---------------------------------------------------------------------------


class DesmembramentoAgent:
    """DesmembramentoAgent — fans out one LLM call per Oferta Principal município.

    For each municipality categorised as "Oferta Principal" in the Mtur dataset,
    calls LLMClientProtocol.extract with the DesmembramentoResult schema to
    discover sub-destinos (distritos, praias, vilas, etc.).

    Validate-or-quarantine (D-11):
      - LLM response successfully validated by instructor → write each DestinoItem
        to Nascente with source="desm" and origem_value=40.0.
      - Any exception (ValidationError, instructor retry exhausted, network error)
        → quarantine_poison(session, nascente_id=None, task_name="brave.desmembramento",
        ...) and continue to the next municipality. The exception is NOT propagated.

    D-06 firewall:
      - origem=40 means max score without human validation is ≤67. Records from
        this agent always land in DLQ until a steward validates them.

    D-18 boundary:
      - quarantine_poison imported from brave.core.quarantine (not brave.tasks.pipeline).
      - No imports from other brave.lanes modules.

    Note on LLM cost guard:
      The real LLMClient implementation (Phase 3) checks the USD cost guard before
      calling OpenRouter. This agent does not add cost-guard logic — it belongs in
      the LLMClient implementation, not in the lane.

    Args:
        llm_client:  LLMClientProtocol implementation (real or fake).
        mtur_client: MturClientProtocol implementation (real or fake).
        session:     SQLAlchemy synchronous Session.
        config:      ScoreConfig with §7.6 weights and thresholds.
    """

    def __init__(
        self,
        llm_client: "LLMClientProtocol",
        mtur_client: "MturClientProtocol",
        session: Session,
        config: ScoreConfig,
    ) -> None:
        self._llm_client = llm_client
        self._mtur_client = mtur_client
        self._session = session
        self._config = config

    async def produce(self, uf: str) -> None:
        """Ingest sub-destinos for all Oferta Principal municipalities in a UF.

        For each Oferta Principal municipality:
          1. Builds a DESMEMBRAMENTO_PROMPT with the municipality's name, UF, and IBGE code.
          2. Calls llm_client.extract(prompt, DesmembramentoResult, mode="tools").
          3. On success: writes each valid DestinoItem to Nascente via store_raw.
          4. On any exception: calls quarantine_poison and continues (no propagation).

        Non-Oferta-Principal municipalities are silently skipped — no LLM call made.

        Args:
            uf: Two-letter Brazilian state code (e.g. "BA", "RJ", "SP").
        """
        municipalities = await self._mtur_client.fetch_municipalities(uf)

        for mun in municipalities:
            if mun.get("categoria") != "Oferta Principal":
                continue

            ibge_code: str = mun.get("ibge_code", "")
            municipio_nome: str = mun.get("name", "")

            prompt = DESMEMBRAMENTO_PROMPT.format(
                municipio_nome=municipio_nome,
                uf=uf,
                ibge_code=ibge_code,
            )

            try:
                result: DesmembramentoResult = await self._llm_client.extract(
                    prompt=prompt,
                    schema=DesmembramentoResult,
                    mode="tools",  # instructor Mode.TOOLS — D-09 carried forward
                )
            except Exception as exc:
                # Quarantine the failure — NOT the §7.6 DLQ (D-11 validate-or-quarantine)
                quarantine_poison(
                    session=self._session,
                    nascente_id=None,
                    task_name="brave.desmembramento",
                    error=str(exc),
                    payload={
                        "municipio_ibge": ibge_code,
                        "municipio_nome": municipio_nome,
                    },
                )
                continue  # Skip this município, continue fan-out

            # Each valid destino → Nascente with origem=40 (D-06 firewall)
            for destino in result.destinos:
                slug = (
                    destino.nome.lower()
                    .replace(" ", "-")
                    .replace("/", "-")
                )
                source_ref = f"desm:{uf}:{ibge_code}:{slug}"

                payload: dict[str, Any] = {
                    "name": destino.nome,
                    "municipio_id": ibge_code,
                    "uf": uf,
                    "tipo": destino.tipo,
                    "posicionamento": destino.posicionamento,
                    # D-11: source_note flags this record as LLM-generated
                    "source_note": "LLM-generated, pending validation",
                    # §7.6 criterion *_value fields — routing.py reads these at normalize step
                    "origem_value": 40.0,
                    "completude_value": _completude_desmembramento(destino),
                    "corroboracao_value": 0.0,
                    # atualidade=0: LLM-inferred geographic fact; not time-indexed
                    "atualidade_value": 0.0,
                    # D-06 firewall: validacao_humana=0 at ingest; steward must validate
                    "validacao_humana_value": 0.0,
                    # Canonical sub-dict for the Pact contract shape (D-10)
                    "canonical": {
                        "name": destino.nome,
                        "uf": uf,
                        "municipio": municipio_nome,
                        "ibge_code": ibge_code,
                    },
                }

                store_raw(
                    session=self._session,
                    source="desm",
                    source_ref=source_ref,
                    entity_type="destination",
                    uf=uf,
                    payload=payload,
                )
