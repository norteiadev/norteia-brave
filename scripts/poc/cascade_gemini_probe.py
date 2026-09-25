#!/usr/bin/env python
"""POC §26: a cascata da §25 com Gemini 2.5 Flash no lugar do Haiku 4.5.

Mesma pergunta da §25 (custo, prazo, gates), trocando só o redator. Roda o CÓDIGO DA LANE:
``TourismCopywriter.write_cascade`` sem alteração. Dois enxertos, ambos fora da lane:

  1. a busca vem de um cache em disco — a Tavily roda UMA vez por atrativo (``--buscar``) e
     todos os modelos recebem byte a byte o mesmo contexto. Sem isso a comparação mediria a
     deriva do índice da Tavily (§23.2: 9/10 → 4/10 em 19 dias), não o modelo;
  2. ``generate()`` é um adaptador: Gemini via OpenRouter (a API direta do Google responde
     404 "no longer available to new users" para esta conta), Haiku via ``RealLLMClient``.

A amostra são os atrativos da lane TA de ``cascade_timed_probe.sample.json`` (sem Cadastur),
limitados pelo que sobra de créditos no free tier da Tavily.

Uso:
    .venv/bin/python scripts/poc/cascade_gemini_probe.py --self-check          # offline
    set -a; . ./.env; set +a
    .venv/bin/python scripts/poc/cascade_gemini_probe.py --buscar --n 140      # 280 créditos
    .venv/bin/python scripts/poc/cascade_gemini_probe.py --rodar --modelo google/gemini-2.5-flash
    .venv/bin/python scripts/poc/cascade_gemini_probe.py --rodar --modelo google/gemini-2.5-flash --thinking
    .venv/bin/python scripts/poc/cascade_gemini_probe.py --rodar --modelo claude-haiku-4-5 --sequenciais 0
"""

from __future__ import annotations

import argparse
import asyncio
import contextvars
import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from cascade_timed_probe import AMOSTRA, USD_TAVILY, pct  # noqa: E402

AQUI = Path(__file__).parent
CACHE = AQUI / "cascade_gemini_probe.contexts.json"
OPENROUTER = "https://openrouter.ai/api/v1/chat/completions"
PRECOS = {  # USD/MTok (entrada, saída incl. thinking) — listas oficiais em 2026-09-14
    "google/gemini-2.5-flash": (0.30, 2.50),
    "claude-haiku-4-5": (1.0, 5.0),
}

_REC: contextvars.ContextVar[dict] = contextvars.ContextVar("rec")
STATUS: Counter = Counter()


def amostra_ta(n: int) -> list[dict]:
    return [a for a in json.loads(AMOSTRA.read_text()) if a["origem"] != "cadastur"][:n]


# ---------------------------------------------------------------------------
# Busca: uma vez, em disco
# ---------------------------------------------------------------------------
async def buscar(n: int, conc: int) -> int:
    import httpx

    from brave.clients.tavily import RealTavilyClient
    from brave.config.settings import AppConfig
    from brave.domains.places.copywriter import cascade_queries

    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    tav = RealTavilyClient(AppConfig().tavily_api_key, http_client=httpx.AsyncClient(timeout=30.0))
    sem = asyncio.Semaphore(conc)
    tempos: list[float] = []

    async def um(a: dict) -> None:
        qs = cascade_queries(a["nome"], a["municipio"], a["uf"])
        if all(q in cache for q in qs):
            return
        async with sem:
            t = time.perf_counter()
            try:
                res = await asyncio.gather(*(tav.search(q) for q in qs))
            except Exception as exc:  # noqa: BLE001
                print("falha", a["nome"], type(exc).__name__, flush=True)
                return
            tempos.append(time.perf_counter() - t)
            cache.update(zip(qs, res, strict=True))

    await asyncio.gather(*(um(a) for a in amostra_ta(n)))
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1))
    print(f"{len(cache)} queries em cache; busca p50 {pct(tempos, 50):.2f}s p95 {pct(tempos, 95):.2f}s")
    return 0


class BuscaEmCache:
    def __init__(self) -> None:
        self._c = json.loads(CACHE.read_text())

    async def search(self, q: str) -> str:
        return self._c[q]  # KeyError → write_cascade degrada para CascadeResult(None): vira "falha"


# ---------------------------------------------------------------------------
# Redatores
# ---------------------------------------------------------------------------
class GeminiOpenRouter:
    """``generate()`` com a forma do RealLLMClient: max_tokens 2048, sem tools, texto puro."""

    def __init__(self, thinking: bool, reasoning: dict | None = None) -> None:
        import httpx

        self._http = httpx.AsyncClient(timeout=180.0)
        self._key = os.environ["BRAVE_LLM_OPENROUTER_API_KEY"]
        self._thinking = thinking
        self._reasoning = reasoning  # sobrepõe ``thinking`` — DeepSeek raciocina por padrão (§28)

    async def generate(self, messages, model, *, system=None, tools=None) -> str:  # noqa: ANN001, ARG002
        body = {
            "model": model,
            "max_tokens": 2048,
            "messages": [{"role": "system", "content": system}, *messages],
            "provider": {"data_collection": "deny"},
            "usage": {"include": True},
        }
        if self._reasoning is not None:
            body["reasoning"] = self._reasoning
        elif self._thinking:
            body["reasoning"] = {"enabled": True}
        rec = _REC.get()
        t = time.perf_counter()
        for tentativa in range(4):  # 429/5xx: backoff curto, como o tenacity dos clients
            r = await self._http.post(
                OPENROUTER, headers={"Authorization": f"Bearer {self._key}"}, json=body
            )
            STATUS[r.status_code] += 1
            if r.status_code == 200 and "choices" in r.json():
                break
            await asyncio.sleep(2 * 2**tentativa)
        r.raise_for_status()
        d = r.json()
        u = d["usage"]
        rec["t_llm"] = time.perf_counter() - t
        rec["tok_in"] = u["prompt_tokens"]
        rec["tok_out"] = u["completion_tokens"]
        rec["tok_thinking"] = (u.get("completion_tokens_details") or {}).get("reasoning_tokens", 0)
        rec["usd_llm"] = u.get("cost")
        rec["finish"] = d["choices"][0].get("finish_reason")
        return d["choices"][0]["message"]["content"] or ""


def haiku():
    from anthropic import AsyncAnthropic

    from brave.clients.llm import RealLLMClient
    from brave.config.settings import AppConfig

    app = AppConfig()
    llm = RealLLMClient(config=app.llm)
    llm._anthropic_client = AsyncAnthropic(api_key=app.llm.anthropic_api_key)
    create = llm._anthropic_client.messages.create

    async def medido(**kw):  # noqa: ANN003, ANN202
        t = time.perf_counter()
        r = await create(**kw)
        STATUS[200] += 1
        rec = _REC.get()
        rec["t_llm"] = time.perf_counter() - t
        rec["tok_in"], rec["tok_out"] = r.usage.input_tokens, r.usage.output_tokens
        rec["usd_llm"] = (r.usage.input_tokens * 1.0 + r.usage.output_tokens * 5.0) / 1e6
        rec["finish"] = r.stop_reason
        return r

    llm._anthropic_client.messages.create = medido
    return llm


# ---------------------------------------------------------------------------
# Obediência ao prompt (a lane grava a saída direto na coluna)
# ---------------------------------------------------------------------------
_MARKDOWN = re.compile(r"(\*\*|^#|^\s*[-*] )", re.M)
_OPERACIONAL = re.compile(r"(R\$\s?\d|\b\d{1,2}h\d{0,2}\b|\(\d{2}\)\s?\d{4}|\bhor[áa]rio de funcionamento\b)", re.I)
_POR_EXTENSO = re.compile(r"\b(mil (novecentos|oitocentos|setecentos|seiscentos|quinhentos)|cento e \w+)\b", re.I)


def obediencia(texto: str) -> list[str]:
    v = []
    if _MARKDOWN.search(texto):
        v.append("markdown")
    if _OPERACIONAL.search(texto):
        v.append("operacional")
    if _POR_EXTENSO.search(texto):
        v.append("numero_por_extenso")
    return v


# ---------------------------------------------------------------------------
async def rodar(modelo: str, thinking: bool, n: int, n_seq: int, conc: int) -> int:
    from brave.domains.places.copywriter import TourismCopywriter

    llm = haiku() if modelo.startswith("claude") else GeminiOpenRouter(thinking)
    cw = TourismCopywriter(llm, modelo, search_client=BuscaEmCache())
    itens = amostra_ta(n)

    async def um(a: dict, fase: str) -> dict:
        rec = {"nome": a["nome"], "uf": a["uf"], "origem": a["origem"], "fase": fase}
        _REC.set(rec)
        t = time.perf_counter()
        out = await cw.write_cascade(a["nome"], a["municipio"], a["uf"], {})
        rec["t_total"] = time.perf_counter() - t
        rec["resultado"] = "ok" if out.prose else (out.motivo or "falha")
        rec["groundedness"] = out.groundedness
        texto = out.prose or out.rascunho or ""
        rec["chars"] = len(texto)
        rec["violacoes"] = obediencia(texto)
        rec["texto"] = texto
        return rec

    async def fase(xs: list[dict], nome: str, c: int) -> tuple[list[dict], float]:
        sem = asyncio.Semaphore(c)

        async def g(a: dict) -> dict:
            async with sem:
                return await um(a, nome)

        t = time.perf_counter()
        return list(await asyncio.gather(*(g(a) for a in xs))), time.perf_counter() - t

    recs, walls = [], {}
    if n_seq:
        r, w = await fase(itens[:n_seq], "sequencial", 1)
        recs += r
        walls["sequencial"] = (len(r), 1, w)
    r, w = await fase(itens[n_seq:], "concorrente", conc)
    recs += r
    walls["concorrente"] = (len(r), conc, w)

    rel = relatorio(recs, walls, modelo)
    rel["thinking"] = thinking
    rel["http_status"] = dict(STATUS)
    slug = modelo.split("/")[-1] + ("-thinking" if thinking else "")
    (AQUI / f"cascade_gemini_probe.{slug}.json").write_text(
        json.dumps({"relatorio": rel, "atrativos": recs}, ensure_ascii=False, indent=2)
    )
    print(json.dumps(rel, ensure_ascii=False, indent=2))
    return 0


def relatorio(recs: list[dict], walls: dict, modelo: str) -> dict:
    escritos = [r for r in recs if "tok_in" in r]
    g = [r["groundedness"] for r in recs if r.get("groundedness") is not None]
    usd_llm = sum(r.get("usd_llm") or 0 for r in escritos)
    usd_busca = 2 * len(recs) * USD_TAVILY  # 2 buscas por atrativo, inclusive os barrados
    fases = {
        f: {
            "n": n,
            "concorrencia": c,
            "wall_s": round(w, 1),
            "llm_atrativos_hora": round(n / w * 3600),
            "llm_p50_s": round(pct([r["t_llm"] for r in recs if r["fase"] == f and "t_llm" in r], 50), 2),
            "llm_p95_s": round(pct([r["t_llm"] for r in recs if r["fase"] == f and "t_llm" in r], 95), 2),
        }
        for f, (n, c, w) in walls.items()
    }
    return {
        "modelo": modelo,
        "n": len(recs),
        "resultados": dict(Counter(r["resultado"] for r in recs)),
        "groundedness_media": round(sum(g) / len(g), 3) if g else None,
        "violacoes": dict(Counter(v for r in recs for v in r.get("violacoes", []))),
        "chars_p50": pct([r["chars"] for r in recs if r["chars"]], 50),
        "finish": dict(Counter(r.get("finish") for r in escritos)),
        "fases": fases,
        "tokens": {
            "chamadas": len(escritos),
            "in_medio": round(sum(r["tok_in"] for r in escritos) / max(len(escritos), 1)),
            "out_medio": round(sum(r["tok_out"] for r in escritos) / max(len(escritos), 1)),
            "thinking_medio": round(sum(r.get("tok_thinking", 0) for r in escritos) / max(len(escritos), 1)),
        },
        "custo": {
            "llm_usd": round(usd_llm, 4),
            "llm_por_escrito_usd": round(usd_llm / max(len(escritos), 1), 5),
            "busca_usd_paygo": round(usd_busca, 2),
            "por_atrativo_usd": round((usd_llm + usd_busca) / len(recs), 5),
            "dez_mil_usd": round((usd_llm + usd_busca) / len(recs) * 10_000),
        },
    }


def self_check() -> int:
    assert obediencia("**Título**\ntexto") == ["markdown"]
    assert obediencia("Entrada R$ 20, aberto das 8h às 17h") == ["operacional"]
    assert obediencia("fundado em mil novecentos e oitenta") == ["numero_por_extenso"]
    assert obediencia("A cachoeira tem 144 metros e fica a 12 km da sede.") == []
    recs = [
        {"resultado": "ok", "fase": "s", "groundedness": 1.0, "chars": 800, "tok_in": 3000,
         "tok_out": 500, "usd_llm": 0.002, "t_llm": 2.0, "violacoes": []},
        {"resultado": "sem_mencao", "fase": "s", "groundedness": None, "chars": 0, "violacoes": []},
    ]
    rel = relatorio(recs, {"s": (2, 1, 4.0)}, "x")
    assert rel["custo"]["llm_por_escrito_usd"] == 0.002
    assert rel["custo"]["por_atrativo_usd"] == round((0.002 + 4 * USD_TAVILY) / 2, 5)
    assert rel["fases"]["s"]["llm_atrativos_hora"] == 1800
    print("self-check ok: obediência e custo do relatório.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-check", action="store_true")
    ap.add_argument("--buscar", action="store_true")
    ap.add_argument("--rodar", action="store_true")
    ap.add_argument("--modelo", default="google/gemini-2.5-flash")
    ap.add_argument("--thinking", action="store_true")
    ap.add_argument("--n", type=int, default=140)
    ap.add_argument("--sequenciais", type=int, default=40)
    ap.add_argument("--concorrencia", type=int, default=8)
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    if a.buscar:
        return asyncio.run(buscar(a.n, conc=2))
    if a.rodar:
        return asyncio.run(rodar(a.modelo, a.thinking, a.n, a.sequenciais, a.concorrencia))
    ap.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
