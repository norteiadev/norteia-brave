#!/usr/bin/env python
"""POC §29: Search API da Parallel chamada direto, com as 2 queries fixas da lane.

Pergunta: substitui o DeepSeek + Parallel do OpenRouter (§28), com o mesmo resultado, sem os
~45 s do modelo pesquisador e mais barato?

Pipeline por atrativo, mesmos 140 da §26–§28:
  1. busca: ``POST https://api.parallel.ai/v1/search`` com ``search_queries =
     cascade_queries(nome, municipio, uf)`` numa requisição só, ``objective`` curto em PT-BR e
     ``mode`` (fast | turbo | basic). Cache em disco por modo: a busca roda uma vez;
  2. contexto do redator = excerpts brutos com ``[título] url`` por resultado (sem resumo de LLM);
  3. redação: ``write_cascade`` sem alteração, Gemini 2.5 Flash sem thinking, ``max_tokens`` 2048,
     e ``finish_reason != stop`` rejeitado (vira falha, como a lane deve fazer — §26.4/§28.3).

Uso:
    .venv/bin/python scripts/poc/parallel_direto_probe.py --self-check
    set -a; . ./.env; set +a
    .venv/bin/python scripts/poc/parallel_direto_probe.py --buscar --modo fast
    .venv/bin/python scripts/poc/parallel_direto_probe.py --escrever --modo fast
    .venv/bin/python scripts/poc/parallel_direto_probe.py --relatorio
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from cascade_gemini_probe import _REC, GeminiOpenRouter, amostra_ta, obediencia  # noqa: E402
from fontes_abertas_probe import FonteFixa, limpar, riqueza  # noqa: E402

from brave.lanes.atrativos.copywriter import cascade_queries  # noqa: E402
from brave.lanes.atrativos.grounding import menciona  # noqa: E402

AQUI = Path(__file__).parent
URL = "https://api.parallel.ai/v1/search"
GEMINI = "google/gemini-2.5-flash"
# USD por requisição com 10 resultados, e por resultado adicional (docs.parallel.ai/getting-started/pricing, 2026-09-14)
PRECO = {"turbo": 0.001, "fast": 0.001, "basic": 0.005, "advanced": 0.005}
PRECO_EXTRA = 0.001
COTA_GRATIS_MES = 5_000  # "Run up to 5,000 requests per month for free" / "$5 in free credits per month"


def cache(modo: str) -> Path:
    return AQUI / f"parallel_direto_probe.busca.{modo}.json"


def saida(modo: str) -> Path:
    return AQUI / f"parallel_direto_probe.{modo}.json"


def pct(xs: list[float], p: int) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p / 100))] if xs else 0.0


# ---------------------------------------------------------------------------
# Busca
# ---------------------------------------------------------------------------
def objetivo(a: dict) -> str:
    local = "/".join(x for x in (a["municipio"], a["uf"]) if x)
    return (
        f"Fatos verificáveis sobre o atrativo turístico {a['nome']}"
        + (f" em {local}" if local else "")
        + ": história, características, o que ver."
    )


def montar_contexto(results: list[dict]) -> str:
    return "\n\n".join(
        f"[{r.get('title') or ''}] {r.get('url') or ''}\n" + "\n".join(r.get("excerpts") or [])
        for r in results
    )


def chave() -> str:
    return os.environ.get("BRAVE_PARALLEL_API_KEY") or os.environ["PARALLEL_API_KEY"]


def custo(modo: str, n_results: int) -> float:
    return PRECO[modo] + PRECO_EXTRA * max(0, n_results - 10)


async def buscar_um(http: httpx.AsyncClient, a: dict, modo: str) -> dict:
    body = {"objective": objetivo(a), "search_queries": cascade_queries(a["nome"], a["municipio"], a["uf"]), "mode": modo}
    status: list[int] = []
    t = time.perf_counter()
    for tentativa in range(5):
        try:
            r = await http.post(URL, headers={"x-api-key": chave()}, json=body)
        except httpx.HTTPError as exc:
            status.append(0)
            erro = type(exc).__name__
        else:
            status.append(r.status_code)
            if r.status_code == 200:
                break
            erro = f"HTTP {r.status_code}: {r.text[:200]}"
            if r.status_code not in (429, 500, 502, 503, 504):
                return {"erro": erro, "status": status}
        espera = 2 * 2**tentativa
        if status[-1] == 429:  # honra retry-after (§25: a parede de taxa da Tavily derrubava tudo)
            espera = max(espera, float(r.headers.get("retry-after") or 0))
        await asyncio.sleep(espera)
    else:
        return {"erro": erro, "status": status}
    t_total = time.perf_counter() - t
    d = r.json()
    results = d.get("results") or []
    bruto = montar_contexto(results)
    return {
        "t": t_total,  # inclui retentativas
        "status": status,
        "search_id": d.get("search_id"),
        "n_results": len(results),
        "urls": [x.get("url") for x in results],
        "chars_excerpt": sum(len(e) for x in results for e in (x.get("excerpts") or [])),
        "bruto": bruto,
        "usage": d.get("usage"),
        "warnings": d.get("warnings"),
        "usd": custo(modo, len(results)),
    }


async def buscar(modo: str, conc: int) -> int:
    arq = cache(modo)
    feito = json.loads(arq.read_text()) if arq.exists() else {}
    sem = asyncio.Semaphore(conc)
    async with httpx.AsyncClient(timeout=60.0) as http:

        async def g(a: dict) -> None:
            if "erro" not in feito.get(a["nome"], {"erro": 1}):
                return
            async with sem:
                p = await buscar_um(http, a, modo)
            feito[a["nome"]] = p
            print(len(feito), a["nome"], p.get("n_results"), round(p.get("t", 0), 2), p.get("status"), p.get("erro", ""), flush=True)

        await asyncio.gather(*(g(a) for a in amostra_ta(140)))
    arq.write_text(json.dumps(feito, ensure_ascii=False, indent=1))
    ok = [v for v in feito.values() if "erro" not in v]
    print(f"{len(ok)}/{len(feito)} ok; p50 {pct([v['t'] for v in ok], 50):.2f}s p95 {pct([v['t'] for v in ok], 95):.2f}s; "
          f"usage exemplo: {ok[0].get('usage') if ok else None}")
    return 0


# ---------------------------------------------------------------------------
# Redação
# ---------------------------------------------------------------------------
class GeminiEstrito(GeminiOpenRouter):
    """Rejeita ``finish_reason != stop``: o texto cortado vira falha, não aprovado (§26.4)."""

    async def generate(self, *a, **kw):  # noqa: ANN002, ANN003, ANN202
        txt = await super().generate(*a, **kw)
        if _REC.get().get("finish") != "stop":
            raise RuntimeError(f"finish_reason={_REC.get().get('finish')}")
        return txt


async def escrever(modo: str, conc: int) -> int:
    from brave.lanes.atrativos.copywriter import TourismCopywriter

    busca = json.loads(cache(modo).read_text())
    llm = GeminiEstrito(thinking=False)
    sem = asyncio.Semaphore(conc)
    recs: list[dict] = []

    async def um(a: dict) -> None:
        ctx = busca.get(a["nome"], {}).get("bruto", "")
        rec = {"nome": a["nome"], "menciona": bool(ctx) and menciona(ctx, a["nome"])}
        async with sem:
            _REC.set(rec)
            cw = TourismCopywriter(llm, GEMINI, search_client=FonteFixa(ctx))
            out = await cw.write_cascade(a["nome"], a["municipio"], a["uf"], {})
        rec["resultado"] = "ok" if out.prose else (out.motivo or "falha")
        rec["groundedness"] = out.groundedness
        rec["texto"] = out.prose or out.rascunho or ""
        rec["riqueza"] = riqueza(out.prose, ctx) if out.prose else 0
        rec["violacoes"] = obediencia(rec["texto"])
        rec["meta"] = bool(_META.search(rec["texto"]))
        recs.append(rec)

    await asyncio.gather(*(um(a) for a in amostra_ta(140)))
    saida(modo).write_text(json.dumps({"registros": recs}, ensure_ascii=False, indent=2))
    return relatorio_cli()


# Candidato a recado ao operador. Dá falso positivo ("não há registro de…" dentro da prosa):
# o relatório só marca, a contagem final é lendo.
_META = re.compile(r"(as fontes|o contexto|informações fornecidas|não (há|encontrei|foi possível)|desculpe|registro)", re.I)


# ---------------------------------------------------------------------------
# Relatório pareado
# ---------------------------------------------------------------------------
def relatorio(modo: str, busca: dict, recs: list[dict], itens: list[dict], tav: dict, ds: dict,
              abertas: dict, subst: set[str]) -> dict:
    n, k = len(itens), 10_000 / len(itens)
    idx = {r["nome"]: r for r in recs}
    ok_busca = [busca[a["nome"]] for a in itens if "erro" not in busca.get(a["nome"], {"erro": 1})]
    ok = [idx[a["nome"]] for a in itens if idx[a["nome"]]["resultado"] == "ok"]
    usd_busca = sum(busca.get(a["nome"], {}).get("usd", 0) for a in itens)
    usd_red = sum(idx[a["nome"]].get("usd_llm") or 0 for a in itens)

    def pareado(outro: dict) -> dict:
        pares = [(idx[x]["riqueza"], outro[x]["riqueza"]) for x in idx
                 if idx[x]["resultado"] == "ok" and outro.get(x, {}).get("resultado") == "ok"]
        m = len(pares) or 1
        a, b = sum(p for p, _ in pares) / m, sum(q for _, q in pares) / m
        return {"n": len(pares), "parallel_direto": round(a, 2), "outro": round(b, 2),
                "razao": round(a / b, 3) if b else None,
                "parallel_mais_rico": sum(p > q for p, q in pares), "outro_mais_rico": sum(q > p for p, q in pares)}

    # Cascata §27: abertas (Gemini) resolvem quando aprovadas e com fonte textual citando; o resto vem daqui.
    aberto = [a["nome"] for a in itens if a["nome"] in subst and abertas[a["nome"]]["resultado"] == "ok"]
    resto = [a["nome"] for a in itens if a["nome"] not in aberto]
    cas_busca = sum(busca.get(x, {}).get("usd", 0) for x in resto)
    cas_red = sum(abertas[a["nome"]].get("usd_llm") or 0 for a in itens) + sum(idx[x].get("usd_llm") or 0 for x in resto)
    cas_ok = len(aberto) + sum(idx[x]["resultado"] == "ok" for x in resto)

    def com_cota(reqs_10k: float, usd_busca_10k: float) -> float:  # 10 mil num único mês
        return usd_busca_10k * max(0.0, reqs_10k - COTA_GRATIS_MES) / reqs_10k if reqs_10k else 0.0

    busca_10k, red_10k = usd_busca * k, usd_red * k
    cas_busca_10k, cas_red_10k = cas_busca * k, cas_red * k
    return {
        "modo": modo,
        "n": n,
        "busca": {
            "ok": len(ok_busca),
            "http_status": dict(Counter(s for p in busca.values() for s in p.get("status", []))),
            "t_p50_s": round(pct([p["t"] for p in ok_busca], 50), 2),
            "t_p95_s": round(pct([p["t"] for p in ok_busca], 95), 2),
            "resultados_media": round(sum(p["n_results"] for p in ok_busca) / max(len(ok_busca), 1), 1),
            "chars_excerpt_p50": pct([p["chars_excerpt"] for p in ok_busca], 50),
            "contexto_chars_p50": pct([len(p["bruto"]) for p in ok_busca], 50),
            "usage_exemplo": ok_busca[0].get("usage") if ok_busca else None,
            "usd_por_atrativo": round(usd_busca / n, 5),
        },
        "redacao": {
            "cita_atrativo": sum(r["menciona"] for r in recs),
            "resultados": dict(Counter(r["resultado"] for r in recs)),
            "finish": dict(Counter(r.get("finish") for r in recs if "finish" in r)),
            "riqueza_media": round(sum(r["riqueza"] for r in ok) / max(len(ok), 1), 2),
            "groundedness_media": round(sum(r["groundedness"] for r in ok) / max(len(ok), 1), 3),
            "violacoes_aprovados": dict(Counter(v for r in ok for v in r["violacoes"])),
            "meta_candidatos_aprovados": [r["nome"] for r in ok if r["meta"]],
            "usd_por_atrativo": round(usd_red / n, 5),
        },
        "pareado_riqueza": {"vs_tavily_s26": pareado(tav), "vs_deepseek_parallel_s28": pareado(ds)},
        "custo": {
            "usd_por_atrativo": round((usd_busca + usd_red) / n, 5),
            "dez_mil_usd": round(busca_10k + red_10k, 1),
            "dez_mil_usd_com_cota": round(com_cota(10_000, busca_10k) + red_10k, 1),
            "cascata_abertas": {
                "resolvidos_abertas": len(aberto),
                "aprovados": cas_ok,
                "dez_mil_usd": round(cas_busca_10k + cas_red_10k, 1),
                "dez_mil_usd_com_cota": round(com_cota(len(resto) * k, cas_busca_10k) + cas_red_10k, 1),
            },
        },
    }


def relatorio_cli() -> int:
    itens = amostra_ta(140)
    fontes, _ = limpar(json.loads((AQUI / "fontes_abertas_probe.fontes.json").read_text()))
    reg27 = json.loads((AQUI / "fontes_abertas_probe.json").read_text())["registros"]
    tav = {r["nome"]: r for r in reg27 if r["variante"] == "tavily"}
    abertas = {r["nome"]: r for r in reg27 if r["variante"] == "abertas"}
    ds = {r["nome"]: r for r in json.loads((AQUI / "deepseek_busca_probe.json").read_text())["registros"]
          if r["fonte"] == "parallel" and r["redator"] == GEMINI}
    subst = {a["nome"] for a in itens if any(
        (fontes.get(a["nome"], {}).get(c) or {}).get("texto") and menciona(fontes[a["nome"]][c]["texto"], a["nome"])
        for c in ["wikipedia", "wikivoyage_pt", "wikivoyage_en", "wikidata"])}
    todos = {}
    for modo in PRECO:
        if not saida(modo).exists():
            continue
        busca = json.loads(cache(modo).read_text())
        doc = json.loads(saida(modo).read_text())
        doc["relatorio"] = relatorio(modo, busca, doc["registros"], itens, tav, ds, abertas, subst)
        saida(modo).write_text(json.dumps(doc, ensure_ascii=False, indent=2))
        todos[modo] = doc["relatorio"]
    print(json.dumps(todos, ensure_ascii=False, indent=2))
    return 0


def self_check() -> int:
    ctx = montar_contexto([{"title": "T", "url": "https://a", "excerpts": ["e1", "e2"]}, {"url": "https://b", "excerpts": []}])
    assert ctx == "[T] https://a\ne1\ne2\n\n[] https://b\n", ctx
    assert custo("fast", 10) == 0.001 and custo("basic", 12) == 0.007
    assert "Mucugê/BA" in objetivo({"nome": "X", "municipio": "Mucugê", "uf": "BA"})
    itens = [{"nome": "a"}, {"nome": "b"}]
    busca = {"a": {"t": 1.0, "status": [200], "n_results": 10, "chars_excerpt": 5, "bruto": "x", "usd": 0.001},
             "b": {"t": 3.0, "status": [429, 200], "n_results": 10, "chars_excerpt": 5, "bruto": "y", "usd": 0.001}}
    recs = [{"nome": "a", "menciona": True, "resultado": "ok", "riqueza": 8, "groundedness": 1.0, "violacoes": [], "meta": False, "usd_llm": 0.002, "finish": "stop"},
            {"nome": "b", "menciona": True, "resultado": "nao_fundamentada", "riqueza": 0, "groundedness": 0.5, "violacoes": [], "meta": False, "usd_llm": 0.002, "finish": "stop"}]
    outro = {"a": {"resultado": "ok", "riqueza": 4}, "b": {"resultado": "ok", "riqueza": 9}}
    abertas = {"a": {"resultado": "ok", "usd_llm": 0.001}, "b": {"resultado": "sem_mencao"}}
    rel = relatorio("fast", busca, recs, itens, outro, outro, abertas, subst={"a"})
    assert rel["pareado_riqueza"]["vs_tavily_s26"] == {"n": 1, "parallel_direto": 8.0, "outro": 4.0, "razao": 2.0, "parallel_mais_rico": 1, "outro_mais_rico": 0}
    assert rel["custo"]["usd_por_atrativo"] == 0.003
    assert rel["busca"]["http_status"] == {200: 2, 429: 1}
    c = rel["custo"]["cascata_abertas"]
    assert c["resolvidos_abertas"] == 1 and c["aprovados"] == 1
    # cascata: abertas redigem os 2 ($0,001) + busca e redação só de "b" ($0,003) → $0,002/atrativo
    assert c["dez_mil_usd"] == 20.0, c
    # 5 mil buscas no resto cabem na cota → só a redação
    assert c["dez_mil_usd_com_cota"] == 15.0, c
    print("self-check ok: contexto, custo, pareamento e cascata.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-check", action="store_true")
    ap.add_argument("--buscar", action="store_true")
    ap.add_argument("--escrever", action="store_true")
    ap.add_argument("--relatorio", action="store_true")
    ap.add_argument("--modo", choices=list(PRECO), default="fast")
    ap.add_argument("--concorrencia", type=int, default=8)
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    if a.buscar:
        return asyncio.run(buscar(a.modo, a.concorrencia))
    if a.escrever:
        return asyncio.run(escrever(a.modo, a.concorrencia))
    if a.relatorio:
        return relatorio_cli()
    ap.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
