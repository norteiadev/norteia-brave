"""POC: mede quantas requisições ANÔNIMAS o Maps aceita deste IP antes do primeiro bloqueio.

Por quê: decidir se o scraper (busca ``tbm=map`` + ``preview/place``) aguenta o volume de um sweep
de UF no lugar do Places API. Rampa em degraus (intervalo cai, depois concorrência sobe) e PARA no
primeiro sinal de bloqueio — sem proxy, sem rotação de UA, sem cookies, sem captcha. Depois do
bloqueio só testa recuperação (1 req/60s, até 10 min). Standalone: não importa ``brave``.

Entradas: ftids e consultas já coletados em out/maps.jsonl (detalhe e busca alternados 1:1).
Saídas: out/rate_probe.jsonl (uma linha por requisição) e out/rate_probe.md (tabela por degrau).

    uv run --frozen python poc/maps_scraper/rate_probe.py
    uv run --frozen python poc/maps_scraper/rate_probe.py --selftest
"""

from __future__ import annotations

import argparse
import itertools
import json
import pathlib
import sys
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import httpx

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from maps_scraper import BRASIL_CENTER, DETAIL_URL, SEARCH_URL, UA, block_reason  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE / "out"
CAP = 500
# (nome, reqs, intervalo s, concorrência)
STEPS = [("D1", 30, 2.0, 1), ("D2", 40, 1.0, 1), ("D3", 60, 0.5, 1),
         ("D4", 80, 0.0, 1), ("D5", 120, 0.0, 4), ("D6", 160, 0.0, 8)]


def signal(status: int, location: str, body: str) -> str | None:
    """Sinal de bloqueio: os do scraper + qualquer resposta sem o prefixo JSON ``)]}'``."""
    return block_reason(status, location, body) or (
        None if body.startswith(")]}'") else f"http_{status}_sem_prefixo")


def pct(xs: list[int], p: float) -> int:
    s = sorted(xs)
    return s[min(len(s) - 1, int(round(p * (len(s) - 1))))] if s else 0


def targets() -> list[tuple[str, str]]:
    """[(endpoint, url)] alternando detalhe/busca, do que maps.jsonl já resolveu."""
    det, sea = [], []
    for line in (OUT / "maps.jsonl").open():
        r = json.loads(line)
        q = urllib.parse.quote(r["query"])
        sea.append(("search", SEARCH_URL.format(Q=q, D=4_000_000, LAT=BRASIL_CENTER[0],
                                                LNG=BRASIL_CENTER[1])))
        ftid = (r.get("details") or {}).get("ftid")
        if ftid:
            det.append(("detail", DETAIL_URL.format(FTID=urllib.parse.quote(ftid, safe=""), Q=q)))
    return [x for pair in zip(det, sea) for x in pair]


class Probe:
    def __init__(self, log: pathlib.Path):
        self.client = httpx.Client(headers={"User-Agent": UA, "Accept-Language": "pt-BR,pt;q=0.9"},
                                   timeout=20, follow_redirects=False)
        self.fh = log.open("w")
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.rows: list[dict] = []
        self.t0 = time.monotonic()

    def hit(self, step: str, endpoint: str, url: str) -> dict | None:
        if self.stop.is_set():
            return None
        t = time.monotonic()
        try:
            r = self.client.get(url)
            status, loc, body, n = r.status_code, r.headers.get("location", ""), r.text, len(r.content)
            sig = signal(status, loc, body)
        except httpx.HTTPError as e:  # rede/timeout: erro, não bloqueio
            status, n, sig = 0, 0, None
            err = type(e).__name__
        else:
            err = None
        row = {"t": round(t - self.t0, 3), "ts": datetime.now(timezone.utc).isoformat(),
               "step": step, "endpoint": endpoint, "status": status,
               "ms": int((time.monotonic() - t) * 1000), "bytes": n, "block": sig, "error": err}
        with self.lock:
            self.rows.append(row)
            self.fh.write(json.dumps(row) + "\n")
            self.fh.flush()
            if sig:
                self.stop.set()
        return row


def run() -> int:
    OUT.mkdir(exist_ok=True)
    pool = itertools.cycle(targets())
    p = Probe(OUT / "rate_probe.jsonl")
    spans: dict[str, tuple[float, float]] = {}
    sent = 0
    for name, n, gap, conc in STEPS:
        if p.stop.is_set() or sent >= CAP:
            break
        n = min(n, CAP - sent)
        jobs = [next(pool) for _ in range(n)]
        start = time.monotonic()
        if conc == 1:
            for i, (ep, url) in enumerate(jobs):
                if i and gap:
                    time.sleep(gap)
                if not p.hit(name, ep, url):
                    break
        else:
            with ThreadPoolExecutor(conc) as ex:
                list(ex.map(lambda j: p.hit(name, *j), jobs))
        spans[name] = (start, time.monotonic())
        sent = len(p.rows)
        print(f"{name}: {sum(r['step'] == name for r in p.rows)} reqs, "
              f"bloqueio={'sim' if p.stop.is_set() else 'não'}", flush=True)

    recovery = []
    if p.stop.is_set():
        blk = next(r for r in p.rows if r["block"])
        print(f"BLOQUEIO em {blk['step']} req#{len(p.rows)}: {blk['block']}", flush=True)
        p.stop.clear()
        ep, url = next(pool)
        for k in range(10):
            time.sleep(60)
            r = p.hit("recovery", ep, url)
            recovery.append(r)
            print(f"recovery +{k + 1}min: {r['status']} {r['block']}", flush=True)
            if not r["block"] and not r["error"]:
                break
            p.stop.clear()
    p.fh.close()
    report(p.rows, spans, recovery)
    return 0


def report(rows: list[dict], spans: dict, recovery: list[dict]) -> None:
    lines = ["# Rate probe — Maps anônimo, 1 IP", "",
             f"Gerado {datetime.now(timezone.utc).isoformat()} · {len(rows)} requisições", "",
             "| degrau | reqs | intervalo/conc | duração s | rpm efetivo | p50 ms | p95 ms | "
             "erros | bloqueio |", "|---|---|---|---|---|---|---|---|---|"]
    best = 0.0
    for name, _, gap, conc in STEPS:
        rs = [r for r in rows if r["step"] == name]
        if not rs:
            continue
        a, b = spans[name]
        rpm = len(rs) / max(b - a, 1e-6) * 60
        blk = next((r["block"] for r in rs if r["block"]), "")
        if not blk:
            best = max(best, rpm)
        ms = [r["ms"] for r in rs]
        errs = sum(1 for r in rs if r["error"] or r["status"] != 200)
        lines.append(f"| {name} | {len(rs)} | {gap}s / {conc} | {b - a:.1f} | {rpm:.0f} | "
                     f"{pct(ms, .5)} | {pct(ms, .95)} | {errs} | {blk or '—'} |")
    lines += ["", f"Maior rpm sem bloqueio: **{best:.0f} rpm** → "
              f"~{best * 60 / 2:.0f} atrativos/hora (2 req/atrativo: busca + detalhe).", "",
              "Ressalva: 1 IP, janela curta (minutos). Limites do Google podem ser por janela "
              "longa (horas/dia) ou por reputação do IP e não foram observados aqui; não "
              "extrapolar linearmente para um sweep de horas sem um teste de soak.", ""]
    if recovery:
        lines += ["## Recuperação", ""] + [f"- t={r['t']}s status={r['status']} "
                                           f"bloqueio={r['block']}" for r in recovery]
    (OUT / "rate_probe.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


def selftest() -> int:
    assert signal(200, "", ")]}'\n[]") is None
    assert signal(429, "", "") == "http_429"
    assert signal(302, "https://www.google.com/sorry/index?x", "") == "redirect_sorry"
    assert signal(200, "", "<html>Our systems have detected unusual traffic") == "body:unusual traffic"
    assert signal(200, "", "<html>ok</html>") == "http_200_sem_prefixo"
    assert signal(500, "", "") == "http_500_sem_prefixo"
    assert pct([1, 2, 3, 4, 5], .5) == 3 and pct([1, 2, 3, 4, 100], .95) == 100 and pct([], .5) == 0
    print("selftest ok")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--selftest", action="store_true")
    sys.exit(selftest() if ap.parse_args().selftest else run())
