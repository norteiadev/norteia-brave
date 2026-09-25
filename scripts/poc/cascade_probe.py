#!/usr/bin/env python
"""POC: a metade que falta da cascata — o modelo barato escreve bem com o snippet da Tavily?

A §18 mediu o INSUMO: o snippet da Tavily carrega 9 dos 10 fatos que o `web_search` da
Anthropic carrega, por 2.311 tokens em vez de 11.900. A §22 mediu o provedor: revendedor
de SERP não serve, o passo de busca precisa ser extrativo.

Nenhuma das duas mediu a REDAÇÃO. A linha "flash-lite free + contexto = $0" da §11.3 é
projeção. Ninguém nunca pegou os 2.311 tokens de snippet, jogou num modelo barato e leu o
que saiu. Esta sonda faz isso.

O teste não pode medir só o recall. O modo de falha aqui tem três formas, e as três
precisam aparecer no mesmo placar:

  1. TRANSFERÊNCIA — o fato está no contexto; o modelo o coloca na prosa? Fato que fica
     no snippet e não entra no texto é fato que a base não recebe.
  2. FABRICAÇÃO — dois atrativos INVENTADOS (os mesmos controles da §19) entram com o
     contexto real que a Tavily devolve para eles, que é ruído. Um modelo que escreve
     confiante sobre um lugar inexistente com o contexto na frente é pior que um que
     escreve sem contexto: ele teve a chance de perceber e não percebeu.
  3. OBEDIÊNCIA — o COPYWRITER_SYSTEM proíbe travessão, markdown, clichê e dado
     operacional (horário, preço, telefone, como chegar). Modelo barato costuma quebrar
     regra de formato, e a lane grava a saída direto na coluna.

Contexto idêntico para todos os modelos: a busca roda UMA vez, é gravada em cache no
disco, e todo modelo recebe byte a byte o mesmo texto. Sem isso a comparação mediria a
variância do ranking da Tavily, não a diferença entre os modelos.

Uso:
    .venv/bin/python scripts/poc/cascade_probe.py --self-check          # offline, sem key
    set -a; . ./.env; set +a
    .venv/bin/python scripts/poc/cascade_probe.py --fetch               # busca e cacheia
    .venv/bin/python scripts/poc/cascade_probe.py                       # roda os modelos
    .venv/bin/python scripts/poc/cascade_probe.py --models haiku,deepseek --verbose

Keys: TAVILY_API_KEY (só com --fetch), BRAVE_LLM_ANTHROPIC_API_KEY,
BRAVE_LLM_OPENROUTER_API_KEY, GEMINI_API_KEY.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from parametric_memory_probe import (  # noqa: E402
    _fold,
    abstem,
    afirmacoes_concretas,
)
from search_snippets_probe import buscar_tavily  # noqa: E402

from brave.domains.places.copywriter import COPYWRITER_SYSTEM, _build_context  # noqa: E402

CACHE = Path(__file__).with_name("cascade_probe_context.json")

# ---------------------------------------------------------------------------
# Alvos. Os três obscuros e seus fatos são os MESMOS da §15.1/§18 — é o que torna este
# número comparável com o 9/10 do insumo e com o 10/10 do Sonnet + web_search.
# Os dois FALSOS são os controles da §19, verificados como inexistentes na Tavily.
# ---------------------------------------------------------------------------
ALVOS: list[dict] = [
    {
        "nome": "Mirante da Lagoa",
        "municipio": "Guarapari",
        "uf": "ES",
        "classe": "obscuro",
        "fatos": [
            ("Parque Estadual Paulo César Vinha", ["paulo cesar vinha"]),
            ("lagoa de Caraís", ["carais"]),
            ("coloração avermelhada", ["avermelhad", "escur", "materia organica", "tanino"]),
            ('apelido "Lagoa da Coca-Cola"', ["coca-cola", "coca cola"]),
            ("restinga", ["restinga"]),
        ],
    },
    {
        "nome": "Mirante de Buenos Aires",
        "municipio": "Guarapari",
        "uf": "ES",
        "classe": "obscuro",
        "fatos": [
            ("distrito de Buenos Aires", ["buenos aires"]),
            ("Pedra do Elefante", ["pedra do elefante"]),
            (
                "origem do nome",
                [
                    "recebe este nome",
                    "recebe o nome",
                    "recebeu o nome",
                    "deve o nome",
                    "batizad",
                    "nome por",
                ],
            ),
        ],
    },
    {
        "nome": "Vista Linda",
        "municipio": "Domingos Martins",
        "uf": "ES",
        "classe": "obscuro",
        "fatos": [
            ("região de Santa Isabel", ["santa isabel"]),
            ("serra de Domingos Martins", ["domingos martins"]),
        ],
    },
    {
        "nome": "Mirante da Pedra Retorcida",
        "municipio": "Brejetuba",
        "uf": "ES",
        "classe": "FALSO",
        "fatos": [],
    },
    {
        "nome": "Cachoeira do Sino Azul",
        "municipio": "Afonso Cláudio",
        "uf": "ES",
        "classe": "FALSO",
        "fatos": [],
    },
]

# Regras do COPYWRITER_SYSTEM que dá para verificar deterministicamente. Não cobrem
# "voz da Norteia" — isso exige leitura humana, e é por isso que --verbose existe.
PROIBIDO_OPERACIONAL = [
    "horario de funcionamento",
    "aberto de",
    "funciona das",
    "das 8h",
    "das 9h",
    "ingresso",
    "entrada custa",
    "taxa de entrada",
    "r$",
    "telefone",
    "whatsapp",
    "www.",
    "http",
    "como chegar",
    "acesso se da",
    "de carro pela",
    "rodovia es-",
]

# $/MTok (in, out). Anthropic: tabela oficial. OpenRouter/Gemini: o custo real vem da
# resposta quando o provedor o informa; estes valores são o fallback.
PRECOS = {
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "deepseek/deepseek-chat": (0.28, 0.42),
    "gemini-3.5-flash-lite": (0.0, 0.0),  # free tier
}

CUSTO_BUSCA_TAVILY = 0.008 * 2  # 2 queries por atrativo (§18.2)


# ---------------------------------------------------------------------------
# Contexto: busca uma vez, grava, reusa. Modelo diferente com contexto diferente não é
# comparação de modelo.
# ---------------------------------------------------------------------------
def queries(alvo: dict) -> list[str]:
    """As mesmas duas variantes da §18 — só nome e município, nenhum termo da lista."""
    return [
        f"{alvo['nome']} {alvo['municipio']} {alvo['uf']} atrativo turístico",
        f"{alvo['nome']} {alvo['municipio']} o que é como chegar",
    ]


def buscar_tudo() -> dict[str, str]:
    key = os.environ.get("TAVILY_API_KEY")
    if not key:
        raise SystemExit("--fetch exige TAVILY_API_KEY (set -a; . ./.env; set +a)")
    out: dict[str, str] = {}
    for alvo in ALVOS:
        partes = []
        for q in queries(alvo):
            txt, n = buscar_tavily(q, key, ler_paginas=False)
            partes.append(txt)
            print(f"  {alvo['nome']}: {n} resultados para «{q}»", file=sys.stderr)
        out[alvo["nome"]] = "\n\n".join(partes)
    CACHE.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"\ncontexto gravado em {CACHE} — custo ${CUSTO_BUSCA_TAVILY * len(ALVOS):.3f}")
    return out


def carregar_contexto() -> dict[str, str]:
    if not CACHE.exists():
        raise SystemExit(f"sem contexto em {CACHE}; rode --fetch primeiro")
    return json.loads(CACHE.read_text())


# A última linha do _build_context manda buscar na web. Na cascata não há ferramenta de
# busca — quem buscou foi a Tavily, antes. Deixar a linha seria pedir ao modelo uma ação
# impossível, e alguns modelos respondem a isso alucinando "conforme pesquisei".
_CAUDA = "for insuficiente, busque na web fontes confiáveis antes de escrever."


def montar_user(alvo: dict, contexto: str) -> str:
    base = _build_context(alvo["nome"], alvo["municipio"], alvo["uf"], {})
    cabeca, _, cauda = base.rpartition("\n")
    if _CAUDA not in cauda:  # o prompt de produção mudou; falhar alto, não silencioso
        raise SystemExit(f"_build_context mudou: a última linha não é a de busca:\n{cauda!r}")
    return (
        f"{cabeca}\n\n"
        "FONTES ENCONTRADAS NA WEB (use apenas estas):\n"
        f"{contexto}\n\n"
        "Escreva a descrição editorial da Norteia para este atrativo, baseada apenas nas "
        "fontes acima. Se as fontes não trouxerem informação suficiente sobre este "
        "atrativo específico, escreva uma descrição sensorial mais curta, sem afirmações "
        "factuais específicas."
    )


# ---------------------------------------------------------------------------
# Medidores
# ---------------------------------------------------------------------------
def fatos_no_texto(texto: str, fatos: list[tuple]) -> list[tuple[str, bool]]:
    alvo = _fold(texto)
    return [(label, any(_fold(a) in alvo for a in aliases)) for label, aliases in fatos]


def violacoes(texto: str) -> list[str]:
    """Regras do prompt de produção que dá para checar sem ler."""
    v = []
    if "—" in texto or "–" in texto:
        v.append("travessão")
    if any(m in texto for m in ("**", "##", "- ", "\n1.")):
        v.append("markdown/lista")
    t = _fold(texto)
    achados = [p for p in PROIBIDO_OPERACIONAL if p in t]
    if achados:
        v.append(f"operacional({achados[0]})")
    return v


# ---------------------------------------------------------------------------
# Modelos. Nenhum recebe ferramenta — o contexto já vem pronto.
# ---------------------------------------------------------------------------
async def roda_anthropic(slug: str, system: str, user: str) -> tuple[str, int, int, float | None]:
    from anthropic import AsyncAnthropic

    c = AsyncAnthropic(api_key=os.environ["BRAVE_LLM_ANTHROPIC_API_KEY"])
    r = await c.messages.create(
        model=slug,
        max_tokens=1024,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    texto = "".join(b.text for b in r.content if b.type == "text")
    return texto, r.usage.input_tokens, r.usage.output_tokens, None


async def roda_openrouter(slug: str, system: str, user: str) -> tuple[str, int, int, float | None]:
    from openai import AsyncOpenAI

    c = AsyncOpenAI(
        api_key=os.environ["BRAVE_LLM_OPENROUTER_API_KEY"],
        base_url="https://openrouter.ai/api/v1",
    )
    r = await c.chat.completions.create(
        model=slug,
        max_tokens=1024,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        # O OpenRouter devolve o custo real em USD quando pedido. Preço de tabela do
        # DeepSeek varia por provedor roteado; medir é melhor que estimar.
        extra_body={"usage": {"include": True}},
    )
    u = r.usage
    custo = getattr(u, "cost", None) if u else None
    return (
        r.choices[0].message.content or "",
        getattr(u, "prompt_tokens", 0) or 0,
        getattr(u, "completion_tokens", 0) or 0,
        custo,
    )


async def roda_gemini(slug: str, system: str, user: str) -> tuple[str, int, int, float | None]:
    import httpx

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{slug}:generateContent"
    async with httpx.AsyncClient(timeout=90) as h:
        r = await h.post(
            url,
            headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]},
            json={
                "system_instruction": {"parts": [{"text": system}]},
                "contents": [{"parts": [{"text": user}]}],
                "generationConfig": {"maxOutputTokens": 1024},
            },
        )
        r.raise_for_status()
        d = r.json()
        cand = d["candidates"][0]
        texto = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []))
        u = d.get("usageMetadata", {})
        return texto, u.get("promptTokenCount", 0), u.get("candidatesTokenCount", 0), 0.0


async def roda_producao(slug: str, system: str, user: str) -> tuple[str, int, int, float | None]:
    """A lane de HOJE: Sonnet + web_search, com o prompt de produção intacto.

    É o controle que decide como ler o placar. Se a lane atual também inventar nos
    atrativos falsos, a fabricação não é regressão da cascata — é defeito preexistente,
    e a §19 (que mediu sem busca nenhuma) não podia ver isso.
    """
    from anthropic import AsyncAnthropic

    from brave.domains.places.copywriter import WEB_SEARCH_TOOL

    c = AsyncAnthropic(api_key=os.environ["BRAVE_LLM_ANTHROPIC_API_KEY"])
    r = await c.messages.create(
        model=slug,
        max_tokens=1024,
        system=system,
        tools=[WEB_SEARCH_TOOL],
        messages=[{"role": "user", "content": user}],
    )
    texto = "".join(b.text for b in r.content if b.type == "text")
    u = r.usage
    # A taxa do web_search não vem no usage; é $10/1.000 buscas (§11.1).
    buscas = getattr(getattr(u, "server_tool_use", None), "web_search_requests", 0) or 0
    custo = u.input_tokens * 3.0 / 1e6 + u.output_tokens * 15.0 / 1e6 + buscas * 0.01
    return texto, u.input_tokens, u.output_tokens, custo


MODELOS: dict[str, tuple] = {
    # nome curto: (runner, slug, env da key)
    "sonnet-4-5": (roda_anthropic, "claude-sonnet-4-5", "BRAVE_LLM_ANTHROPIC_API_KEY"),
    "sonnet-5": (roda_anthropic, "claude-sonnet-5", "BRAVE_LLM_ANTHROPIC_API_KEY"),
    "haiku-4-5": (roda_anthropic, "claude-haiku-4-5", "BRAVE_LLM_ANTHROPIC_API_KEY"),
    "deepseek": (roda_openrouter, "deepseek/deepseek-chat", "BRAVE_LLM_OPENROUTER_API_KEY"),
    "flash-lite": (roda_gemini, "gemini-3.5-flash-lite", "GEMINI_API_KEY"),
    # O controle. Não recebe o contexto da Tavily: usa o prompt de produção inteiro,
    # com a instrução de buscar na web e a ferramenta ligada. É a lane de hoje.
    "producao": (roda_producao, "claude-sonnet-4-5", "BRAVE_LLM_ANTHROPIC_API_KEY"),
}
SEM_CONTEXTO_INJETADO = {"producao"}


def custo_llm(slug: str, tin: int, tout: int, informado: float | None) -> float:
    if informado is not None:
        return informado
    pin, pout = PRECOS.get(slug, (0.0, 0.0))
    return tin * pin / 1e6 + tout * pout / 1e6


async def rodar(nomes: list[str], verbose: bool) -> int:
    contextos = carregar_contexto()
    placar: dict[str, dict] = {}

    for nome_modelo in nomes:
        fn, slug, env = MODELOS[nome_modelo]
        if not os.environ.get(env):
            print(f"[pular] {nome_modelo}: falta {env}", file=sys.stderr)
            continue

        print(
            f"\n{'=' * 78}\n{nome_modelo.upper()}  ({slug}) — contexto Tavily, sem tool\n{'=' * 78}"
        )
        p = placar.setdefault(
            nome_modelo,
            {
                "ok": 0,
                "tot": 0,
                "custo": 0.0,
                "n": 0,
                "viol": 0,
                "falso_inventou": 0,
                "falso_absteve": 0,
                "invencoes": 0,
                "erro": 0,
            },
        )

        for alvo in ALVOS:
            if nome_modelo in SEM_CONTEXTO_INJETADO:
                user = _build_context(alvo["nome"], alvo["municipio"], alvo["uf"], {})
            else:
                user = montar_user(alvo, contextos.get(alvo["nome"], ""))
            try:
                texto, tin, tout, informado = await fn(slug, COPYWRITER_SYSTEM, user)
            except Exception as exc:  # noqa: BLE001 - a sonda não pode morrer por um modelo
                p["erro"] += 1
                print(f"  {alvo['nome']}: ERRO {type(exc).__name__}: {str(exc)[:160]}")
                continue

            custo = custo_llm(slug, tin, tout, informado)
            p["custo"] += custo
            p["n"] += 1
            viol = violacoes(texto)
            p["viol"] += len(viol)

            marca = {"obscuro": "·", "FALSO": "⚠"}[alvo["classe"]]
            print(f"\n  {marca} {alvo['nome']} ({alvo['municipio']}) [{alvo['classe']}]")
            print(f"      {tin} in / {tout} out · ${custo:.5f} · {len(texto)} chars")

            if alvo["classe"] == "FALSO":
                concretas = afirmacoes_concretas(texto)
                if abstem(texto):
                    p["falso_absteve"] += 1
                    print("      ABSTEVE — não inventou")
                else:
                    p["falso_inventou"] += 1
                    p["invencoes"] += concretas
                    print(f"      INVENTOU — {concretas} afirmações concretas")
                    print(f"      «{' '.join(texto.split())[:200]}…»")
            else:
                checados = fatos_no_texto(texto, alvo["fatos"])
                ok = sum(1 for _, achou in checados if achou)
                p["ok"] += ok
                p["tot"] += len(checados)
                print(f"      {ok}/{len(checados)} fatos na prosa")
                for label, achou in checados:
                    print(f"        {'✓' if achou else '✗'} {label}")

            if viol:
                print(f"      ⚠ viola o prompt: {', '.join(viol)}")
            if verbose:
                print(f"      --- texto ---\n{texto}\n")

    if not placar:
        print("\nNenhum modelo rodou. Exporte ao menos uma key.", file=sys.stderr)
        return 1

    print(f"\n\n{'=' * 78}\nPLACAR\n{'=' * 78}")
    print(
        "\n| modelo | fatos na prosa | inventou falso | viola prompt | $/atrativo (LLM) "
        "| $/atrativo (+busca) |"
    )
    print("|---|---|---|---|---|---|")
    for m, p in placar.items():
        if not p["n"]:
            continue
        med = p["custo"] / p["n"]
        falso = (
            f"**{p['falso_inventou']}/2** ({p['invencoes']} afirmações)"
            if p["falso_inventou"]
            else f"0/2 — absteve {p['falso_absteve']}/2"
        )
        erro = f" · {p['erro']} erros" if p["erro"] else ""
        # A linha de produção já paga a busca dentro do próprio custo (taxa do web_search).
        busca = 0.0 if m in SEM_CONTEXTO_INJETADO else CUSTO_BUSCA_TAVILY
        print(
            f"| {m} | {p['ok']}/{p['tot']} | {falso} | {p['viol']}{erro} | "
            f"${med:.5f} | ${med + busca:.4f} |"
        )
    print("\nBaselines: insumo Tavily = 9/10 fatos NO CONTEXTO (§18).")
    print("           Sonnet + web_search = 10/10, $0,0758/atrativo (§15.1).")
    print("Leitura: fato alto só vale com a coluna do falso em 0/2. Transferência abaixo")
    print("         de 9/10 é perda da redação, não da busca — o fato estava no contexto.")
    return 0


def self_check() -> int:
    """Valida os medidores e a montagem do prompt. Offline, sem key."""
    # Transferência: o fato precisa ser lido no TEXTO, não no contexto.
    prosa = "A trilha cruza a restinga até a lagoa de Carais, de água escura."
    achados = [lbl for lbl, ok in fatos_no_texto(prosa, ALVOS[0]["fatos"]) if ok]
    assert len(achados) == 3, achados  # carais, escur, restinga
    assert fatos_no_texto("LAGOA DE CARAÍS", ALVOS[0]["fatos"])[1][1]  # acento/caixa

    # Violações: cada regra do prompt que dá para checar.
    assert violacoes("um lugar bonito, tranquilo e aberto ao vento") == []
    assert "travessão" in violacoes("o mirante — alto e calmo")
    assert any("markdown" in v for v in violacoes("**Mirante**\nvista boa"))
    assert any("operacional" in v for v in violacoes("Funciona das 8h às 17h."))
    assert any("operacional" in v for v in violacoes("A entrada custa R$ 20."))

    # Montagem: a cauda "busque na web" sai, as fontes entram, o resto do contexto fica.
    user = montar_user(ALVOS[0], "TITULO\nhttps://x/y\ncorpo do snippet")
    assert _CAUDA not in user, "a instrução de buscar na web não foi removida"
    assert "corpo do snippet" in user
    assert 'Atrativo: "Mirante da Lagoa"' in user
    assert user.count("FONTES ENCONTRADAS") == 1

    # Custo: o valor informado pelo provedor vence a tabela.
    assert custo_llm("claude-haiku-4-5", 1_000_000, 0, None) == 1.0
    assert custo_llm("claude-haiku-4-5", 1_000_000, 0, 0.42) == 0.42

    print("self-check ok: transferência de fato, as 3 classes de violação, remoção da")
    print("cauda de busca, injeção das fontes e precedência do custo informado.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--models", default="sonnet-4-5,haiku-4-5,deepseek,flash-lite")
    ap.add_argument("--fetch", action="store_true", help="busca na Tavily e grava o cache")
    ap.add_argument("--verbose", action="store_true", help="imprime a descrição inteira")
    ap.add_argument("--self-check", action="store_true")
    args = ap.parse_args()

    if args.self_check:
        return self_check()
    if args.fetch:
        buscar_tudo()
        return 0

    nomes = [m.strip() for m in args.models.split(",") if m.strip()]
    ruins = [m for m in nomes if m not in MODELOS]
    if ruins:
        raise SystemExit(f"modelo desconhecido: {ruins}; use {list(MODELOS)}")
    return asyncio.run(rodar(nomes, args.verbose))


if __name__ == "__main__":
    raise SystemExit(main())
