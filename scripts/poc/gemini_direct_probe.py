"""§30 — os mesmos 100 atrativos escritos pelo Gemini direto (AI Studio), tier flex ou standard.

Mede o que a rota do OpenRouter não permite medir: o tier cobrado (`usageMetadata.serviceTier`),
a taxa de 503 do Flex e o preço pela tabela local (o Google não devolve custo). As buscas são as
mesmas da §29 (`parallel_direto_probe.busca.turbo.json`), então o único fator que muda é o redator.

uso: .venv/bin/python scripts/poc/gemini_direct_probe.py MODELO TIER CONCORRENCIA
     MODELO = gemini-2.5-flash | gemini-2.5-flash-lite   TIER = flex | standard
chave: BRAVE_LLM_GEMINI_API_KEY (no ambiente ou no .env da raiz).
saída: scripts/poc/gemini_direct_probe.{MODELO}.{TIER}.json + um resumo no stdout.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts/poc"))
from fontes_abertas_probe import riqueza  # noqa: E402
from parallel_direto_probe import amostra_ta  # noqa: E402

from brave.domains.places.copywriter import (  # noqa: E402
    COPYWRITER_SYSTEM,
    _build_context,
    _strip_dashes,
)
from brave.domains.places.grounding import (  # noqa: E402
    MIN_GROUNDEDNESS,
    groundedness_ratio,
    menciona,
    menciona_municipio,
)

MODEL, TIER, CONC = sys.argv[1], sys.argv[2], int(sys.argv[3])
PRICE = {  # USD por 1M tokens (entrada, saída), ai.google.dev/gemini-api/docs/pricing 2026-09-15
    ("gemini-2.5-flash", "standard"): (0.30, 2.50), ("gemini-2.5-flash", "flex"): (0.15, 1.25),
    ("gemini-2.5-flash-lite", "standard"): (0.10, 0.40), ("gemini-2.5-flash-lite", "flex"): (0.05, 0.20),
}[(MODEL, TIER)]
HERE = Path(__file__).parent
OUT = HERE / f"gemini_direct_probe.{MODEL}.{TIER}.json"


def _chave() -> str:
    if os.environ.get("BRAVE_LLM_GEMINI_API_KEY"):
        return os.environ["BRAVE_LLM_GEMINI_API_KEY"]
    for linha in (ROOT / ".env").read_text().splitlines():
        if linha.startswith("BRAVE_LLM_GEMINI_API_KEY="):
            return linha.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit("BRAVE_LLM_GEMINI_API_KEY ausente (ambiente ou .env).")


key = _chave()
busca = json.loads((HERE / "parallel_direto_probe.busca.turbo.json").read_text())
base = {r["nome"]: r for r in json.loads((HERE / "parallel_direto_probe.turbo.json").read_text())["registros"]}
# Comparação opcional com o claude -p Sonnet (§28.x); ausente = colunas vazias.
_sonnet_json = HERE / "claude_p_probe.sonnet.default.json"
sonnet = {r["nome"]: r for r in json.loads(_sonnet_json.read_text())["registros"]} if _sonnet_json.exists() else {}
itens = [a for a in amostra_ta(140)
         if (c := (busca.get(a["nome"]) or {}).get("bruto")) and menciona(c, a["nome"]) and menciona_municipio(c, a["municipio"])][:100]


def run(a: dict) -> dict:
    ctx = busca[a["nome"]]["bruto"]
    body = {"systemInstruction": {"parts": [{"text": COPYWRITER_SYSTEM}]},
            "contents": [{"role": "user", "parts": [{"text": _build_context(a["nome"], a["municipio"], a["uf"], {}, fontes=ctx)}]}],
            "generationConfig": {"maxOutputTokens": 2048, "thinkingConfig": {"thinkingBudget": 0}}}
    if TIER == "flex":
        body["serviceTier"] = "flex"
    rec = {"nome": a["nome"], "tentativas": 0}
    t = time.time()
    for tentativa in range(6):
        rec["tentativas"] = tentativa + 1
        req = urllib.request.Request(f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent",
                                     data=json.dumps(body).encode(), headers={"x-goog-api-key": key, "Content-Type": "application/json"})
        try:
            d = json.load(urllib.request.urlopen(req, timeout=300))
            break
        except urllib.error.HTTPError as e:
            rec.setdefault("http_erros", []).append(e.code)
            if e.code in (429, 500, 503, 504):
                time.sleep(min(60, 5 * 2 ** tentativa))
                continue
            rec["erro"] = e.read().decode()[:300]
            return rec
        except Exception as e:  # noqa: BLE001
            rec.setdefault("http_erros", []).append(type(e).__name__)
            time.sleep(10)
    else:
        rec["erro"] = "esgotou tentativas"
        return rec
    rec["s"] = round(time.time() - t, 1)
    c = d["candidates"][0]
    u = d.get("usageMetadata", {})
    txt = _strip_dashes("".join(p.get("text", "") for p in c.get("content", {}).get("parts", [])))
    g = groundedness_ratio(txt, ctx) if txt else 0.0
    tin, tout = u.get("promptTokenCount", 0), u.get("candidatesTokenCount", 0) + u.get("thoughtsTokenCount", 0)
    b, s = base.get(a["nome"], {}), sonnet.get(a["nome"], {})
    rec.update({"finish": c.get("finishReason"), "tier": u.get("serviceTier"), "in": tin, "out": tout,
                "cached": u.get("cachedContentTokenCount", 0), "usd": (tin * PRICE[0] + tout * PRICE[1]) / 1e6,
                "g": round(g, 3), "ok": bool(txt) and g >= MIN_GROUNDEDNESS and c.get("finishReason") == "STOP",
                "riqueza": riqueza(txt, ctx) if txt else 0, "chars": len(txt),
                "or_riqueza": b.get("riqueza") if b.get("resultado") == "ok" else None,
                "sonnet_riqueza": s.get("riqueza") if s.get("ok") else None, "texto": txt})
    return rec


t0 = time.time()
with ThreadPoolExecutor(CONC) as ex:
    recs = list(ex.map(run, itens))
wall = round(time.time() - t0, 1)
OUT.write_text(json.dumps({"model": MODEL, "tier": TIER, "conc": CONC, "wall_s": wall, "registros": recs}, ensure_ascii=False, indent=1))
ok = [r for r in recs if r.get("ok")]
good = [r for r in recs if "in" in r]
pairs = [(r["riqueza"], r["or_riqueza"]) for r in ok if r.get("or_riqueza") is not None]
spairs = [(r["riqueza"], r["sonnet_riqueza"]) for r in ok if r.get("sonnet_riqueza") is not None]
lat = sorted(r["s"] for r in good)
avg = lambda xs: round(sum(xs) / max(len(xs), 1), 2)  # noqa: E731
print(json.dumps({
    "model": MODEL, "tier": TIER, "n": len(recs), "wall_s": wall, "erros": sum(1 for r in recs if r.get("erro")),
    "com_retry": sum(1 for r in recs if r.get("http_erros")), "http_erros": sorted({str(x) for r in recs for x in r.get("http_erros", [])}),
    "tiers_vistos": sorted({str(r.get("tier")) for r in good}), "finish": sorted({str(r.get("finish")) for r in good}),
    "aprovados": len(ok), "g_medio": avg([r["g"] for r in good]),
    "riqueza_vs_openrouter_2.5flash": {"n": len(pairs), "este": avg([p for p, _ in pairs]), "openrouter": avg([q for _, q in pairs])},
    "riqueza_vs_sonnet": {"n": len(spairs), "este": avg([p for p, _ in spairs]), "sonnet": avg([q for _, q in spairs])},
    "in_medio": avg([r["in"] for r in good]), "out_medio": avg([r["out"] for r in good]), "cached_total": sum(r["cached"] for r in good),
    "lat_p50": lat[len(lat) // 2] if lat else None, "lat_p95": lat[int(len(lat) * .95)] if lat else None,
    "usd_100": round(sum(r["usd"] for r in good), 4), "usd_10k": round(sum(r["usd"] for r in good) / max(len(good), 1) * 10_000, 2),
}, ensure_ascii=False, indent=1))
