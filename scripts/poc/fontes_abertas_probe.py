#!/usr/bin/env python
"""POC §27: Wikipedia, Wikivoyage, Wikidata e OSM substituem a Tavily na cascata?

A §15.1 mediu essas fontes numa amostra de atrativos obscuros do OSM no ES (5% com Wikipedia)
— e a §24 mostrou que a carga real é outra: atrativos do TripAdvisor. Esta sonda mede as
fontes abertas NOS MESMOS 140 atrativos da §26, cujo contexto Tavily e texto Gemini já estão
em disco, para comparar par a par.

Duas fases:
  --coletar   busca cada atrativo nas 4 fontes (sem key, sem custo) e grava em disco;
  --escrever  roda ``TourismCopywriter.write_cascade`` (gate de menção → Gemini 2.5 Flash →
              gate de groundedness) com o contexto de cada fonte no lugar da Tavily.

Como cada fonte é consultada:
  - Wikipedia pt: artigo próprio do atrativo (título casa os termos identificadores e o texto
    cita o município) ou, sem artigo, os parágrafos do artigo do MUNICÍPIO que citam o atrativo;
  - Wikivoyage pt e en: os parágrafos da página do município que citam o atrativo (a Wikivoyage
    é organizada por destino; atrativo é item de lista dentro dela);
  - Wikidata: o item do artigo da Wikipedia, ou busca por nome com P17=Brasil e P131 = município;
    fatos das propriedades de atrativo, com rótulos pt;
  - OSM (Nominatim, 1 req/s): o objeto pelo nome + município; categoria e tags. As tags
    ``wikidata``/``wikipedia`` servem de ponte quando a busca por nome falhou.

OpenTripMap fica fora: plano único publicado é "Free — Non-commercial use", e o dado é
OSM + Wikidata + Wikipedia reprocessados (dev.opentripmap.org/product, /price).

Uso:
    .venv/bin/python scripts/poc/fontes_abertas_probe.py --self-check
    .venv/bin/python scripts/poc/fontes_abertas_probe.py --coletar
    set -a; . ./.env; set +a
    .venv/bin/python scripts/poc/fontes_abertas_probe.py --escrever
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import Counter
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from cascade_gemini_probe import _REC, CACHE as CACHE_TAVILY, GeminiOpenRouter, amostra_ta  # noqa: E402

from brave.domains.places.grounding import _fold, afirmacoes_concretas, groundedness, menciona  # noqa: E402

AQUI = Path(__file__).parent
FONTES = AQUI / "fontes_abertas_probe.fontes.json"
SAIDA = AQUI / "fontes_abertas_probe.json"
UA = {"user-agent": "norteia-brave-poc/1.0 (avaliacao de fontes abertas; leandro.freire08@gmail.com)"}
MAX_CHARS = 6000  # ~1.700 tokens: o mesmo porte do contexto Tavily
UF_NOME = {
    "AC": "Acre", "AL": "Alagoas", "AM": "Amazonas", "AP": "Amapá", "BA": "Bahia", "CE": "Ceará",
    "DF": "Distrito Federal", "ES": "Espírito Santo", "GO": "Goiás", "MA": "Maranhão",
    "MG": "Minas Gerais", "MS": "Mato Grosso do Sul", "MT": "Mato Grosso", "PA": "Pará",
    "PB": "Paraíba", "PE": "Pernambuco", "PI": "Piauí", "PR": "Paraná", "RJ": "Rio de Janeiro",
    "RN": "Rio Grande do Norte", "RO": "Rondônia", "RR": "Roraima", "RS": "Rio Grande do Sul",
    "SC": "Santa Catarina", "SE": "Sergipe", "SP": "São Paulo", "TO": "Tocantins",
}
WD_PROPS = {
    "P31": "é um(a)", "P131": "localizado em", "P571": "fundação/criação", "P1619": "inauguração",
    "P1435": "patrimônio", "P2044": "altitude (m)", "P2048": "altura (m)", "P2043": "comprimento (m)",
    "P2046": "área", "P84": "arquiteto", "P149": "estilo arquitetônico", "P138": "nome em homenagem a",
    "P361": "parte de", "P706": "situado em", "P206": "junto a", "P4552": "cordilheira/serra",
    "P3018": "área protegida", "P112": "fundado por", "P140": "religião", "P825": "dedicado a",
}


# ---------------------------------------------------------------------------
# Coleta
# ---------------------------------------------------------------------------
def paragrafos_que_citam(texto: str, nome: str) -> str:
    ps = [p.strip() for p in texto.split("\n") if p.strip()]
    return "\n".join(p for p in ps if menciona(p, nome))[:MAX_CHARS]


_PARAR = {"da", "do", "de", "das", "dos", "e", "a", "o", "the", "beach"}


def confere(nome: str, titulo: str, texto: str) -> bool:
    """TODAS as palavras do nome no título ou na abertura — inclusive as genéricas.

    O gate de menção descarta "lagoa", "mirante", "igreja" de propósito (§24.1); para escolher
    ARTIGO isso é fatal: "Lagoa do Paraíso" casou com a novela "O Outro Lado do Paraíso" e
    "Mirante do Forte São João" com o município "Mata de São João" (§27.2).
    """
    alvo = _fold(f"{titulo} {texto[:600]}")
    palavras = [p for p in _fold(nome).replace("-", " ").split() if len(p) > 1 and p not in _PARAR and any(c.isalnum() for c in p)]
    return all(p in alvo for p in palavras)


def limpar(fontes: dict) -> tuple[dict, list[str]]:
    """Aplica ``confere`` ao que a coleta (feita com o casamento frouxo) gravou em disco."""
    rejeitados = []
    for nome, f in fontes.items():
        wp = f.get("wikipedia") or {}
        if wp.get("tipo") == "artigo" and not confere(nome, wp["titulo"], wp["texto"]):
            rejeitados.append(f"{nome} → {wp['titulo']}")
            if (f.get("wikidata") or {}).get("qid") == wp.get("qid"):
                f["wikidata"] = {}
            f["wikipedia"] = {}
    return fontes, rejeitados


def mesmo_lugar(texto: str, municipio: str, uf: str) -> bool:
    """Artigo com o nome certo sobre o lugar errado ("Praia do Forno" de outra cidade)."""
    alvo = _fold(texto)
    if municipio:
        return _fold(municipio) in alvo
    return True  # fixture do TA sem município: não há como checar


class Wiki:
    def __init__(self, c: httpx.Client, host: str) -> None:
        self.c, self.api = c, f"https://{host}/w/api.php"

    def _q(self, **p) -> dict:  # noqa: ANN003
        for tentativa in range(4):
            r = self.c.get(self.api, params={"format": "json", "formatversion": 2, **p})
            if r.status_code == 200:
                return r.json()
            time.sleep(2 * 2**tentativa)
        r.raise_for_status()
        return {}

    def busca(self, q: str, n: int = 5) -> list[str]:
        return [h["title"] for h in self._q(action="query", list="search", srsearch=q, srlimit=n)["query"]["search"]]

    def texto(self, titulo: str) -> tuple[str, str, str | None]:
        """(título resolvido, texto puro, QID)."""
        d = self._q(action="query", prop="extracts|pageprops", explaintext=1, titles=titulo, redirects=1)
        pg = d["query"]["pages"][0]
        return pg.get("title", titulo), pg.get("extract", "") or "", (pg.get("pageprops") or {}).get("wikibase_item")


def coletar_wiki(w: Wiki, a: dict, *, artigo_proprio: bool) -> dict:
    nome, mun, uf = a["nome"], a["municipio"], a["uf"]
    local = f"{mun} {UF_NOME.get(uf, '')}".strip()
    hits = w.busca(f"{nome} {local}".strip())
    if artigo_proprio:
        for t in hits:
            if menciona(t, nome):
                titulo, texto, qid = w.texto(t)
                if texto and mesmo_lugar(texto, mun, uf) and confere(nome, titulo, texto):
                    return {"tipo": "artigo", "titulo": titulo, "qid": qid, "texto": texto[:MAX_CHARS]}
    if mun:
        cand = [t for t in hits if _fold(t).split(" (")[0] == _fold(mun)] or [mun]
        titulo, texto, _ = w.texto(cand[0])
        trecho = paragrafos_que_citam(texto, nome)
        if trecho:
            return {"tipo": "municipio", "titulo": titulo, "texto": trecho}
    return {}


def coletar_wikidata(c: httpx.Client, a: dict, qid: str | None) -> dict:
    api = "https://www.wikidata.org/w/api.php"

    def ents(ids: list[str], props: str = "claims|labels") -> dict:
        if not ids:
            return {}
        r = c.get(api, params={"action": "wbgetentities", "ids": "|".join(ids[:50]), "props": props,
                               "languages": "pt|en", "format": "json"})
        return r.json().get("entities", {})

    def rotulo(e: dict) -> str:
        lb = e.get("labels", {})
        return (lb.get("pt") or lb.get("en") or {}).get("value", "")

    if not qid:
        r = c.get(api, params={"action": "wbsearchentities", "search": a["nome"], "language": "pt",
                               "limit": 7, "format": "json"})
        ids = [h["id"] for h in r.json().get("search", [])]
        for q, e in ents(ids).items():
            cl = e.get("claims", {})
            pais = {x["mainsnak"].get("datavalue", {}).get("value", {}).get("id") for x in cl.get("P17", [])}
            if "Q155" not in pais or not menciona(rotulo(e), a["nome"]):
                continue
            p131 = [x["mainsnak"].get("datavalue", {}).get("value", {}).get("id") for x in cl.get("P131", [])]
            locais = {_fold(rotulo(v)) for v in ents([p for p in p131 if p], "labels").values()}
            if not a["municipio"] or _fold(a["municipio"]) in locais:
                qid = q
                break
    if not qid:
        return {}
    e = ents([qid])[qid]
    cl = e.get("claims", {})
    fatos: list[tuple[str, str]] = []
    refs: set[str] = set()
    for p in WD_PROPS:
        for x in cl.get(p, []):
            v = x["mainsnak"].get("datavalue", {}).get("value")
            if isinstance(v, dict) and "id" in v:
                refs.add(v["id"])
                fatos.append((p, v["id"]))
            elif isinstance(v, dict) and "time" in v:
                fatos.append((p, v["time"][1:5]))
            elif isinstance(v, dict) and "amount" in v:
                fatos.append((p, v["amount"].lstrip("+")))
            elif v is not None:
                fatos.append((p, str(v)))
    nomes = {k: rotulo(v) for k, v in ents(sorted(refs)).items()}
    linhas = [f"{WD_PROPS[p]}: {nomes.get(v, v)}" for p, v in fatos if nomes.get(v, v)]
    texto = f"{rotulo(e)} (Wikidata {qid})\n" + "\n".join(linhas)
    return {"qid": qid, "n_fatos": len(linhas), "texto": texto} if linhas else {}


def coletar_osm(c: httpx.Client, a: dict) -> dict:
    q = ", ".join(x for x in (a["nome"], a["municipio"], UF_NOME.get(a["uf"], ""), "Brasil") if x)
    time.sleep(1.1)  # política do Nominatim: no máximo 1 req/s
    r = c.get("https://nominatim.openstreetmap.org/search",
              params={"q": q, "format": "jsonv2", "extratags": 1, "namedetails": 1, "limit": 3})
    for h in r.json() if r.status_code == 200 else []:
        if not menciona(h.get("name") or h.get("display_name", ""), a["nome"]):
            continue
        tags = h.get("extratags") or {}
        linhas = [f"{h['name']} — OpenStreetMap: {h['category']}={h['type']}", f"endereço: {h['display_name']}"]
        linhas += [f"{k}: {v}" for k, v in tags.items()]
        return {"osm": f"{h['osm_type']}/{h['osm_id']}", "tags": tags, "texto": "\n".join(linhas)}
    return {}


def coletar() -> int:
    feito = json.loads(FONTES.read_text()) if FONTES.exists() else {}
    with httpx.Client(headers=UA, timeout=30.0, follow_redirects=True) as c:
        wp, vpt, ven = Wiki(c, "pt.wikipedia.org"), Wiki(c, "pt.wikivoyage.org"), Wiki(c, "en.wikivoyage.org")
        for i, a in enumerate(amostra_ta(140)):
            if a["nome"] in feito:
                continue
            f: dict = {}
            try:
                f["osm"] = coletar_osm(c, a)
                f["wikipedia"] = coletar_wiki(wp, a, artigo_proprio=True)
                ponte = (f["osm"].get("tags") or {})
                if not f["wikipedia"] and ponte.get("wikipedia", "").startswith("pt:"):
                    t, texto, qid = wp.texto(ponte["wikipedia"][3:])
                    if texto:
                        f["wikipedia"] = {"tipo": "artigo", "titulo": t, "qid": qid, "texto": texto[:MAX_CHARS], "via": "osm"}
                f["wikivoyage_pt"] = coletar_wiki(vpt, a, artigo_proprio=False)
                f["wikivoyage_en"] = coletar_wiki(ven, a, artigo_proprio=False)
                qid = f["wikipedia"].get("qid") or ponte.get("wikidata")
                f["wikidata"] = coletar_wikidata(c, a, qid)
            except Exception as exc:  # noqa: BLE001
                f["erro"] = f"{type(exc).__name__}: {exc}"[:200]
            feito[a["nome"]] = f
            print(i, a["nome"], {k: bool(v) for k, v in f.items()}, flush=True)
            if i % 10 == 0:
                FONTES.write_text(json.dumps(feito, ensure_ascii=False, indent=1))
    FONTES.write_text(json.dumps(feito, ensure_ascii=False, indent=1))
    return 0


# ---------------------------------------------------------------------------
# Redação
# ---------------------------------------------------------------------------
VARIANTES = {
    "wikipedia": ["wikipedia"],
    "wikivoyage": ["wikivoyage_pt", "wikivoyage_en"],
    "wikidata": ["wikidata"],
    "osm": ["osm"],
    "abertas": ["wikipedia", "wikivoyage_pt", "wikivoyage_en", "wikidata", "osm"],
}


def contexto(f: dict, chaves: list[str]) -> str:
    return "\n\n".join(f[k]["texto"] for k in chaves if (f.get(k) or {}).get("texto"))


class FonteFixa:
    """search_client da lane: a 1ª query recebe o contexto da fonte, a 2ª nada."""

    def __init__(self, txt: str) -> None:
        self.txt, self.n = txt, 0

    async def search(self, q: str) -> str:  # noqa: ARG002
        self.n += 1
        return self.txt if self.n == 1 else ""


def riqueza(texto: str, ctx: str) -> int:
    """Afirmações concretas DISTINTAS e fundamentadas — o proxy de "fatos" desta sonda."""
    alvo = _fold(ctx)
    return len({_fold(x) for x in afirmacoes_concretas(texto) if _fold(x) in alvo})


async def escrever(conc: int) -> int:
    from brave.domains.places.copywriter import TourismCopywriter

    fontes, rejeitados = limpar(json.loads(FONTES.read_text()))
    print(f"{len(rejeitados)} artigos rejeitados pelo casamento estrito:", *rejeitados, sep="\n  ")
    tav = json.loads(CACHE_TAVILY.read_text())
    base = {r["nome"]: r for r in json.loads((AQUI / "cascade_gemini_probe.gemini-2.5-flash.json").read_text())["atrativos"]}
    llm = GeminiOpenRouter(thinking=False)
    sem = asyncio.Semaphore(conc)
    recs: list[dict] = []

    async def um(a: dict, var: str) -> None:
        ctx = contexto(fontes.get(a["nome"], {}), VARIANTES[var])
        rec = {"nome": a["nome"], "variante": var, "ctx_chars": len(ctx), "menciona": bool(ctx) and menciona(ctx, a["nome"])}
        async with sem:
            _REC.set(rec)
            cw = TourismCopywriter(llm, "google/gemini-2.5-flash", search_client=FonteFixa(ctx))
            out = await cw.write_cascade(a["nome"], a["municipio"], a["uf"], {})
        rec["resultado"] = "ok" if out.prose else (out.motivo or "falha")
        rec["texto"] = out.prose or out.rascunho or ""
        rec["riqueza"] = riqueza(out.prose, ctx) if out.prose else 0
        recs.append(rec)

    itens = amostra_ta(140)
    await asyncio.gather(*(um(a, v) for a in itens for v in VARIANTES))

    # Linha de base: a Tavily da §26, mesmo redator, mesmo prompt.
    from brave.domains.places.copywriter import cascade_queries

    for a in itens:
        b = base[a["nome"]]
        ctx = "\n\n".join(tav[q] for q in cascade_queries(a["nome"], a["municipio"], a["uf"]))
        recs.append({"nome": a["nome"], "variante": "tavily", "ctx_chars": len(ctx), "menciona": menciona(ctx, a["nome"]),
                     "resultado": b["resultado"], "texto": b["texto"], "usd_llm": b.get("usd_llm"),
                     "riqueza": riqueza(b["texto"], ctx) if b["resultado"] == "ok" else 0})

    rel = relatorio(recs, fontes, itens)
    SAIDA.write_text(json.dumps({"relatorio": rel, "registros": recs}, ensure_ascii=False, indent=2))
    print(json.dumps(rel, ensure_ascii=False, indent=2))
    return 0


def relatorio(recs: list[dict], fontes: dict, itens: list[dict]) -> dict:
    n = len(itens)
    por: dict[str, dict] = {}
    for var in [*VARIANTES, "tavily"]:
        rs = [r for r in recs if r["variante"] == var]
        ok = [r for r in rs if r["resultado"] == "ok"]
        por[var] = {
            "cobertura_menciona": sum(r["menciona"] for r in rs),
            "resultados": dict(Counter(r["resultado"] for r in rs)),
            "aprovados": len(ok),
            "riqueza_media_aprovados": round(sum(r["riqueza"] for r in ok) / max(len(ok), 1), 2),
            "ctx_chars_p50": sorted(r["ctx_chars"] for r in rs)[len(rs) // 2] if rs else 0,
            "llm_usd": round(sum(r.get("usd_llm") or 0 for r in rs), 4),
        }
    # Cascata híbrida: abertas primeiro; Tavily só para quem as abertas não aprovaram.
    ab = {r["nome"]: r for r in recs if r["variante"] == "abertas"}
    tv = {r["nome"]: r for r in recs if r["variante"] == "tavily"}
    sem_texto = [a["nome"] for a in itens if ab[a["nome"]]["resultado"] != "ok"]
    hib_ok = sum(1 for a in itens if ab[a["nome"]]["resultado"] == "ok" or tv[a["nome"]]["resultado"] == "ok")
    # Onde os dois aprovaram: o texto das abertas é tão rico quanto o da Tavily?
    pares = [(ab[k]["riqueza"], tv[k]["riqueza"]) for k in ab if ab[k]["resultado"] == "ok" and tv[k]["resultado"] == "ok"]
    tipos_wp = Counter((f.get("wikipedia") or {}).get("tipo", "nada") for f in fontes.values())
    return {
        "n": n,
        "wikipedia_tipo": dict(tipos_wp),
        "wikipedia_via_osm": sum(1 for f in fontes.values() if (f.get("wikipedia") or {}).get("via") == "osm"),
        "erros_coleta": sum(1 for f in fontes.values() if "erro" in f),
        "por_variante": por,
        "hibrida": {
            "atrativos_que_ainda_vao_para_tavily": len(sem_texto),
            "buscas_tavily_evitadas_pct": round(100 * (n - len(sem_texto)) / n, 1),
            "aprovados": hib_ok,
        },
        "pareado_abertas_vs_tavily": {
            "n": len(pares),
            "riqueza_abertas": round(sum(p[0] for p in pares) / max(len(pares), 1), 2),
            "riqueza_tavily": round(sum(p[1] for p in pares) / max(len(pares), 1), 2),
            "abertas_mais_rico": sum(1 for x, y in pares if x > y),
            "empate": sum(1 for x, y in pares if x == y),
            "tavily_mais_rico": sum(1 for x, y in pares if x < y),
        },
    }


def self_check() -> int:
    assert paragrafos_que_citam("Arraial do Cabo.\nA Praia do Forno tem águas claras.\nOutra.", "Praia do Forno") == "A Praia do Forno tem águas claras."
    assert not confere("Lagoa do Paraíso", "O Outro Lado do Paraíso", "telenovela brasileira")
    assert confere("Praia Do Espelho", "Praia do Espelho", "praia em Porto Seguro")
    assert mesmo_lugar("Praia em Arraial do Cabo, RJ", "Arraial do Cabo", "RJ")
    assert not mesmo_lugar("Praia em Búzios", "Arraial do Cabo", "RJ")
    assert contexto({"wikipedia": {"texto": "a"}, "osm": {}}, ["wikipedia", "osm"]) == "a"
    assert riqueza("Fundado em 1558 por Pedro Palácios, a 154 metros.", "1558 Pedro Palácios") == 2
    ok, tot, _ = groundedness("Fundado em 1558.", "1558")
    assert (ok, tot) == (1, 1)
    print("self-check ok: recorte de parágrafo, lugar, contexto e riqueza.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-check", action="store_true")
    ap.add_argument("--coletar", action="store_true")
    ap.add_argument("--escrever", action="store_true")
    ap.add_argument("--concorrencia", type=int, default=8)
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    if a.coletar:
        return coletar()
    if a.escrever:
        return asyncio.run(escrever(a.concorrencia))
    ap.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
