#!/usr/bin/env python
"""POC: a cascata Tavily serve para os ~10 mil atrativos do TripAdvisor?

A §23 mediu 3 atrativos escolhidos por serem os mais difíceis possíveis: vindos do OSM,
sem artigo na Wikipedia. Não é a distribuição do trabalho. **Atrativo do TripAdvisor tem
página no TripAdvisor por definição** — a pergunta é se isso muda a resposta.

Amostra: `docs/poc/pilot-100/atrativos.json` — 100 atrativos REAIS já processados pela rota
de produção (§21), com as `queries` que ela emitiu e as `fontes` que ela citou. Comparar
provedor com a MESMA query elimina o confundidor "a query era ruim".

Três medidas, e nenhuma precisa de lista de fatos escrita à mão (por isso escala):

  1. COBERTURA — o contexto que a Tavily devolve menciona o atrativo pelo nome? Contexto
     que só fala do município é o insumo exato que produziu 2/2 fabricações na §23.4.
  2. RECUPERAÇÃO DA EVIDÊNCIA — as URLs da Tavily batem com as `fontes` que a rota de
     produção citou? Mesma pergunta, provedores diferentes, evidência comparável.
  3. RISCO DE FABRICAÇÃO EM ESCALA — a fração da carga que cairia no modelo com contexto
     sem menção ao atrativo. É a projeção direta para os 10 mil.

Uso:
    .venv/bin/python scripts/poc/cascade_scale_probe.py --self-check   # offline, sem key
    set -a; . ./.env; set +a
    .venv/bin/python scripts/poc/cascade_scale_probe.py --n 50
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import unicodedata
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from search_snippets_probe import buscar_tavily  # noqa: E402

AMOSTRA = Path(__file__).resolve().parents[2] / "docs/poc/pilot-100/atrativos.json"
SAIDA = Path(__file__).with_name("cascade_scale_probe.json")
PRECO_QUERY_TAVILY = 0.008

# Palavras que sozinhas não identificam o atrativo — "Praia" bate em qualquer texto de
# litoral. Sem isto a cobertura sai inflada: o contexto genérico do município marcaria
# presença por conter "praia", que é o oposto do que a medida quer detectar.
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
    """As palavras do nome que de fato identificam o atrativo.

    "Praia Da Costa" → ["costa"]. "Convento da Penha" → ["penha"]. Se sobrar nada
    (nome inteiro genérico, ex. "Centro Histórico"), devolve o nome inteiro dobrado —
    aí só casa quem escrever a expressão completa.
    """
    palavras = [p for p in _fold(nome).replace("-", " ").split() if len(p) > 2]
    fortes = [p for p in palavras if p not in GENERICAS]
    return fortes or [_fold(nome)]


def menciona(contexto: str, nome: str) -> bool:
    """O contexto fala DESTE atrativo, não só do município dele."""
    alvo = _fold(contexto)
    return all(t in alvo for t in termos_identificadores(nome))


def dominios(urls: list[str]) -> set[str]:
    out = set()
    for u in urls:
        try:
            h = urlparse(u).netloc.lower().removeprefix("www.")
        except ValueError:
            continue
        if h:
            out.add(h)
    return out


def urls_do_contexto(contexto: str) -> list[str]:
    return [ln.strip() for ln in contexto.splitlines() if ln.strip().startswith("http")]


def afirmacoes_concretas(texto: str) -> list[str]:
    """As afirmações verificáveis do texto: números, medidas e nomes próprios compostos.

    É o que dá para checar contra o contexto sem ter gabarito escrito à mão — e é
    exatamente a classe que a §23.4 viu ser inventada ("50 metros de queda").
    """
    import re

    fora = []
    fora += re.findall(r"\b\d{3,4}\b", texto)  # anos, altitudes
    # findall com UM grupo devolve strings, não tuplas — indexar aqui pegaria o primeiro
    # caractere ("5" em vez de "50 metros") e deixaria toda medida passar por infundada.
    fora += re.findall(r"\b\d+[,.]?\d*\s?(?:m|km|metros|quilômetros|hectares)\b", texto, re.I)
    cap = r"[A-ZÁÂÃÉÊÍÓÔÕÚÇ][a-zà-ú]+"
    con = r"(?:d[aeo]s?)"
    fora += re.findall(rf"\b{cap}(?:\s(?:{con}\s)?{cap})+\b", texto)
    return fora


def groundedness(texto: str, contexto: str) -> tuple[int, int, list[str]]:
    """Quantas afirmações concretas do texto existem no contexto que o alimentou.

    Afirmação que não está no contexto veio da memória paramétrica ou da invenção — e
    nada no pipeline distingue as duas. Devolve (fundamentadas, total, as soltas).
    """
    alvo = _fold(contexto)
    claims = afirmacoes_concretas(texto)
    soltas = [c for c in claims if _fold(c) not in alvo]
    return len(claims) - len(soltas), len(claims), soltas


async def escrever(contexto: str, nome: str, municipio: str, uf: str) -> str:
    """flash-lite gratuito com o prompt de produção, sem ferramenta. A cascata inteira."""
    import httpx

    from brave.lanes.atrativos.copywriter import COPYWRITER_SYSTEM, _build_context

    base = _build_context(nome, municipio, uf, {})
    cabeca = base.rpartition("\n")[0]
    user = (
        f"{cabeca}\n\nFONTES ENCONTRADAS NA WEB (use apenas estas):\n{contexto}\n\n"
        "Escreva a descrição editorial da Norteia para este atrativo, baseada apenas nas "
        "fontes acima. Se as fontes não trouxerem informação suficiente sobre este atrativo "
        "específico, escreva uma descrição sensorial mais curta, sem afirmações factuais "
        "específicas."
    )
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-3.5-flash-lite:generateContent"
    )
    async with httpx.AsyncClient(timeout=90) as h:
        r = await h.post(
            url,
            headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]},
            json={
                "system_instruction": {"parts": [{"text": COPYWRITER_SYSTEM}]},
                "contents": [{"parts": [{"text": user}]}],
                "generationConfig": {"maxOutputTokens": 1024},
            },
        )
        r.raise_for_status()
        cand = r.json()["candidates"][0]
        return "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []))


def rodar(n: int, seed: int, escreve: bool = False) -> int:
    key = os.environ.get("TAVILY_API_KEY")
    if not key:
        raise SystemExit("falta TAVILY_API_KEY (set -a; . ./.env; set +a)")

    todos = json.loads(AMOSTRA.read_text())
    # Só os que a rota de produção conseguiu processar — são os que têm evidência para
    # comparar. Os 11 sem `fontes` não têm baseline, e incluí-los mediria outra coisa.
    elegiveis = [a for a in todos if a.get("fontes")]
    random.Random(seed).shuffle(elegiveis)
    amostra = elegiveis[:n]

    # A URL precisa entrar no contexto: é ela que a lane grava em `fontes`, e o slug
    # carrega fato (§22.4). Sem isso a comparação de evidência é impossível.
    import search_snippets_probe as sonda

    sonda.INCLUIR_URL = True

    linhas = []
    for i, a in enumerate(amostra, 1):
        queries = a.get("queries") or [f"{a['nome']} {a['municipio']} {a['uf']} atrativo turístico"]
        try:
            pedacos = [buscar_tavily(q, key, False)[0] for q in queries[:2]]
        except Exception as exc:  # noqa: BLE001
            print(f"  [{i}] {a['nome']}: ERRO {type(exc).__name__}: {str(exc)[:100]}")
            continue
        ctx = "\n\n".join(pedacos)
        prod = dominios(a["fontes"])
        tav = dominios(urls_do_contexto(ctx))
        linha = {
            "nome": a["nome"],
            "municipio": a["municipio"],
            "uf": a["uf"],
            "queries": len(queries[:2]),
            "tokens": len(ctx) // 4,
            "menciona": menciona(ctx, a["nome"]),
            "dominios_producao": sorted(prod),
            "dominios_tavily": sorted(tav),
            "overlap": sorted(prod & tav),
        }
        if escreve:
            import asyncio

            try:
                txt = asyncio.run(escrever(ctx, a["nome"], a["municipio"], a["uf"]))
            except Exception as exc:  # noqa: BLE001
                txt = ""
                print(f"       escrita falhou: {type(exc).__name__}: {str(exc)[:90]}")
            if txt:
                ok, tot, soltas = groundedness(txt, ctx)
                linha["texto"] = txt
                linha["grounded"] = ok
                linha["claims"] = tot
                linha["soltas"] = soltas

        linhas.append(linha)
        marca = "✓" if linha["menciona"] else "✗"
        ov = f"{len(linha['overlap'])}/{len(prod)}" if prod else "—"
        extra = ""
        if "claims" in linha:
            extra = f" · fundamentadas {linha['grounded']}/{linha['claims']}"
        print(
            f"  [{i:3}] {marca} {a['nome'][:38]:40} {a['municipio'][:16]:18} "
            f"{linha['tokens']:5} tok · fontes {ov}{extra}"
        )

    if not linhas:
        print("nada rodou", file=sys.stderr)
        return 1

    SAIDA.write_text(json.dumps(linhas, ensure_ascii=False, indent=2))
    n_ok = sum(1 for x in linhas if x["menciona"])
    com_ov = sum(1 for x in linhas if x["overlap"])
    tot_tok = sum(x["tokens"] for x in linhas)
    q = sum(x["queries"] for x in linhas)
    custo_busca = q * PRECO_QUERY_TAVILY / len(linhas)

    print(f"\n{'=' * 78}\nPLACAR — {len(linhas)} atrativos reais do TripAdvisor\n{'=' * 78}")
    print(
        f"  cobertura (contexto menciona o atrativo)  {n_ok}/{len(linhas)} = {n_ok / len(linhas):.0%}"
    )
    print(
        f"  recupera ao menos 1 domínio da produção   {com_ov}/{len(linhas)} = {com_ov / len(linhas):.0%}"
    )
    print(f"  tokens/atrativo                           {tot_tok / len(linhas):,.0f}")
    print(f"  $/atrativo (só busca)                     ${custo_busca:.4f}")
    print(
        f"\n  SEM MENÇÃO = risco de fabricação: {len(linhas) - n_ok}/{len(linhas)} "
        f"= {1 - n_ok / len(linhas):.0%} da carga"
    )
    print(
        f"  projetado em 10.000 atrativos:    {round((1 - n_ok / len(linhas)) * 10000):,} "
        f"descrições sobre contexto que não fala do atrativo"
    )
    com_texto = [x for x in linhas if x.get("claims")]
    if com_texto:
        g = sum(x["grounded"] for x in com_texto)
        c = sum(x["claims"] for x in com_texto)
        print("\n  REDAÇÃO (flash-lite grátis, prompt de produção, sem tool)")
        print(f"  afirmações concretas fundamentadas no contexto  {g}/{c} = {g / c:.0%}")
        piores = sorted(com_texto, key=lambda x: x["grounded"] - x["claims"])[:5]
        print("  os 5 textos com mais afirmação solta:")
        for x in piores:
            n_soltas = x["claims"] - x["grounded"]
            if not n_soltas:
                continue
            print(
                f"    {x['nome'][:34]:36} {n_soltas:2} soltas de {x['claims']:2} · "
                f"{', '.join(x['soltas'][:4])}"
            )

    print(f"\n  detalhe por atrativo em {SAIDA}")
    return 0


def self_check() -> int:
    """Prova que a medida de menção não infla nem deprecia. Offline."""
    # Termo genérico não pode marcar presença: contexto do município citando "praia"
    # não é contexto sobre a Praia da Costa.
    assert termos_identificadores("Praia Da Costa") == ["costa"]
    assert not menciona("As praias de Vila Velha atraem visitantes o ano todo.", "Praia Da Costa")
    assert menciona("O calçadão da Praia da Costa em Vila Velha", "Praia Da Costa")

    # Acento e caixa não podem quebrar.
    assert menciona("CONVENTO DA PENHA, em Vila Velha", "Convento da Penha")
    assert termos_identificadores("Convento da Penha") == ["penha"]

    # Nome todo genérico cai no fallback da expressão inteira — mais estrito, de
    # propósito: se nenhuma palavra identifica sozinha, exige a expressão completa.
    assert termos_identificadores("Centro Histórico") == ["centro historico"]
    assert not menciona("o centro da cidade e seus prédios", "Centro Histórico")
    assert menciona("passeio pelo centro histórico", "Centro Histórico")
    assert termos_identificadores("Praia Grande") == ["praia grande"]

    # Nome com dois termos fortes exige os dois — meia-menção não conta.
    assert not menciona("a Pedra Azul fica na região", "Pedra do Elefante")

    # Domínios: normaliza www e ignora lixo.
    assert dominios(["https://www.a.com/x", "https://a.com/y", "http://b.org"]) == {
        "a.com",
        "b.org",
    }
    assert urls_do_contexto("Titulo\nhttps://a.com/x\ncorpo") == ["https://a.com/x"]

    # Groundedness: a afirmação que não está no contexto tem que aparecer como solta.
    ctx = "A cachoeira tem 80 metros e fica na Serra do Caparao."
    ok, tot, soltas = groundedness("Com 80 metros de queda, na Serra do Caparao.", ctx)
    assert (ok, tot) == (2, 2), (ok, tot, soltas)
    ok, tot, soltas = groundedness("Tem 50 metros e fica na Serra do Mar.", ctx)
    assert "50 metros" in soltas and "Serra do Mar" in soltas, soltas
    assert ok < tot
    assert groundedness("um lugar bonito para visitar", ctx) == (0, 0, [])

    print("self-check ok: menção exige termo identificador (não genérico), é imune a")
    print("acento/caixa, exige TODOS os termos fortes, e os domínios normalizam www.")
    print("groundedness separa afirmação presente no contexto de afirmação solta.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--n", type=int, default=50, help="quantos atrativos (2 queries cada)")
    ap.add_argument("--seed", type=int, default=20260908)
    ap.add_argument(
        "--write",
        action="store_true",
        help="roda o flash-lite sobre o contexto e mede groundedness",
    )
    ap.add_argument("--self-check", action="store_true")
    args = ap.parse_args()

    if args.self_check:
        return self_check()
    return rodar(args.n, args.seed, args.write)


if __name__ == "__main__":
    raise SystemExit(main())
