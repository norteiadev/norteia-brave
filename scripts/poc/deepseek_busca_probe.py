#!/usr/bin/env python
"""POC §28: DeepSeek V4 Flash 0731 como pesquisador (e como redator) no lugar da Tavily.

Objetivo 1 — DeepSeek só busca, Gemini 2.5 Flash escreve.
Objetivo 2 — DeepSeek busca e escreve.

A busca nativa da API da DeepSeek (Responses API, ``tools=[{"type": "web_search"}]``) foi
testada antes desta sonda e NÃO executa: a tool é ecoada e ignorada, e o modelo cita URLs
inventadas (§28.1). O caminho medido é o do pedido original: DeepSeek via OpenRouter com a
server tool ``openrouter:web_search`` — o modelo decide as queries, o OpenRouter executa no
motor escolhido (Exa ou Parallel) e cobra por busca.

Pipeline por atrativo, mesmos 140 da §26/§27:
  1. pesquisa: DeepSeek + openrouter:web_search, ``max_tool_calls: 2`` (paridade com as 2
     queries da Tavily), 5 resultados por busca;
  2. o CONTEXTO do redator é o texto bruto das buscas (``annotations[].url_citation.content``),
     não o resumo do DeepSeek — o resumo pode trazer memória do modelo, e o gate de
     groundedness contra um resumo inventado lavaria a invenção. A taxa com que o resumo
     afirma o que as buscas não trouxeram é medida à parte;
  3. redação: ``write_cascade`` sem alteração, com Gemini 2.5 Flash e com DeepSeek 0731.

Uso:
    .venv/bin/python scripts/poc/deepseek_busca_probe.py --self-check
    set -a; . ./.env; set +a
    .venv/bin/python scripts/poc/deepseek_busca_probe.py --pesquisar --motor exa
    .venv/bin/python scripts/poc/deepseek_busca_probe.py --pesquisar --motor parallel
    .venv/bin/python scripts/poc/deepseek_busca_probe.py --escrever
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
from cascade_gemini_probe import _REC, OPENROUTER, GeminiOpenRouter, amostra_ta, obediencia  # noqa: E402
from fontes_abertas_probe import FonteFixa, contexto, limpar, riqueza  # noqa: E402

from brave.lanes.atrativos.grounding import groundedness_ratio, menciona  # noqa: E402

AQUI = Path(__file__).parent
DEEPSEEK = "deepseek/deepseek-v4-flash-0731"
GEMINI = "google/gemini-2.5-flash"
MOTORES = {  # parâmetros da server tool + preço por busca (docs OpenRouter, 2026-09-14)
    "exa": ({"engine": "exa"}, 0.007),
    "parallel": ({"engine": "parallel", "mode": "fast"}, 0.001),
}
PESQUISA_SYSTEM = (
    "Você é um pesquisador de turismo. Faça no máximo 2 buscas na web sobre o atrativo indicado "
    "— o atrativo exato, naquele município — e depois liste os fatos verificáveis que as buscas "
    "trouxeram (história, datas, medidas, características, o que há para ver), cada um com a URL "
    "da fonte. Use apenas o que as buscas trouxeram. Se as buscas não falarem deste atrativo, "
    "responda apenas NAO_ENCONTRADO."
)


def cache(motor: str) -> Path:
    return AQUI / f"deepseek_busca_probe.pesquisa.{motor}.json"


# ---------------------------------------------------------------------------
# Pesquisa
# ---------------------------------------------------------------------------
async def pesquisar_um(http: httpx.AsyncClient, a: dict, motor: str) -> dict:
    params, _ = MOTORES[motor]
    local = ", ".join(x for x in (a["municipio"], a["uf"]) if x)
    body = {
        "model": DEEPSEEK,
        "messages": [
            {"role": "system", "content": PESQUISA_SYSTEM},
            {"role": "user", "content": f"Atrativo: {a['nome']}" + (f" — {local}" if local else "")},
        ],
        "tools": [{"type": "openrouter:web_search", "parameters": {**params, "max_uses": 2, "max_results": 5}}],
        "max_tool_calls": 2,
        "max_tokens": 8000,
        "provider": {"data_collection": "deny"},
        "usage": {"include": True},
    }
    t = time.perf_counter()
    for tentativa in range(4):
        r = await http.post(OPENROUTER, headers={"Authorization": f"Bearer {os.environ['BRAVE_LLM_OPENROUTER_API_KEY']}"}, json=body)
        if r.status_code == 200 and "choices" in r.json():
            break
        await asyncio.sleep(3 * 2**tentativa)
    d = r.json()
    if "choices" not in d:
        return {"erro": f"HTTP {r.status_code}: {r.text[:200]}"}
    m, u = d["choices"][0]["message"], d["usage"]
    anns = [x["url_citation"] for x in (m.get("annotations") or []) if x.get("type") == "url_citation"]
    vistos, blocos = set(), []
    for x in anns:  # a mesma página volta citada várias vezes
        chave = (x.get("url"), (x.get("content") or "")[:80])
        if x.get("content") and chave not in vistos:
            vistos.add(chave)
            blocos.append(f"[{x.get('title', '')}] {x.get('url', '')}\n{x['content']}")
    ci = (u.get("cost_details") or {}).get("upstream_inference_cost") or 0
    return {
        "dossie": m.get("content") or "",
        "bruto": "\n\n".join(blocos),
        "urls": sorted({x.get("url", "") for x in anns}),
        "buscas": (u.get("server_tool_use_details") or {}).get("web_search_requests", 0),
        "usd_total": u.get("cost"),
        "usd_modelo": ci,
        "tok_in": u.get("prompt_tokens"),
        "tok_out": u.get("completion_tokens"),
        "t": time.perf_counter() - t,
        "finish": d["choices"][0].get("finish_reason"),
    }


async def pesquisar(motor: str, conc: int) -> int:
    arq = cache(motor)
    feito = json.loads(arq.read_text()) if arq.exists() else {}
    sem = asyncio.Semaphore(conc)
    async with httpx.AsyncClient(timeout=400.0) as http:

        async def g(a: dict) -> None:
            if a["nome"] in feito and "erro" not in feito[a["nome"]]:
                return
            async with sem:
                feito[a["nome"]] = await pesquisar_um(http, a, motor)
                p = feito[a["nome"]]
                print(len(feito), a["nome"], p.get("buscas"), p.get("usd_total"), round(p.get("t", 0)), p.get("erro", ""), flush=True)

        await asyncio.gather(*(g(a) for a in amostra_ta(140)))
    arq.write_text(json.dumps(feito, ensure_ascii=False, indent=1))
    return 0


# ---------------------------------------------------------------------------
# Redação
# ---------------------------------------------------------------------------
_URL = re.compile(r"https?://[^\s)\]>]+")


def urls_fora(dossie: str, urls: list[str]) -> int:
    """URLs citadas no resumo que nenhuma busca devolveu — fonte vinda da memória do modelo."""
    base = {u.rstrip("/") for u in urls}
    return sum(1 for u in {x.rstrip("/.,") for x in _URL.findall(dossie)} if u.rstrip("/") not in base)


async def escrever(conc: int, so_deepseek: bool = False) -> int:
    """``so_deepseek`` refaz só o redator DeepSeek e reaproveita o Gemini já gravado.

    Primeira rodada: DeepSeek com raciocínio padrão → 182 de 184 falhas com
    ``finish_reason: length`` (o raciocínio come os 2.048 tokens da lane) e 21 textos
    aprovados cortados. Refeito com ``reasoning: {"enabled": false}`` (§28.3).
    """
    from brave.lanes.atrativos.copywriter import TourismCopywriter

    itens = amostra_ta(140)
    fontes, _ = limpar(json.loads((AQUI / "fontes_abertas_probe.fontes.json").read_text()))
    ab = [r for r in json.loads((AQUI / "fontes_abertas_probe.json").read_text())["registros"] if r["variante"] == "abertas"]
    sem = asyncio.Semaphore(conc)
    llms = {GEMINI: GeminiOpenRouter(thinking=False), DEEPSEEK: GeminiOpenRouter(thinking=False, reasoning={"enabled": False})}
    saida = AQUI / "deepseek_busca_probe.json"
    recs: list[dict] = []
    if so_deepseek:
        recs = [r for r in json.loads(saida.read_text())["registros"] if r["redator"] != DEEPSEEK]

    async def um(a: dict, fonte: str, ctx: str, redator: str) -> None:
        if so_deepseek and redator != DEEPSEEK:
            return
        rec = {"nome": a["nome"], "fonte": fonte, "redator": redator, "menciona": bool(ctx) and menciona(ctx, a["nome"])}
        async with sem:
            _REC.set(rec)
            cw = TourismCopywriter(llms[redator], redator, search_client=FonteFixa(ctx))
            out = await cw.write_cascade(a["nome"], a["municipio"], a["uf"], {})
        rec["resultado"] = "ok" if out.prose else (out.motivo or "falha")
        rec["texto"] = out.prose or out.rascunho or ""
        rec["riqueza"] = riqueza(out.prose, ctx) if out.prose else 0
        rec["violacoes"] = obediencia(rec["texto"])
        recs.append(rec)

    tarefas = []
    for motor in MOTORES:
        pesq = json.loads(cache(motor).read_text())
        for a in itens:
            ctx = pesq.get(a["nome"], {}).get("bruto", "")
            tarefas += [um(a, motor, ctx, GEMINI), um(a, motor, ctx, DEEPSEEK)]
    # Objetivo 2 dentro da cascata da §27: o DeepSeek também escreve sobre as fontes abertas.
    for a in itens:
        tarefas.append(um(a, "abertas", contexto(fontes.get(a["nome"], {}), ["wikipedia", "wikivoyage_pt", "wikivoyage_en", "wikidata", "osm"]), DEEPSEEK))
    await asyncio.gather(*tarefas)
    if not so_deepseek:
        for r in ab:  # o Gemini sobre as abertas já foi medido na §27
            recs.append({**r, "fonte": "abertas", "redator": GEMINI})

    rel = relatorio(recs, itens, fontes)
    saida.write_text(json.dumps({"relatorio": rel, "registros": recs}, ensure_ascii=False, indent=2))
    print(json.dumps(rel, ensure_ascii=False, indent=2))
    return 0


def relatorio(recs: list[dict], itens: list[dict], fontes: dict) -> dict:
    n = len(itens)
    k = 10_000 / n
    idx = {(r["fonte"], r["redator"], r["nome"]): r for r in recs}
    tav = {r["nome"]: r for r in json.loads((AQUI / "fontes_abertas_probe.json").read_text())["registros"] if r["variante"] == "tavily"}

    def subst(nome: str) -> bool:  # regra da §27.4: fonte textual aberta citando o atrativo
        f = fontes.get(nome, {})
        return any((f.get(c) or {}).get("texto") and menciona(f[c]["texto"], nome) for c in ["wikipedia", "wikivoyage_pt", "wikivoyage_en", "wikidata"])

    out: dict = {"n": n, "motores": {}}
    for motor, (_, preco) in MOTORES.items():
        pesq = json.loads(cache(motor).read_text())
        ps = [pesq[a["nome"]] for a in itens if "erro" not in pesq.get(a["nome"], {"erro": 1})]
        busca_usd = sum(p["usd_total"] or 0 for p in ps)
        m = {
            "pesquisas_ok": len(ps),
            "buscas_media": round(sum(p["buscas"] for p in ps) / len(ps), 2),
            "usd_pesquisa_por_atrativo": round(busca_usd / len(ps), 5),
            "usd_taxa_busca_por_atrativo": round(sum((p["usd_total"] or 0) - p["usd_modelo"] for p in ps) / len(ps), 5),
            "usd_modelo_pesquisa_por_atrativo": round(sum(p["usd_modelo"] for p in ps) / len(ps), 5),
            "t_pesquisa_p50_s": round(sorted(p["t"] for p in ps)[len(ps) // 2], 1),
            "contexto_bruto_chars_p50": sorted(len(p["bruto"]) for p in ps)[len(ps) // 2],
            "resumo_nao_encontrado": sum("NAO_ENCONTRADO" in p["dossie"] for p in ps),
            "resumo_groundedness_media": round(sum(groundedness_ratio(p["dossie"], p["bruto"]) for p in ps) / len(ps), 3),
            "resumo_com_url_inventada": sum(urls_fora(p["dossie"], p["urls"]) > 0 for p in ps),
            "redatores": {},
        }
        for red in (GEMINI, DEEPSEEK):
            rs = [idx[(motor, red, a["nome"])] for a in itens]
            ok = [r for r in rs if r["resultado"] == "ok"]
            usd_red = sum(r.get("usd_llm") or 0 for r in rs)
            por_atr = (busca_usd + usd_red) / n
            # Cascata §27: abertas (mesmo redator) primeiro, este motor no resto.
            abr = {a["nome"]: idx[("abertas", red, a["nome"])] for a in itens}
            aberto = [a["nome"] for a in itens if abr[a["nome"]]["resultado"] == "ok" and subst(a["nome"])]
            resto = [a["nome"] for a in itens if a["nome"] not in aberto]
            cas_usd = sum(r.get("usd_llm") or 0 for r in abr.values()) + sum(
                (pesq[x].get("usd_total") or 0) + (idx[(motor, red, x)].get("usd_llm") or 0) for x in resto
            )
            cas_ok = len(aberto) + sum(idx[(motor, red, x)]["resultado"] == "ok" for x in resto)
            m["redatores"][red] = {
                "cita_atrativo": sum(r["menciona"] for r in rs),
                "resultados": dict(Counter(r["resultado"] for r in rs)),
                "riqueza_media": round(sum(r["riqueza"] for r in ok) / max(len(ok), 1), 2),
                "violacoes": dict(Counter(v for r in ok for v in r["violacoes"])),
                "usd_redacao_por_atrativo": round(usd_red / n, 5),
                "usd_por_atrativo": round(por_atr, 5),
                "dez_mil_usd": round(por_atr * 1e4),
                "cascata_abertas": {"resolvidos_abertas": len(aberto), "aprovados": cas_ok, "dez_mil_usd": round(cas_usd * k)},
            }
        # pareado com a Tavily (Gemini nos dois)
        pares = [(idx[(motor, GEMINI, a["nome"])]["riqueza"], tav[a["nome"]]["riqueza"]) for a in itens
                 if idx[(motor, GEMINI, a["nome"])]["resultado"] == "ok" and tav[a["nome"]]["resultado"] == "ok"]
        m["pareado_gemini_vs_tavily"] = {"n": len(pares), "riqueza_motor": round(sum(x for x, _ in pares) / max(len(pares), 1), 2),
                                         "riqueza_tavily": round(sum(y for _, y in pares) / max(len(pares), 1), 2)}
        out["motores"][motor] = m
    return out


def self_check() -> int:
    assert urls_fora("Fonte: https://a.com/x e https://b.com/y.", ["https://a.com/x/"]) == 1
    assert urls_fora("sem url", []) == 0
    print("self-check ok: URLs inventadas no resumo.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-check", action="store_true")
    ap.add_argument("--pesquisar", action="store_true")
    ap.add_argument("--motor", choices=list(MOTORES), default="exa")
    ap.add_argument("--escrever", action="store_true")
    ap.add_argument("--so-deepseek", action="store_true", help="refaz só o redator DeepSeek")
    ap.add_argument("--concorrencia", type=int, default=8)
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    if a.pesquisar:
        return asyncio.run(pesquisar(a.motor, a.concorrencia))
    if a.escrever:
        return asyncio.run(escrever(a.concorrencia, a.so_deepseek))
    ap.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
