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

from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from brave.clients.base import LLMClientProtocol

logger = structlog.get_logger(__name__)

# Basic server-side web_search variant — broadly supported (incl. claude-sonnet-4-5) on the
# pinned anthropic 0.109.x. max_uses bounds per-description search cost.
WEB_SEARCH_TOOL: dict[str, Any] = {
    "type": "web_search_20250305",
    "name": "web_search",
    "max_uses": 3,
}

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


def _build_context(nome: str, municipio: str, uf: str, places_context: dict[str, Any]) -> str:
    """Compose the grounding user message from the atrativo + Places fields."""
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
    lines.append(
        "Escreva a descrição editorial da Norteia para este atrativo. Se o contexto acima "
        "for insuficiente, busque na web fontes confiáveis antes de escrever."
    )
    return "\n".join(lines)


class TourismCopywriter:
    """Writes a Norteia-voice atrativo description grounded in Places + web search.

    Args:
        llm_client: LLMClientProtocol (Real uses Anthropic + web_search; Null returns a stub).
        model:      Anthropic model slug (a Sonnet slug — web_search runs there).
        enable_web_search: When False, the web_search tool is not offered (description is
                    grounded only in the Places context — cheaper, offline-safe).
    """

    def __init__(
        self,
        llm_client: LLMClientProtocol,
        model: str = "claude-sonnet-4-5",
        *,
        enable_web_search: bool = True,
    ) -> None:
        self._llm_client = llm_client
        self._model = model
        self._enable_web_search = enable_web_search

    async def write(
        self,
        nome: str,
        municipio: str,
        uf: str,
        places_context: dict[str, Any] | None = None,
    ) -> str | None:
        """Return finished PT-BR prose, or None if generation yields nothing usable.

        Never raises — any LLM/search failure degrades to None (the caller keeps the floor).
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
