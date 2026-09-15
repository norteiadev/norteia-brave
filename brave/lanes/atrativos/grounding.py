"""Deterministic grounding gates for the cascade copywriter — no LLM, no network.

Two gates, one on each side of the model call (docs/poc/gemini-viability.md §23-§24):

  - INPUT, ``menciona``: does the search context talk about THIS atrativo, not only its
    município? Context that only talks about the município is the exact input that produced
    confident fabrication in 5 of 5 models (§23.4). The prompt's abstention instruction does
    NOT hold — Sonnet wrote "não achei informação verificável" and then 1,898 characters of
    invented facts — so the decision has to be made before the model is called.
  - OUTPUT, ``groundedness``: what fraction of the concrete claims in the generated text
    (years, measures, compound proper names) appears in the context that fed it? A claim not
    in the context came from parametric memory or from invention, and nothing downstream can
    tell the two apart — so the rule is mechanical, not a judgment.

Ported from scripts/poc/cascade_scale_probe.py (which now imports it from here).

D-18 boundary: no imports from brave.lanes.destinos or brave.tasks.
"""

from __future__ import annotations

import re
import unicodedata

# Below this fraction of grounded claims the prose is NOT written to descricao_editorial; it
# is parked for steward review instead (see PlacesEnrichmentAgent).
#
# First calibrated on §24 (28 texts, 130 claims), which looked bimodal — 0.33-0.50 (famous
# places written from memory) and 0.86-1.00, nothing between. The §25 run (189 texts over 200
# real atrativos) filled that band: ten texts sit at 0.62-0.73, eight of them at exactly
# 0.67 (one claim in three loose). The distribution is continuous; there is no natural cut.
#   0.75 (kept): 17 of 189 (9%) to review — no text with a third of its claims ungrounded
#                reaches descricao_editorial. ~900 drafts on 10k atrativos.
#   0.60:         7 of 189 (4%) — only texts where most claims are loose. ~370 drafts.
# 0.75 stays because the failure it prevents (a true-but-unsourced fact in the canonical base,
# §24.2) is exactly what nothing downstream can detect, and review load is the cheaper side.
# Lower it on steward evidence, not on volume.
MIN_GROUNDEDNESS: float = 0.75

# Words that alone do not identify the atrativo — "praia" matches any coastal text. Without
# this the mention gate inflates: generic município context would count as coverage by
# containing "praia", which is the opposite of what the gate must detect.
GENERICAS = {
    "praia",
    "parque",
    "museu",
    "igreja",
    "centro",
    "mirante",
    "cachoeira",
    "lagoa",
    "ilha",
    "morro",
    "pico",
    "serra",
    "rio",
    "ponte",
    "mercado",
    "feira",
    "teatro",
    "catedral",
    "santuario",
    "convento",
    "forte",
    "farol",
    "trilha",
    "cristo",
    "jardim",
    "monumento",
    "palacio",
    "casa",
    "memorial",
    "estatua",
    "orla",
    "baia",
    "canal",
    "historico",
    "municipal",
    "estadual",
    "nacional",
    "natural",
    "turistico",
    "velha",
    "nova",
    "grande",
    "pequeno",
    "alto",
    "novo",
    "velho",
    "sao",
    "santa",
    "santo",
    "de",
    "da",
    "do",
    "das",
    "dos",
    "e",
    "a",
    "o",
    "as",
    "os",
    "em",
    "no",
    "na",
}


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def termos_identificadores(nome: str) -> list[str]:
    """The words of the name that actually identify the atrativo.

    "Praia Da Costa" → ["costa"]. "Convento da Penha" → ["penha"]. When nothing is left
    (a fully generic name, e.g. "Centro Histórico") the whole folded name is returned — then
    only a context that writes the full expression matches.
    """
    # A token with no letter or digit (an emoji: "Figueira Da Esquina 🌳❤️", §25) can never
    # appear in search text, so keeping it would block the atrativo forever.
    palavras = [
        p
        for p in _fold(nome).replace("-", " ").split()
        if len(p) > 2 and any(c.isalnum() for c in p)
    ]
    fortes = [p for p in palavras if p not in GENERICAS]
    return fortes or [_fold(nome)]


def menciona(contexto: str, nome: str) -> bool:
    """The context talks about THIS atrativo, not only about its município."""
    alvo = _fold(contexto)
    return all(t in alvo for t in termos_identificadores(nome))


def menciona_municipio(contexto: str, municipio: str) -> bool:
    """The context names the record's município at least once (whole words, accent-folded).

    Catches the gross wrong-município record: "Cristo Redentor" filed under Ubá/MG got 0
    mentions of Ubá in the Parallel context, while every other of the 106 §29 atrativos with a
    município got ≥ 11. Word boundaries matter — a substring test finds "uba" inside "cuba".
    Limited on purpose: the cascade queries carry the município, so a record whose município is
    wrong but whose search still returns pages about that town (e.g. a homonym) passes. An empty
    município is not judged.
    """
    if not municipio.strip():
        return True
    return re.search(rf"\b{re.escape(_fold(municipio.strip()))}\b", _fold(contexto)) is not None


def afirmacoes_concretas(texto: str) -> list[str]:
    """The verifiable claims of a text: numbers, measures and compound proper names.

    What can be checked against the context without a hand-written answer key — and exactly
    the class §23.4 saw being invented ("50 metros de queda").
    """
    fora = []
    fora += re.findall(r"\b\d{3,4}\b", texto)  # years, altitudes
    # findall with ONE group returns strings, not tuples — indexing here would take the first
    # character ("5" instead of "50 metros") and let every measure pass as ungrounded.
    fora += re.findall(r"\b\d+[,.]?\d*\s?(?:m|km|metros|quilômetros|hectares)\b", texto, re.I)
    cap = r"[A-ZÁÂÃÉÊÍÓÔÕÚÇ][a-zà-ú]+"
    con = r"(?:d[aeo]s?)"
    fora += re.findall(rf"\b{cap}(?:\s(?:{con}\s)?{cap})+\b", texto)
    return fora


def groundedness(texto: str, contexto: str) -> tuple[int, int, list[str]]:
    """How many concrete claims of the text exist in the context that fed it.

    Returns (grounded, total, the loose ones).
    """
    alvo = _fold(contexto)
    claims = afirmacoes_concretas(texto)
    soltas = [c for c in claims if _fold(c) not in alvo]
    return len(claims) - len(soltas), len(claims), soltas


def groundedness_ratio(texto: str, contexto: str) -> float:
    """Grounded fraction of the concrete claims; 1.0 when there are none.

    A text with no concrete claim is the sensory-only prose the prompt asks for when the
    sources are thin — nothing in it can be false, so it passes.
    """
    ok, tot, _ = groundedness(texto, contexto)
    return ok / tot if tot else 1.0


if __name__ == "__main__":  # pragma: no cover — ponytail runnable check
    assert termos_identificadores("Praia Da Costa") == ["costa"]
    assert not menciona("As praias de Vila Velha atraem visitantes.", "Praia Da Costa")
    assert menciona("O calçadão da Praia da Costa em Vila Velha", "Praia Da Costa")
    assert termos_identificadores("Centro Histórico") == ["centro historico"]
    assert termos_identificadores("Figueira Da Esquina 🌳❤️") == ["figueira", "esquina"]
    assert groundedness_ratio("um lugar bonito", "x") == 1.0
    assert menciona_municipio("Turismo em Ubá, MG", "Ubá")
    assert not menciona_municipio("Viagem para Cuba", "Ubá")
    assert menciona_municipio("qualquer coisa", "")
    print("grounding self-check ok")
