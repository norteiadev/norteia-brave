"""LLM WhatsApp-number discovery for the Atrativos lane (Phase F).

When an atrativo reaches the WhatsApp gate WITHOUT a celular candidate captured at
enrichment time (ContactFinderAgent found no mobile number), the manual DLQ→WhatsApp
batch move dispatches ``discover_whatsapp_number_task`` (brave/tasks/pipeline.py),
which calls :func:`discover_number` here to ask the LLM for a plausible WhatsApp
number for the establishment.

Offline posture (run_real_externals=False, the default):
  The task injects NullLLMClient, whose ``extract`` returns None → :func:`discover_number`
  returns None → no number → the task routes the record back to DLQ with
  dlq_reason="no_contact_found". Fully deterministic, no network, CI-keyless.

Real posture (run_real_externals=True, opt-in):
  The task injects RealLLMClient; the model returns a WhatsAppNumberDiscovery with a
  raw phone string, which the task normalizes to E.164 + masks into a celular
  candidate before proceeding to outreach.

D-18 boundary: this module imports ONLY its own lane schema (and the client protocol
under TYPE_CHECKING). It never imports brave.core, brave.tasks, or another lane.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from brave.lanes.atrativos.schemas import WhatsAppNumberDiscovery

if TYPE_CHECKING:
    from brave.clients.base import LLMClientProtocol

logger = structlog.get_logger(__name__)


async def discover_number(
    llm_client: LLMClientProtocol,
    *,
    name: str,
    uf: str | None = None,
    address: str | None = None,
) -> str | None:
    """Ask the LLM for a plausible WhatsApp number for an atrativo, or None.

    Uses ``llm_client.extract`` with the WhatsAppNumberDiscovery schema and
    instructor Mode.Tools (mandatory for DeepSeek schema adherence, D-09). The
    2nd-layer Pydantic validation is provided by the schema itself.

    Offline (NullLLMClient) → extract returns None → this returns None (no number).
    Any extraction error is swallowed and treated as "no number found" so the caller
    routes the record back to DLQ rather than crashing the task.

    Args:
        llm_client: LLMClientProtocol implementation (Null offline, Real opt-in).
        name:       Atrativo display name (the primary lookup key).
        uf:         Two-letter UF code, added to the prompt for disambiguation.
        address:    Optional address string for disambiguation.

    Returns:
        A RAW candidate phone string (E.164 / 55-prefixed / bare national), or None
        when no plausible number was found. Celular-validation + masking are the
        caller's responsibility (whatsapp_candidate_from_phone).
    """
    location = ", ".join(part for part in (address, uf, "Brasil") if part)
    prompt = (
        "Você está ajudando a Norteia a validar atrativos turísticos no Brasil. "
        "Encontre, se existir publicamente, o número de WhatsApp/telefone de contato "
        f"do responsável pelo seguinte estabelecimento:\n\n"
        f"Nome: {name}\n"
        f"Localização: {location or 'Brasil'}\n\n"
        "Retorne o número em formato E.164 (+55DDDNNNNNNNNN) quando encontrar. "
        "Se não houver um número plausível, retorne phone=null."
    )

    try:
        result = await llm_client.extract(
            prompt=prompt,
            schema=WhatsAppNumberDiscovery,
            mode="tools",
        )
    except Exception as exc:  # noqa: BLE001 — treat any LLM error as "not found"
        logger.warning("whatsapp_number_discovery_failed", name=name, error=str(exc))
        return None

    if isinstance(result, WhatsAppNumberDiscovery):
        return result.phone
    if isinstance(result, dict):
        phone = result.get("phone")
        return phone if isinstance(phone, str) else None
    # None (offline Null client) or any unexpected shape → no number found.
    return None
