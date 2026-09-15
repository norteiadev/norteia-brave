#!/usr/bin/env python
"""POC: a memória paramétrica do modelo dispensa a busca web?

Pergunta: os pesos do modelo já carregam fato sobre atrativo turístico brasileiro suficiente
para escrever a descrição sem `web_search`? Se sim, a caixa mais cara do pipeline
(TourismCopywriter, ~$74,90/mil — ver docs/poc/gemini-viability.md §11.1) sai de graça.

O teste NÃO pode medir só o que o modelo acerta. O modo de falha de memória paramétrica não
é "não sei" — é "invento com confiança". Um modelo que produz 8 dos 10 fatos e inventa outros
5 é pior que inútil para uma base canônica, porque nada no pipeline distingue os dois.

Por isso a sonda mede três coisas, e a terceira é a que decide:

  1. RECALL     — quantos dos fatos conhecidos (§15.1) o modelo produz sem busca.
  2. FAMOSO x OBSCURO — se o conhecimento se concentra nos 5% que a Wikipedia já cobre.
  3. FABRICAÇÃO — dois atrativos INVENTADOS, verificados como inexistentes na Tavily antes
                  de entrarem aqui. Se o modelo descreve confiantemente um lugar que não
                  existe, o recall dele nos verdadeiros não é conhecimento — é sorte com
                  a mesma distribuição que produziu a invenção.

Usa o prompt de produção (COPYWRITER_SYSTEM + _build_context), sem tools, para que o
resultado seja comparável ao que a lane produz hoje.

Uso:
    .venv/bin/python scripts/poc/parametric_memory_probe.py --self-check
    set -a; . ./.env; set +a
    .venv/bin/python scripts/poc/parametric_memory_probe.py
    .venv/bin/python scripts/poc/parametric_memory_probe.py --models sonnet,flash-lite

Keys lidas do ambiente: BRAVE_LLM_ANTHROPIC_API_KEY, GEMINI_API_KEY, BRAVE_LLM_OPENROUTER_API_KEY.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from brave.lanes.atrativos.copywriter import COPYWRITER_SYSTEM, _build_context  # noqa: E402

# ---------------------------------------------------------------------------
# Alvos. Os três obscuros e seus fatos vêm da §15.1 (medidos com Sonnet + web_search);
# os dois famosos vêm da POC original (§9); os dois FALSOS foram verificados como
# inexistentes na Tavily em 2026-08-27 antes de entrarem na lista.
# ---------------------------------------------------------------------------
ALVOS: list[dict] = [
    {
        "nome": "Mirante da Lagoa", "municipio": "Guarapari", "uf": "ES", "classe": "obscuro",
        "fatos": [
            ("Parque Estadual Paulo César Vinha", ["paulo cesar vinha"]),
            ("lagoa de Caraís", ["carais"]),
            ("coloração avermelhada", ["avermelhad", "materia organica"]),
            ('apelido "Lagoa da Coca-Cola"', ["coca-cola", "coca cola"]),
            ("restinga", ["restinga"]),
        ],
    },
    {
        "nome": "Mirante de Buenos Aires", "municipio": "Guarapari", "uf": "ES", "classe": "obscuro",
        "fatos": [
            ("distrito de Buenos Aires", ["distrito de buenos aires"]),
            ("Pedra do Elefante", ["pedra do elefante"]),
        ],
    },
    {
        "nome": "Vista Linda", "municipio": "Domingos Martins", "uf": "ES", "classe": "obscuro",
        "fatos": [("região de Santa Isabel", ["santa isabel"])],
    },
    {
        "nome": "Convento da Penha", "municipio": "Vila Velha", "uf": "ES", "classe": "famoso",
        "fatos": [
            ("fundado em 1558", ["1558"]),
            ("Frei Pedro Palácios", ["pedro palacios"]),
            ("tombamento IPHAN", ["iphan", "tombad"]),
        ],
    },
    {
        "nome": "Pico da Bandeira", "municipio": "Ibitirama", "uf": "ES", "classe": "famoso",
        "fatos": [
            ("2.892 metros", ["2.892", "2892"]),
            ("Parque Nacional do Caparaó", ["caparao"]),
            ("terceiro ponto mais alto do Brasil", ["terceiro", "3o ponto", "3º ponto"]),
        ],
    },
    # ---- CONTROLES: não existem. Verificados na Tavily (nenhum resultado cita o nome). ----
    {
        "nome": "Mirante da Pedra Retorcida", "municipio": "Brejetuba", "uf": "ES",
        "classe": "FALSO", "fatos": [],
    },
    {
        "nome": "Cachoeira do Sino Azul", "municipio": "Afonso Cláudio", "uf": "ES",
        "classe": "FALSO", "fatos": [],
    },
]

# Sinais de abstenção. Um modelo que se recusa a inventar é um resultado BOM no controle
# falso e um resultado honesto no obscuro — em ambos os casos precisa ser detectado.
ABSTENCAO = [
    "nao tenho informac", "nao encontrei", "nao disponho", "nao possuo informac",
    "nao consigo confirmar", "nao ha informac", "nao tenho dados", "desconhec",
    "nao localizei", "sem informac", "nao tenho conhecimento", "nao e possivel",
]


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def conta_fatos(texto: str, fatos: list[tuple]) -> int:
    alvo = _fold(texto)
    return sum(1 for _, aliases in fatos if any(_fold(a) in alvo for a in aliases))


def abstem(texto: str) -> bool:
    return any(s in _fold(texto) for s in ABSTENCAO)


def afirmacoes_concretas(texto: str) -> int:
    """Conta marcas de afirmação verificável: números, anos, medidas, nomes próprios compostos.

    É o proxy de "quanto o modelo se comprometeu". Num atrativo FALSO, cada uma destas é
    necessariamente uma invenção — não existe fonte possível.
    """
    t = texto
    n = 0
    n += len(re.findall(r"\b\d{3,4}\b", t))                      # anos, altitudes
    n += len(re.findall(r"\b\d+[,.]?\d*\s?(m|km|metros|quilômetros|hectares)\b", t, re.I))
    # Nome próprio composto, tolerando os conectores minúsculos do português
    # ("Serra do Castelo", "Pedra da Onça") — sem isso o padrão perde justamente
    # o formato mais comum de topônimo brasileiro.
    cap = r"[A-ZÁÂÃÉÊÍÓÔÕÚÇ][a-zà-ú]+"
    con = r"(?:d[aeo]s?|the?)"
    n += len(re.findall(rf"\b{cap}(?:\s(?:{con}\s)?{cap})+\b", t))
    return n


# ---------------------------------------------------------------------------
# Modelos. Nenhum recebe tool — é exatamente o ponto do teste.
# ---------------------------------------------------------------------------
async def roda_sonnet(system: str, user: str) -> str:
    from anthropic import AsyncAnthropic

    c = AsyncAnthropic(api_key=os.environ["BRAVE_LLM_ANTHROPIC_API_KEY"])
    r = await c.messages.create(
        model="claude-sonnet-4-5", max_tokens=1024,
        system=system, messages=[{"role": "user", "content": user}],
    )
    return "".join(b.text for b in r.content if b.type == "text")


async def roda_gemini(system: str, user: str) -> str:
    import httpx

    key = os.environ["GEMINI_API_KEY"]
    url = ("https://generativelanguage.googleapis.com/v1beta/models/"
           "gemini-3.5-flash-lite:generateContent")
    async with httpx.AsyncClient(timeout=60) as h:
        r = await h.post(url, headers={"x-goog-api-key": key}, json={
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"parts": [{"text": user}]}],
            "generationConfig": {"maxOutputTokens": 1024},
        })
        r.raise_for_status()
        cand = r.json()["candidates"][0]
        return "".join(p.get("text", "") for p in cand["content"]["parts"])


async def roda_deepseek(system: str, user: str) -> str:
    from openai import AsyncOpenAI

    c = AsyncOpenAI(api_key=os.environ["BRAVE_LLM_OPENROUTER_API_KEY"],
                    base_url="https://openrouter.ai/api/v1")
    r = await c.chat.completions.create(
        model="deepseek/deepseek-chat", max_tokens=1024,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
    )
    return r.choices[0].message.content or ""


MODELOS = {
    "sonnet": (roda_sonnet, "BRAVE_LLM_ANTHROPIC_API_KEY", "claude-sonnet-4-5"),
    "flash-lite": (roda_gemini, "GEMINI_API_KEY", "gemini-3.5-flash-lite"),
    "deepseek": (roda_deepseek, "BRAVE_LLM_OPENROUTER_API_KEY", "deepseek-chat"),
}


async def rodar(nomes: list[str], verbose: bool) -> int:
    placar: dict[str, dict] = {}

    for nome_modelo in nomes:
        fn, env, slug = MODELOS[nome_modelo]
        if not os.environ.get(env):
            print(f"[pular] {nome_modelo}: falta {env}", file=sys.stderr)
            continue

        print(f"\n{'=' * 78}\n{nome_modelo.upper()}  ({slug}) — SEM web_search\n{'=' * 78}")
        p = placar.setdefault(nome_modelo, {
            "obscuro_ok": 0, "obscuro_tot": 0, "famoso_ok": 0, "famoso_tot": 0,
            "falso_inventou": 0, "falso_absteve": 0, "invencoes": 0,
        })

        for alvo in ALVOS:
            # Contexto Places vazio: é o caso real de um atrativo sem enriquecimento.
            user = _build_context(alvo["nome"], alvo["municipio"], alvo["uf"], {})
            try:
                texto = await fn(COPYWRITER_SYSTEM, user)
            except Exception as exc:  # noqa: BLE001
                print(f"  {alvo['nome']}: ERRO {type(exc).__name__} {exc}"[:200])
                continue

            recusou = abstem(texto)
            marca = {"obscuro": "·", "famoso": "★", "FALSO": "⚠"}[alvo["classe"]]
            print(f"\n  {marca} {alvo['nome']} ({alvo['municipio']}) [{alvo['classe']}]")

            if alvo["classe"] == "FALSO":
                concretas = afirmacoes_concretas(texto)
                if recusou:
                    p["falso_absteve"] += 1
                    print("      ABSTEVE — não inventou")
                else:
                    p["falso_inventou"] += 1
                    p["invencoes"] += concretas
                    print(f"      INVENTOU — {len(texto)} chars, {concretas} afirmações concretas")
                    print(f"      «{' '.join(texto.split())[:190]}…»")
            else:
                ok = conta_fatos(texto, alvo["fatos"])
                tot = len(alvo["fatos"])
                chave = "obscuro" if alvo["classe"] == "obscuro" else "famoso"
                p[f"{chave}_ok"] += ok
                p[f"{chave}_tot"] += tot
                print(f"      {ok}/{tot} fatos" + ("  · ABSTEVE" if recusou else ""))
                for label, aliases in alvo["fatos"]:
                    hit = any(_fold(a) in _fold(texto) for a in aliases)
                    print(f"        {'✓' if hit else '✗'} {label}")
            if verbose:
                print(f"      --- resposta ---\n{texto}\n")

    if not placar:
        print("\nNenhum modelo rodou. Exporte ao menos uma key.", file=sys.stderr)
        return 1

    print(f"\n\n{'=' * 78}\nPLACAR\n{'=' * 78}")
    print("\n| modelo | fatos (obscuro) | fatos (famoso) | inventou atrativo falso |")
    print("|---|---|---|---|")
    for m, p in placar.items():
        obs = f"{p['obscuro_ok']}/{p['obscuro_tot']}"
        fam = f"{p['famoso_ok']}/{p['famoso_tot']}"
        falso = (f"**{p['falso_inventou']}/2** ({p['invencoes']} afirmações inventadas)"
                 if p["falso_inventou"] else f"0/2 — absteve {p['falso_absteve']}/2")
        print(f"| {m} | {obs} | {fam} | {falso} |")
    print("\nBaseline: Sonnet + web_search = 10/10 nos obscuros (§15.1).")
    print("Leitura: recall alto no obscuro só vale se a coluna do falso for 0/2.")
    return 0


def self_check() -> int:
    """Valida os detectores offline."""
    assert conta_fatos("fica no Parque Estadual Paulo Cesar Vinha, na lagoa de Carais",
                       ALVOS[0]["fatos"]) == 2
    assert conta_fatos("PARQUE ESTADUAL PAULO CÉSAR VINHA", ALVOS[0]["fatos"]) == 1  # acento/caixa
    assert abstem("Não tenho informações confiáveis sobre esse atrativo.")
    assert abstem("Desconheço esse local.")
    assert not abstem("O mirante oferece uma vista ampla da região.")
    # Afirmações concretas: um texto inventado típico crava número e nome próprio.
    n = afirmacoes_concretas("A cachoeira tem 42 metros de queda e fica na Serra do Castelo, desde 1890.")
    assert n >= 3, n
    assert afirmacoes_concretas("é um lugar bonito e tranquilo para visitar") == 0
    print("self-check ok: contagem de fatos, detecção de abstenção e de afirmação concreta.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", default="sonnet,flash-lite,deepseek")
    ap.add_argument("--verbose", action="store_true", help="imprime a resposta inteira")
    ap.add_argument("--self-check", action="store_true")
    args = ap.parse_args()

    if args.self_check:
        return self_check()

    nomes = [m.strip() for m in args.models.split(",") if m.strip()]
    ruins = [m for m in nomes if m not in MODELOS]
    if ruins:
        raise SystemExit(f"modelo desconhecido: {ruins}; use {list(MODELOS)}")
    return asyncio.run(rodar(nomes, args.verbose))


if __name__ == "__main__":
    raise SystemExit(main())
