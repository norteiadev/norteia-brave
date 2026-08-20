#!/usr/bin/env python
"""POC: o snippet de uma API de busca contratada carrega os fatos que o web_search carrega?

Esta é a pergunta em aberto da §15.3 do docs/poc/gemini-viability.md. O `web_search` da
Anthropic custa $10/1.000 E injeta 12-28 mil tokens de página no prompt (61% da conta,
§11.1). Exa/Tavily/Brave devolvem título + 2-3 linhas por resultado. Se o snippet bastar,
a cascata da §15.2 fecha; se não bastar, é preciso um segundo passo de leitura de página
e parte dos tokens volta.

O alvo a bater são os fatos que o Sonnet + web_search produziu nos três atrativos obscuros
medidos na §15.1 — atrativos reais do OSM, sem artigo na Wikipedia, que são os 95% do caso.

Mede por provedor e por atrativo: fatos encontrados, tokens do contexto, custo da query.
Sem LLM no caminho — o que se mede aqui é o insumo, não a redação.

Uso:
    # offline, prova que o casador de fatos funciona (sem key, sem rede)
    .venv/bin/python scripts/poc/search_snippets_probe.py --self-check

    # snippets (o teste da §15.3)
    TAVILY_API_KEY=tvly-... .venv/bin/python scripts/poc/search_snippets_probe.py
    .venv/bin/python scripts/poc/search_snippets_probe.py --provider exa,tavily,brave

    # segundo passo: ler a página (mede quanto token volta e quantos fatos entram)
    .venv/bin/python scripts/poc/search_snippets_probe.py --provider exa --read-pages

Keys (nenhuma no .env do projeto — todas de free tier, ver §17.3):
    TAVILY_API_KEY        1.000 créditos/mês, sem cartão
    EXA_API_KEY           $10 em créditos/mês (~2.000 buscas)
    BRAVE_SEARCH_API_KEY  $5 em créditos/mês (~1.000 buscas), cartão só para identidade
"""

from __future__ import annotations

import argparse
import os
import sys
import unicodedata

import httpx

TIMEOUT = 30.0

# ---------------------------------------------------------------------------
# O alvo: os fatos que o Sonnet + web_search trouxe na §15.1.
#
# Cada fato tem aliases; basta um casar. `generic=True` marca o fato cujo termo é comum
# demais para ser prova de nada sozinho (um snippet de turismo qualquer diz "litoral").
# Esses entram no relatório separados, para não inflar a taxa de acerto.
# ---------------------------------------------------------------------------
ATRATIVOS: list[dict] = [
    {
        "nome": "Mirante da Lagoa",
        "municipio": "Guarapari",
        "uf": "ES",
        "custo_sonnet": 0.0611,
        "fatos": [
            ("Parque Estadual Paulo César Vinha", ["paulo cesar vinha"], False),
            ("lagoa de Caraís", ["carais"], False),
            ("coloração avermelhada / matéria orgânica", ["avermelhad", "materia organica"], False),
            ('apelido "Lagoa da Coca-Cola"', ["coca-cola", "coca cola"], False),
            ("trilha em restinga", ["restinga"], False),
        ],
    },
    {
        "nome": "Mirante de Buenos Aires",
        "municipio": "Guarapari",
        "uf": "ES",
        "custo_sonnet": 0.0532,
        "fatos": [
            ("distrito de Buenos Aires", ["distrito de buenos aires"], False),
            ("Pedra do Elefante", ["pedra do elefante"], False),
            ("origem do nome", ["origem do nome", "recebeu o nome", "batizad", "deve o nome"], False),
            ("contraste montanha/litoral", ["litoral", "mar aberto"], True),
        ],
    },
    {
        "nome": "Vista Linda",
        "municipio": "Domingos Martins",
        "uf": "ES",
        "custo_sonnet": 0.1131,
        "fatos": [
            ("região de Santa Isabel", ["santa isabel"], False),
            ("ponte sobre lagoa artificial", ["lagoa artificial", "represa", "ponte"], False),
            ("serra de Domingos Martins", ["domingos martins"], True),
        ],
    },
]

# Preço por query, das páginas oficiais em 2026-08-20 (§17.3).
PRECO_POR_QUERY = {"exa": 0.005, "tavily": 0.008, "brave": 0.005}
PRECO_POR_PAGINA_LIDA = {"exa": 0.001}


def _fold(s: str) -> str:
    """Minúsculas sem acento — o snippet e o fato precisam casar apesar da grafia."""
    s = unicodedata.normalize("NFKD", s.lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def casar_fatos(contexto: str, fatos: list[tuple]) -> list[tuple[str, bool, bool]]:
    """Devolve (label, achou, generico) para cada fato alvo."""
    alvo = _fold(contexto)
    return [(label, any(_fold(a) in alvo for a in aliases), generico) for label, aliases, generico in fatos]


# ---------------------------------------------------------------------------
# Provedores. Cada um devolve (texto_do_contexto, n_resultados).
# O texto é o que iria para o prompt: título + snippet por resultado.
# ---------------------------------------------------------------------------
def buscar_tavily(query: str, key: str, ler_paginas: bool) -> tuple[str, int]:
    r = httpx.post(
        "https://api.tavily.com/search",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "query": query,
            "search_depth": "advanced" if ler_paginas else "basic",
            "max_results": 5,
            "include_raw_content": ler_paginas,
        },
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    res = r.json().get("results", [])
    partes = []
    for it in res:
        corpo = (it.get("raw_content") or it.get("content") or "")[:3000]
        partes.append(f"{it.get('title', '')}\n{corpo}")
    return "\n\n".join(partes), len(res)


def buscar_exa(query: str, key: str, ler_paginas: bool) -> tuple[str, int]:
    contents = {"text": {"maxCharacters": 3000}} if ler_paginas else {"highlights": True}
    r = httpx.post(
        "https://api.exa.ai/search",
        headers={"x-api-key": key, "content-type": "application/json"},
        json={"query": query, "numResults": 5, "type": "auto", "contents": contents},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    res = r.json().get("results", [])
    partes = []
    for it in res:
        corpo = it.get("text") or " … ".join(it.get("highlights") or [])
        partes.append(f"{it.get('title', '')}\n{corpo[:3000]}")
    return "\n\n".join(partes), len(res)


def buscar_brave(query: str, key: str, ler_paginas: bool) -> tuple[str, int]:
    if ler_paginas:
        raise SystemExit("brave: a API não lê página; use --provider exa,tavily com --read-pages")
    r = httpx.get(
        "https://api.search.brave.com/res/v1/web/search",
        headers={"X-Subscription-Token": key, "Accept": "application/json"},
        params={"q": query, "count": 5, "country": "br", "search_lang": "pt"},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    res = r.json().get("web", {}).get("results", [])
    partes = [f"{it.get('title', '')}\n{it.get('description', '')}" for it in res]
    return "\n\n".join(partes), len(res)


PROVEDORES = {
    "exa": (buscar_exa, "EXA_API_KEY"),
    "tavily": (buscar_tavily, "TAVILY_API_KEY"),
    "brave": (buscar_brave, "BRAVE_SEARCH_API_KEY"),
}


def contar_tokens(texto: str) -> tuple[int, str]:
    """Conta exato pelo tokenizer da Anthropic se a key existir; senão estima.

    A comparação é contra números que saíram do usage da Anthropic (§9.3, §11.1), então
    o tokenizer precisa ser o mesmo para a conta fechar.
    """
    key = os.environ.get("BRAVE_LLM_ANTHROPIC_API_KEY")
    if key:
        try:
            import anthropic

            n = (
                anthropic.Anthropic(api_key=key)
                .messages.count_tokens(
                    model="claude-sonnet-4-5",
                    messages=[{"role": "user", "content": texto or "."}],
                )
                .input_tokens
            )
            return n, "exato"
        except Exception as exc:  # noqa: BLE001 - a sonda não pode morrer por isso
            print(f"  (contagem exata indisponível: {exc}; caindo para estimativa)", file=sys.stderr)
    return len(texto) // 4, "estimado"


def rodar(provedores: list[str], ler_paginas: bool) -> int:
    linhas: list[str] = []
    for prov in provedores:
        fn, env = PROVEDORES[prov]
        key = os.environ.get(env)
        if not key:
            print(f"[pular] {prov}: falta {env}", file=sys.stderr)
            continue

        print(f"\n{'=' * 78}\n{prov.upper()}{'  (+ leitura de página)' if ler_paginas else '  (snippets)'}\n{'=' * 78}")
        tot_ok = tot_fatos = tot_tokens = 0
        for atr in ATRATIVOS:
            query = f"{atr['nome']} {atr['municipio']} {atr['uf']} atrativo turístico"
            try:
                contexto, n = fn(query, key, ler_paginas)
            except httpx.HTTPStatusError as exc:
                print(f"  {atr['nome']}: HTTP {exc.response.status_code} — {exc.response.text[:200]}")
                continue

            tokens, modo = contar_tokens(contexto)
            checados = casar_fatos(contexto, atr["fatos"])
            fortes = [c for c in checados if not c[2]]
            ok = sum(1 for _, achou, _ in fortes if achou)
            tot_ok += ok
            tot_fatos += len(fortes)
            tot_tokens += tokens

            print(f"\n  {atr['nome']} ({atr['municipio']}/{atr['uf']})")
            print(f"    {n} resultados · {tokens} tokens ({modo}) · Sonnet gastou ${atr['custo_sonnet']:.4f}")
            for label, achou, generico in checados:
                marca = "✓" if achou else "✗"
                sufixo = "  (termo genérico, não conta)" if generico else ""
                print(f"    {marca} {label}{sufixo}")
            print(f"    fatos fortes: {ok}/{len(fortes)}")

        if tot_fatos:
            custo = PRECO_POR_QUERY[prov] * len(ATRATIVOS)
            if ler_paginas and prov in PRECO_POR_PAGINA_LIDA:
                custo += PRECO_POR_PAGINA_LIDA[prov] * 5 * len(ATRATIVOS)
            media_tok = tot_tokens / len(ATRATIVOS)
            linhas.append(
                f"| {prov}{' + páginas' if ler_paginas else ''} | {tot_ok}/{tot_fatos} | "
                f"{media_tok:,.0f} | ${custo / len(ATRATIVOS):.4f} |"
            )

    if linhas:
        print(f"\n\n{'=' * 78}\nPLACAR — contra Sonnet + web_search: 12/12 fatos, ~11.900 tokens, $0,0758\n{'=' * 78}")
        print("\n| provedor | fatos fortes | tokens/atrativo | $/atrativo (busca) |")
        print("|---|---|---|---|")
        print("\n".join(linhas))
        print("\nA conclusão da §15.3 depende de duas coisas: a taxa de fatos e os tokens.")
        print("Snippet basta = fatos altos COM tokens uma ordem de grandeza abaixo de 11.900.")
    else:
        print("\nNenhum provedor rodou. Exporte ao menos uma key (ver docstring).", file=sys.stderr)
        return 1
    return 0


def self_check() -> int:
    """Prova que o casador de fatos acerta e erra pelos motivos certos. Offline."""
    atr = ATRATIVOS[0]
    bom = (
        "Mirante da Lagoa fica no Parque Estadual Paulo Cesar Vinha, sobre a lagoa de Carais, "
        "cuja agua avermelhada pela materia organica rendeu o apelido de Lagoa da Coca-Cola. "
        "A trilha atravessa restinga preservada."
    )
    achados = [label for label, achou, _ in casar_fatos(bom, atr["fatos"]) if achou]
    assert len(achados) == 5, achados

    # Acento e caixa não podem quebrar o casamento.
    assert casar_fatos("PARQUE ESTADUAL PAULO CÉSAR VINHA", atr["fatos"])[0][1]

    # Um snippet genérico de turismo não pode marcar fato forte nenhum.
    ruim = "Conheça as belezas de Guarapari, um dos destinos mais procurados do litoral capixaba."
    fortes = [label for label, achou, gen in casar_fatos(ruim, atr["fatos"]) if achou and not gen]
    assert fortes == [], fortes

    # O fato genérico do atrativo 2 casa em texto vazio de conteúdo — por isso não conta.
    _, achou, generico = casar_fatos("passeio no litoral", ATRATIVOS[1]["fatos"])[3]
    assert achou and generico

    print("self-check ok: casador de fatos acerta os 5 fatos, ignora snippet genérico,")
    print("é imune a acento/caixa, e o fato genérico está corretamente marcado.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--provider", default="exa,tavily,brave", help="lista separada por vírgula")
    ap.add_argument("--read-pages", action="store_true", help="segundo passo: puxa o texto da página")
    ap.add_argument("--self-check", action="store_true", help="valida o casador de fatos, offline")
    args = ap.parse_args()

    if args.self_check:
        return self_check()

    provedores = [p.strip() for p in args.provider.split(",") if p.strip()]
    desconhecidos = [p for p in provedores if p not in PROVEDORES]
    if desconhecidos:
        raise SystemExit(f"provedor desconhecido: {desconhecidos}; use {list(PROVEDORES)}")
    return rodar(provedores, args.read_pages)


if __name__ == "__main__":
    raise SystemExit(main())
