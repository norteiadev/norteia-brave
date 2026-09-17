"""TourismCopywriter — generates a Norteia-voice editorial description for an atrativo.

The TripAdvisor lane has no rich description source (Melhores Destinos, the old lane, was
dropped — it could match only distinctively-named capital attractions). This copywriter is
the replacement: a strong tourism-copywriter system prompt driving a single tool-using
``llm_client.generate()`` call with Anthropic's server-side ``web_search`` tool. Google
Places ``editorialSummary`` + top reviews are passed as grounding context so the model
searches only when it needs more.

Guards (system prompt + a deterministic post-generation pass):
  - PT-BR output, Norteia inclusive voice (famílias/casais/solo — não um único segmento).
  - No em-dash (``—``/``–``) — reads as AI-generated; stripped after generation too.
  - No clichés ("joia escondida", etc.).
  - Prose only: experiential tips (melhor hora, pontos de foto, o que levar) are allowed;
    hard operational data (horário, contato, preço/entrada, acesso) is EXCLUDED — those are
    structured fields sourced deterministically from the Places API, never the model.
  - Accuracy: ground every claim in the Places context or a web-search result; never invent
    amenities or accessibility. Nothing verifiable → shorter sensory prose, no factual claims.

D-18 boundary: no imports from brave.lanes.destinos or brave.tasks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import structlog

from brave.lanes.atrativos.grounding import (
    MIN_GROUNDEDNESS,
    groundedness_ratio,
    menciona,
    menciona_municipio,
)
from brave.shared.exceptions import CostGuardError

if TYPE_CHECKING:
    from brave.clients.base import LLMClientProtocol
    from brave.clients.parallel import ParallelSearch

logger = structlog.get_logger(__name__)

# Basic server-side web_search variant — broadly supported (incl. claude-sonnet-4-5) on the
# pinned anthropic 0.109.x.
#
# max_uses stays 3 — a CAP, not a target. Measured over 5 live descriptions (famous and
# obscure alike): the model spends exactly 2 searches every time, so lowering the cap to 2
# saves nothing on real traffic and removes the only headroom a hard case has. Exceeding the
# cap returns a `max_uses_exceeded` tool-result block, which generate() drops (it reads only
# text blocks) — the record silently degrades to the "sensory-only, no facts" fallback below
# instead of erroring, so a too-tight cap is invisible in production.
#
# user_location biases results to Brazilian sources — static country only, since this dict is
# a module constant shared by every atrativo.
WEB_SEARCH_TOOL: dict[str, Any] = {
    "type": "web_search_20250305",
    "name": "web_search",
    "user_location": {"type": "approximate", "country": "BR"},
    "max_uses": 3,
}

# Cascade writer default — production reads AppConfig.atrativo_cascade_model (env
# ATRATIVO_CASCADE_MODEL). Gemini 2.5 Flash, measured side by side on the same context
# (docs/poc/gemini-viability.md §26): 3.3x cheaper per call than Haiku 4.5, 1.7x faster, fewer
# drafts to the DLQ and fewer defects past the gate; 135/140 approved on the Parallel turbo
# context (§29). The bare slug goes to Google AI Studio direct on the Flex tier (§30, ~$19 per
# 10k against ~$33 on OpenRouter); "google/gemini-2.5-flash" routes the same model through
# OpenRouter. Thinking stays off on both routes — on, it doubled cost and latency and brought
# the only truncated replies.
CASCADE_MODEL = "gemini-2.5-flash"

COPYWRITER_SYSTEM = """Você é um copywriter especialista em turismo e conhecedor de destinos brasileiros, escrevendo para a Norteia — uma bússola confiável que orienta jornadas pelo Brasil real, com presença e propósito. Voz: inspiradora, humana, curiosa, prática e acolhedora, para um público inclusivo (famílias, casais, viajantes solo) — nunca um único segmento.

OBJETIVO: gerar uma descrição envolvente, precisa e sensorial de um atrativo turístico, em português do Brasil.

ESTRUTURA:
- Comece com um gancho forte que situe o leitor no lugar.
- Traga o significado histórico e/ou cultural do atrativo.
- Termine com dicas EXPERIENCIAIS de visita: melhor hora do dia para ir, bons pontos para fotos, o que levar, o que observar. Dicas de experiência, não de logística.

TOM:
- Convidativo, imersivo e informativo. Valorize a brasilidade com orgulho, sem soar publicitário.

PRECISÃO (obrigatório):
- Baseie cada afirmação no contexto fornecido (Google Places, avaliações) ou em um resultado de busca na web. Use a ferramenta de busca quando precisar de mais contexto confiável.
- NUNCA invente comodidades, acessibilidade, história ou números. Se não houver informação verificável suficiente, escreva uma descrição sensorial mais curta, sem afirmações factuais específicas.

PROIBIÇÕES:
- NÃO inclua na prosa dados operacionais: horário de funcionamento, contato (telefone, site, redes), preço ou taxa de entrada, nem instruções de acesso/como chegar. Esses dados vivem em campos estruturados, fora da descrição — jamais os afirme.
- NUNCA use o caractere travessão "—" (nem "–"). Prefira vírgulas, pontos ou parênteses.
- Evite clichês ("joia escondida", "imperdível", "único no mundo", "o melhor de todos os tempos") e superlativos vagos.
- Sem títulos, sem emojis, sem listas, sem markdown. Prosa corrida.

Retorne APENAS a descrição final, sem comentários, sem preâmbulo."""


def _strip_dashes(text: str) -> str:
    """Remove em-dash / en-dash the model may emit despite the prompt.

    Replaces "word — word" style separators with a comma+space, and any bare dash with a
    space, then collapses the double spaces that leaves. Deterministic belt-and-suspenders:
    prompt instructions are not reliably obeyed.
    """
    # " — " (spaced separator) → ", "  ; bare — / – → " "
    out = text.replace(" — ", ", ").replace(" – ", ", ")
    out = out.replace("—", " ").replace("–", " ")
    while "  " in out:
        out = out.replace("  ", " ")
    return out.strip()


# Closing instruction per mode. The web_search one asks the model to search; in the cascade
# there is no tool, so that line would request an impossible action — and some models answer
# it by hallucinating "conforme pesquisei" (§23). The cascade closes on the injected sources.
_CLOSING_WEB_SEARCH = (
    "Escreva a descrição editorial da Norteia para este atrativo. Se o contexto acima "
    "for insuficiente, busque na web fontes confiáveis antes de escrever."
)
_CLOSING_CASCADE = (
    "Escreva a descrição editorial da Norteia para este atrativo, baseada apenas nas "
    "fontes acima. Se as fontes não trouxerem informação suficiente sobre este atrativo "
    "específico, escreva uma descrição sensorial mais curta, sem afirmações factuais "
    "específicas."
)


def _build_context(
    nome: str,
    municipio: str,
    uf: str,
    places_context: dict[str, Any],
    fontes: str | None = None,
) -> str:
    """Compose the grounding user message from the atrativo + Places fields.

    ``fontes`` (cascade mode) is the pre-fetched search context; it replaces the "search the
    web" closing line with the sources block. Without it the message is the web_search one.
    """
    editorial = (places_context.get("editorial_summary") or "").strip()
    types = places_context.get("types") or []
    address = (places_context.get("formatted_address") or "").strip()
    review_texts = [
        (r.get("text") or "").strip()
        for r in (places_context.get("reviews") or [])
        if (r.get("text") or "").strip()
    ][:3]

    lines = [f'Atrativo: "{nome}" — município {municipio}/{uf}.']
    if types:
        lines.append(f"Tipos (Google Places): {', '.join(str(t) for t in types)}.")
    if address:
        lines.append(f"Endereço: {address}.")
    if editorial:
        lines.append(f"Resumo do Google Places: {editorial}")
    if review_texts:
        lines.append("Trechos de avaliações de visitantes:")
        lines.extend(f"- {t}" for t in review_texts)
    if fontes is None:
        lines.append(_CLOSING_WEB_SEARCH)
    else:
        lines += ["", "FONTES ENCONTRADAS NA WEB (use apenas estas):", fontes, "", _CLOSING_CASCADE]
    return "\n".join(lines)


def cascade_queries(nome: str, municipio: str, uf: str) -> list[str]:
    """The two searches the cascade runs per atrativo — deterministic, no model involved.

    The first mirrors the query shape the production model emitted most (name + place +
    "história", §24 sample); the second quotes the name so the engine must match it verbatim,
    which is what the mention gate then checks. The production model's second query often
    carried a fact it already "knew" ("areia monazítica", "1558") — a deterministic lane
    cannot, and must not, inject memory into the search.
    """
    local = " ".join(x for x in (municipio, uf) if x)
    return [f"{nome} {local} história".strip(), f'"{nome}" {local} atrativo turístico'.strip()]


def cascade_objective(nome: str, municipio: str, uf: str) -> str:
    """The natural-language objective Parallel ranks the excerpts against (§29 wording)."""
    local = "/".join(x for x in (municipio, uf) if x)
    return (
        f"Fatos verificáveis sobre o atrativo turístico {nome}"
        + (f" em {local}" if local else "")
        + ": história, características, o que ver."
    )


@dataclass(frozen=True)
class CascadeResult:
    """Outcome of one cascade pass.

    prose:        passed BOTH gates — ready for descricao_editorial.
    motivo:       why there is no prose: "sem_mencao" (the search does not mention the
                  atrativo; the model was never called), "municipio_nao_confirmado" (the
                  search never names the record's município — likely a wrong-município
                  record; the model was never called) or "nao_fundamentada" (written, but
                  below MIN_GROUNDEDNESS). None on success and on a plain failure.
    rascunho:     the ungrounded prose, kept for steward review — never canonical.
    groundedness: grounded fraction of the concrete claims, when a text was generated.
    busca:        the paid search, whole — set on EVERY outcome after the search succeeded,
                  so the caller can persist it (atrativo_buscas) whatever the verdict.
    """

    prose: str | None
    motivo: str | None = None
    rascunho: str | None = None
    groundedness: float | None = None
    busca: ParallelSearch | None = None


class TourismCopywriter:
    """Writes a Norteia-voice atrativo description grounded in Places + web search.

    Two modes:
      - web_search (default, ``write``): one Sonnet call with the server-side web_search tool.
      - cascade (``search_client`` given, ``write_cascade``): the lane runs the search itself
        (Parallel), gates on mention and município, writes with a cheap model and NO tool, then
        gates the output on groundedness. Measured in docs/poc/gemini-viability.md §23-§29.

    Args:
        llm_client: LLMClientProtocol (Real uses Anthropic + web_search; Null returns a stub).
        model:      Anthropic model slug (a Sonnet slug — web_search runs there; CASCADE_MODEL
                    in cascade mode).
        enable_web_search: When False, the web_search tool is not offered (description is
                    grounded only in the Places context — cheaper, offline-safe).
        search_client: RealParallelClient-shaped (``async search(queries, objective) ->
                    ParallelSearch``). Enables the cascade; ``write`` is unaffected by it.
    """

    def __init__(
        self,
        llm_client: LLMClientProtocol,
        model: str = "claude-sonnet-4-5",
        *,
        enable_web_search: bool = True,
        search_client: Any = None,
    ) -> None:
        self._llm_client = llm_client
        self._model = model
        self._enable_web_search = enable_web_search
        self._search_client = search_client

    @property
    def cascade(self) -> bool:
        return self._search_client is not None

    async def write_cascade(
        self,
        nome: str,
        municipio: str,
        uf: str,
        places_context: dict[str, Any] | None = None,
    ) -> CascadeResult:
        """Search → mention gate → município gate → write (no tool) → groundedness gate.

        Same failure posture as ``write``: any search/LLM failure degrades to an empty result
        (the caller keeps the floor), except ``CostGuardError``, which propagates — both the
        search and the model check the budget before dispatch. A guard trip on the MODEL after
        a paid search loses that search (the exception carries no result); rare, and the next
        pass searches again.
        """
        if not nome or self._search_client is None:
            return CascadeResult(None)
        try:
            # Both queries in ONE request — billed once (§29).
            busca = await self._search_client.search(
                cascade_queries(nome, municipio, uf), cascade_objective(nome, municipio, uf)
            )
        except CostGuardError:
            logger.warning("copywriter_cost_guard_blocked", nome=nome, uf=uf)
            raise
        except Exception:  # noqa: BLE001 — search failure keeps the TA floor
            logger.warning("copywriter_search_failed_kept_floor", nome=nome, uf=uf)
            return CascadeResult(None)
        fontes = busca.fontes()

        # Input gate: context that does not name the atrativo is where every model fabricated
        # (§23.4). No model call → no spend, and the record goes on without a description.
        if not menciona(fontes, nome):
            logger.info("copywriter_gate_sem_mencao", nome=nome, uf=uf)
            return CascadeResult(None, motivo="sem_mencao", busca=busca)

        # The search never names the record's município: the record, not the search, is
        # likely wrong (Nascente geocoded a homonym). Writing would describe the real place
        # under the wrong town — both models did, and groundedness passed it (§26.4, §29.3).
        if not menciona_municipio(fontes, municipio):
            logger.info("copywriter_gate_municipio_nao_confirmado", nome=nome, uf=uf)
            return CascadeResult(None, motivo="municipio_nao_confirmado", busca=busca)

        user = _build_context(nome, municipio, uf, places_context or {}, fontes=fontes)
        try:
            raw = await self._llm_client.generate(
                [{"role": "user", "content": user}],
                model=self._model,
                system=COPYWRITER_SYSTEM,
                tools=None,
            )
        except CostGuardError:
            logger.warning("copywriter_cost_guard_blocked", nome=nome, uf=uf)
            raise
        except Exception:  # noqa: BLE001 — copywriter failure keeps the TA floor
            logger.warning("copywriter_failed_kept_floor", nome=nome, uf=uf)
            return CascadeResult(None, busca=busca)
        texto = _strip_dashes(raw or "")
        if not texto:
            return CascadeResult(None, busca=busca)

        # Output gate: grounded against EVERYTHING the model was given (Places context and the
        # atrativo header included), not only the search — those are facts we supplied too.
        ratio = groundedness_ratio(texto, user)
        if ratio < MIN_GROUNDEDNESS:
            logger.info("copywriter_gate_nao_fundamentada", nome=nome, uf=uf, groundedness=ratio)
            return CascadeResult(
                None, motivo="nao_fundamentada", rascunho=texto, groundedness=ratio, busca=busca
            )
        return CascadeResult(texto, groundedness=ratio, busca=busca)

    async def write(
        self,
        nome: str,
        municipio: str,
        uf: str,
        places_context: dict[str, Any] | None = None,
    ) -> str | None:
        """Return finished PT-BR prose, or None if generation yields nothing usable.

        Degrades to None on any LLM/search failure (the caller keeps the floor), with ONE
        exception: ``CostGuardError`` propagates. The guard raises it BEFORE dispatch, so no
        token was spent and no attempt happened — a caller with a retry budget must be able
        to tell that apart from "the model produced nothing" (see PlacesEnrichmentAgent's
        descricao_attempts).
        """
        if not nome:
            return None
        context = _build_context(nome, municipio, uf, places_context or {})
        tools = [WEB_SEARCH_TOOL] if self._enable_web_search else None
        try:
            raw = await self._llm_client.generate(
                [{"role": "user", "content": context}],
                model=self._model,
                system=COPYWRITER_SYSTEM,
                tools=tools,
            )
        except CostGuardError:  # no spend happened — never swallow into a "failed" None
            logger.warning("copywriter_cost_guard_blocked", nome=nome, uf=uf)
            raise
        except Exception:  # noqa: BLE001 — copywriter failure keeps the TA floor
            logger.warning("copywriter_failed_kept_floor", nome=nome, uf=uf)
            return None
        cleaned = _strip_dashes(raw or "")
        return cleaned or None


if __name__ == "__main__":  # pragma: no cover — ponytail runnable check
    # No network, no LLM: exercises the deterministic guard + context builder.
    assert _strip_dashes("A praia — larga — e calma.") == "A praia, larga, e calma."
    assert "—" not in _strip_dashes("Vista—mar")
    ctx = _build_context(
        "Praia de Camburi",
        "Vitória",
        "ES",
        {"editorial_summary": "Orla urbana.", "reviews": [{"text": "Linda ao pôr do sol"}]},
    )
    assert "Camburi" in ctx and "Orla urbana" in ctx and "pôr do sol" in ctx
    print("copywriter self-check ok")
