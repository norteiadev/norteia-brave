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
# Calibrated on the §24 distribution (scripts/poc/cascade_scale_probe.json — 28 texts, 130
# concrete claims). It is bimodal with an empty band between 0.50 and 0.86:
#   0.33 Copacabana · 0.40 Pelourinho · 0.50 Quadrado   ← famous places, prose from memory
#   0.86-0.92 × 5  (one stray "Mata Atlântica" / "Zona Portuária")
#   1.00 × 20
# Any cut inside (0.50, 0.86) splits this sample identically; 0.75 sits mid-band so a text
# with one generic stray out of 4 claims still passes, and one with 2 of 4 does not. It routes
# 3 of 28 (11%) to review — all three are fame-driven memory prose, true but unverifiable.
# Recalibrate from a larger sample before tightening: 28 texts is small.
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
    palavras = [p for p in _fold(nome).replace("-", " ").split() if len(p) > 2]
    fortes = [p for p in palavras if p not in GENERICAS]
    return fortes or [_fold(nome)]


def menciona(contexto: str, nome: str) -> bool:
    """The context talks about THIS atrativo, not only about its município."""
    alvo = _fold(contexto)
    return all(t in alvo for t in termos_identificadores(nome))


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
    assert groundedness_ratio("um lugar bonito", "x") == 1.0
    print("grounding self-check ok")
