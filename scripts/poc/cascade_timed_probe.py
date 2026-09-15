#!/usr/bin/env python
"""POC §25: os 200 cronometrados — a cascata cabe em semanas?

A §24 mediu qualidade e custo. Falta throughput: quantos atrativos por hora a cascata
inteira (Tavily → gate → Haiku) processa, onde está o gargalo, e o que isso diz sobre os
~10 mil do TripAdvisor.

Roda o CÓDIGO DA LANE, não uma réplica: RealTavilyClient + TourismCopywriter.write_cascade
+ RealLLMClient. O único enxerto é instrumentação — hooks httpx que contam cada resposta
HTTP por provedor (inclusive as que o retry do SDK/tenacity esconde) e um wrapper que lê o
`usage` da Anthropic para o custo real. Retry fica no padrão de produção.

Duas fases sobre 200 atrativos distintos:
  1. sequencial (concorrência 1) — latência limpa por atrativo, sem disputa;
  2. concorrente (--concorrencia, padrão 8) — throughput e comportamento sob rate limit.

Amostra: os 100 do piloto + o que o banco tem além deles + o snapshot de imagens (tudo da
lane TA, 130) + os 30 do fixture oa30 do TA. O banco não tem 200 (a sessão TA pede
bootstrap manual para varrer mais); os últimos 40 são parques do Cadastur, marcados
`cadastur` — relatados em separado.

Uso:
    .venv/bin/python scripts/poc/cascade_timed_probe.py --self-check   # offline
    set -a; . ./.env; set +a
    .venv/bin/python scripts/poc/cascade_timed_probe.py --amostra        # grava a amostra (precisa do banco)
    .venv/bin/python scripts/poc/cascade_timed_probe.py --rodar          # os 200, ~400 créditos Tavily
"""

from __future__ import annotations

import argparse
import asyncio
import contextvars
import json
import os
import random
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

RAIZ = Path(__file__).resolve().parents[2]
PILOTO = RAIZ / "docs/poc/pilot-100/atrativos.json"
IMAGENS = RAIZ / "scripts/poc_images/out/atrativos_images.json"
FIXTURE_TA = RAIZ / "tests/fixtures/tripadvisor/attractions_oa30.html"
AMOSTRA = Path(__file__).with_name("cascade_timed_probe.sample.json")
SAIDA = Path(__file__).with_name("cascade_timed_probe.json")
SEED = 20260910
HAIKU_IN, HAIKU_OUT = 1.0, 5.0  # USD/MTok
USD_TAVILY = 0.008


def _chave(nome: str, uf: str) -> str:
    """Só o nome, dobrado: o fixture do TA não tem UF, e "Museu do Amanha" (fixture) é o
    mesmo "Museu Do Amanhã" (piloto, RJ)."""
    from brave.lanes.atrativos.grounding import _fold

    return " ".join(_fold(nome).split())


def montar_amostra(n: int = 200) -> list[dict]:
    """TA primeiro (piloto → banco → snapshot), MTur só para completar."""
    import sqlalchemy as sa

    out: list[dict] = []
    vistos: set[str] = set()

    def add(nome: str, municipio: str, uf: str, origem: str) -> None:
        k = _chave(nome, uf)
        if nome and k not in vistos and len(out) < n:
            vistos.add(k)
            out.append(
                {"nome": nome, "municipio": municipio or "", "uf": uf or "", "origem": origem}
            )

    for a in json.loads(PILOTO.read_text()):
        add(a["nome"], a["municipio"], a["uf"], "ta-piloto")
    eng = sa.create_engine(os.environ["BRAVE_DB_URL"])
    with eng.connect() as c:
        rows = c.execute(
            sa.text(
                "select normalized->>'name', normalized->>'municipio', "
                "coalesce(uf, normalized->>'uf') from rio_records "
                "where entity_type='attraction' and routing <> 'descarte' order by canonical_key"
            )
        ).all()
    for nome, mun, uf in rows:
        add(nome or "", mun or "", uf or "", "ta-banco")
    for a in json.loads(IMAGENS.read_text()):
        add(a["nome"], a.get("municipio") or "", a["uf"], "ta-snapshot")

    # O fixture oa30 é uma página real de listagem do TA (Brasil inteiro): nome sem município.
    from brave.domains.tripadvisor.client import TripAdvisorClient

    html = FIXTURE_TA.read_text(encoding="utf-8")
    for card in TripAdvisorClient._parse_attractions_page(
        TripAdvisorClient._extract_sections_from_html(html)
    ):
        add(card["name"], "", "", "ta-fixture")

    # Resto: parques do Cadastur (lazer/temáticos) — estabelecimentos turísticos reais, com
    # município. Não é a distribuição do TA; é a cauda "nome de empresa" que a Nascente também
    # arrasta. Relatado em separado.
    with eng.connect() as c:
        parques = c.execute(
            sa.text(
                "select trade_name, municipio, uf from local_businesses "
                "where cadastur_dataset in ('cadastur-05','cadastur-10') "
                "and trade_name ~ '[A-Za-z]{3}' order by md5(trade_name || municipio)"
            )
        ).all()
    for nome, mun, uf in parques:
        add(nome.title(), mun or "", uf or "", "cadastur")
    # Embaralha DEPOIS de escolher: as duas fases recebem a mesma mistura de origens, senão
    # a sequencial seria só piloto e a concorrente só MTur, e a latência não compararia.
    random.Random(SEED).shuffle(out)
    return out


# ---------------------------------------------------------------------------
# Instrumentação
# ---------------------------------------------------------------------------
_REC: contextvars.ContextVar[dict] = contextvars.ContextVar("rec")
STATUS: dict[str, Counter] = {"tavily": Counter(), "anthropic": Counter()}
LIMITES: dict[str, dict[str, str]] = {"tavily": {}, "anthropic": {}}


def _hook(provedor: str):
    async def on_response(resp) -> None:  # noqa: ANN001
        STATUS[provedor][resp.status_code] += 1
        for h, v in resp.headers.items():
            if "ratelimit" in h.lower() or h.lower() == "retry-after":
                LIMITES[provedor][h.lower()] = v

    return on_response


def construir():
    import httpx
    from anthropic import AsyncAnthropic, DefaultAsyncHttpxClient

    from brave.clients.llm import RealLLMClient
    from brave.clients.tavily import RealTavilyClient
    from brave.config.settings import AppConfig
    from brave.lanes.atrativos.copywriter import CASCADE_MODEL, TourismCopywriter

    app = AppConfig()
    llm = RealLLMClient(config=app.llm)  # sem redis/session: o cost guard não é o objeto aqui
    llm._anthropic_client = AsyncAnthropic(
        api_key=app.llm.anthropic_api_key,
        http_client=DefaultAsyncHttpxClient(event_hooks={"response": [_hook("anthropic")]}),
    )
    create = llm._anthropic_client.messages.create

    async def create_medido(**kw):  # noqa: ANN003, ANN202
        t = time.perf_counter()
        r = await create(**kw)
        rec = _REC.get()
        rec["t_llm"] = time.perf_counter() - t
        rec["tok_in"] = r.usage.input_tokens
        rec["tok_out"] = r.usage.output_tokens
        return r

    llm._anthropic_client.messages.create = create_medido

    search = RealTavilyClient(
        app.tavily_api_key,
        http_client=httpx.AsyncClient(timeout=30.0, event_hooks={"response": [_hook("tavily")]}),
    )
    buscar = search.search

    async def buscar_medido(q: str) -> str:
        t = time.perf_counter()
        try:
            return await buscar(q)
        finally:
            rec = _REC.get()
            rec["t_busca"] = max(rec.get("t_busca", 0.0), time.perf_counter() - t)
            rec["buscas"] = rec.get("buscas", 0) + 1

    search.search = buscar_medido
    return TourismCopywriter(llm, CASCADE_MODEL, search_client=search)


async def um(cw, a: dict, fase: str) -> dict:  # noqa: ANN001
    rec = {"nome": a["nome"], "uf": a["uf"], "origem": a["origem"], "fase": fase}
    _REC.set(rec)
    t = time.perf_counter()
    try:
        out = await cw.write_cascade(a["nome"], a["municipio"], a["uf"], {})
        rec["resultado"] = "ok" if out.prose else (out.motivo or "falha")
        rec["groundedness"] = out.groundedness
    except Exception as exc:  # noqa: BLE001
        rec["resultado"] = f"erro:{type(exc).__name__}"
    rec["t_total"] = time.perf_counter() - t
    return rec


async def fase(cw, itens: list[dict], nome: str, conc: int) -> tuple[list[dict], float]:  # noqa: ANN001
    sem = asyncio.Semaphore(conc)

    async def guardado(a: dict) -> dict:
        async with sem:
            return await um(cw, a, nome)

    t = time.perf_counter()
    # Cada tarefa roda num contexto copiado — o _REC de uma não vaza para a outra.
    recs = await asyncio.gather(*(guardado(a) for a in itens))
    return list(recs), time.perf_counter() - t


def pct(xs: list[float], p: int) -> float:
    if len(xs) < 2:
        return xs[0] if xs else 0.0
    return statistics.quantiles(xs, n=100, method="inclusive")[p - 1]


def relatorio(recs: list[dict], walls: dict[str, tuple[int, int, float]]) -> dict:
    res = Counter(r["resultado"] for r in recs)
    tin = sum(r.get("tok_in", 0) for r in recs)
    tout = sum(r.get("tok_out", 0) for r in recs)
    creditos = STATUS["tavily"][200]
    custo_llm = (tin * HAIKU_IN + tout * HAIKU_OUT) / 1e6
    custo_busca = creditos * USD_TAVILY
    fases = {}
    for f, (n, conc, wall) in walls.items():
        rs = [r for r in recs if r["fase"] == f]
        tt = [r["t_total"] for r in rs]
        tb = [r["t_busca"] for r in rs if "t_busca" in r]
        tl = [r["t_llm"] for r in rs if "t_llm" in r]
        fases[f] = {
            "n": n,
            "concorrencia": conc,
            "wall_s": round(wall, 1),
            "atrativos_hora": round(n / wall * 3600),
            "p50_s": round(pct(tt, 50), 2),
            "p95_s": round(pct(tt, 95), 2),
            "busca_p50_s": round(pct(tb, 50), 2),
            "busca_p95_s": round(pct(tb, 95), 2),
            "llm_p50_s": round(pct(tl, 50), 2),
            "llm_p95_s": round(pct(tl, 95), 2),
        }
    por_origem = {}
    for o in sorted({r["origem"] for r in recs}):
        rs = [r for r in recs if r["origem"] == o]
        por_origem[o] = dict(Counter(r["resultado"] for r in rs)) | {"n": len(rs)}
    return {
        "resultados": dict(res),
        "por_origem": por_origem,
        "fases": fases,
        "http_status": {p: dict(c) for p, c in STATUS.items()},
        "limites_headers": LIMITES,
        "tokens": {"in": tin, "out": tout, "chamadas_llm": sum(1 for r in recs if "tok_in" in r)},
        "custo": {
            "tavily_creditos": creditos,
            "tavily_usd": round(custo_busca, 4),
            "haiku_usd": round(custo_llm, 4),
            "total_usd": round(custo_busca + custo_llm, 4),
            "por_atrativo_usd": round((custo_busca + custo_llm) / len(recs), 5),
        },
    }


async def rodar(conc: int, n_seq: int, inicio: int = 0, saida: Path = SAIDA) -> int:
    """``inicio`` pula os primeiros da amostra — refazer só uma fase sem repetir buscas."""
    amostra = json.loads(AMOSTRA.read_text())[inicio:]
    cw = construir()
    walls: dict[str, tuple[int, int, float]] = {}
    recs: list[dict] = []
    seq, par = amostra[:n_seq], amostra[n_seq:]
    if seq:
        print(f"fase 1: {len(seq)} sequenciais…", flush=True)
        r1, w1 = await fase(cw, seq, "sequencial", 1)
        walls["sequencial"] = (len(seq), 1, w1)
        recs += r1
    print(f"fase 2: {len(par)} com concorrência {conc}…", flush=True)
    r2, w2 = await fase(cw, par, "concorrente", conc)
    walls["concorrente"] = (len(par), conc, w2)
    recs += r2
    rel = relatorio(recs, walls)
    saida.write_text(
        json.dumps({"relatorio": rel, "atrativos": recs}, ensure_ascii=False, indent=2)
    )
    print(json.dumps(rel, ensure_ascii=False, indent=2))
    return 0


def self_check() -> int:
    assert pct([1.0, 2.0, 3.0, 4.0], 50) == 2.5
    rel = relatorio(
        [
            {
                "resultado": "ok",
                "fase": "s",
                "origem": "x",
                "t_total": 2.0,
                "t_busca": 1.0,
                "t_llm": 1.0,
                "tok_in": 1000,
                "tok_out": 200,
            },
            {"resultado": "sem_mencao", "fase": "s", "origem": "x", "t_total": 1.0, "t_busca": 1.0},
        ],
        {"s": (2, 1, 3.0)},
    )
    assert rel["fases"]["s"]["atrativos_hora"] == 2400
    assert rel["tokens"]["chamadas_llm"] == 1
    assert rel["custo"]["haiku_usd"] == 0.002
    print("self-check ok: percentis e custo do relatório.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--self-check", action="store_true")
    ap.add_argument(
        "--amostra", action="store_true", help="monta e grava os 200 (precisa do banco)"
    )
    ap.add_argument("--rodar", action="store_true")
    ap.add_argument("--concorrencia", type=int, default=8)
    ap.add_argument("--sequenciais", type=int, default=50)
    ap.add_argument("--inicio", type=int, default=0, help="pula os N primeiros da amostra")
    ap.add_argument("--saida", type=Path, default=SAIDA)
    args = ap.parse_args()
    if args.self_check:
        return self_check()
    if args.amostra:
        amostra = montar_amostra()
        AMOSTRA.write_text(json.dumps(amostra, ensure_ascii=False, indent=2))
        print(len(amostra), Counter(a["origem"] for a in amostra))
        return 0
    if args.rodar:
        return asyncio.run(
            rodar(args.concorrencia, args.sequenciais, args.inicio, args.saida)
        )
    ap.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
