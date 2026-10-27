"""POC: avalia se o out/maps.jsonl do maps_scraper serve ao Brave.

Por quê: "parece o Places" não basta; o que importa é se as FUNÇÕES PURAS do Brave
(to_hours_map, _google_not_atrativo, _result_is_business, _best_match) dão o mesmo resultado
com o dado raspado que com o Places API (New) oficial. Com --ground-truth, numa subamostra
chama Text Search + Place Details com as MESMAS field masks de brave/clients/places.py
(REST via httpx, chave só da env BRAVE_PLACES_API_KEY) e compara atrativo a atrativo.

Não toca DB/Redis/Celery/norteia-api: os imports do Brave rodam com socket.connect bloqueado.

    uv run --frozen python poc/maps_scraper/avaliar.py                      # cobertura + gate
    uv run --frozen python poc/maps_scraper/avaliar.py --ground-truth --md poc/maps_scraper/out/avaliacao.md
    uv run --frozen python poc/maps_scraper/avaliar.py --selftest
"""

from __future__ import annotations

import argparse
import json
import math
import os
import pathlib
import re
import socket
import sys
import urllib.parse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE / "out"
sys.path.insert(0, str(HERE.parents[1]))


def _import_brave() -> None:
    """Importa só funções/constantes puras; qualquer tentativa de conexão no import explode."""
    real = socket.socket.connect

    def deny(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("import do brave tentou abrir conexão")

    socket.socket.connect = deny  # type: ignore[method-assign]
    try:
        global to_hours_map, _google_not_atrativo, _result_is_business, _best_match
        global _TEXT_SEARCH_FIELD_MASK, _GET_PLACE_FIELD_MASK
        from brave.clients.places import _GET_PLACE_FIELD_MASK, _TEXT_SEARCH_FIELD_MASK
        from brave.domains.places.places_enrichment import (
            _best_match,
            _google_not_atrativo,
            _result_is_business,
        )
        from brave.shared.opening_hours import to_hours_map
    finally:
        socket.socket.connect = real  # type: ignore[method-assign]


_import_brave()

NOW = datetime.now(timezone.utc)
FIELDS = [
    "place_id", "business_status", "weekday_text", "to_hours_map", "reviews_publishTime",
    "review_recente_90d", "telefone", "site", "location", "types", "primary_type",
    "price_level", "municipio_nome",
]


def last_review(d: dict) -> datetime | None:
    ts = [r["publishTime"] for r in d.get("reviews") or [] if r.get("publishTime")]
    return max((datetime.fromisoformat(t.replace("Z", "+00:00")) for t in ts), default=None)


def has(d: dict, field: str) -> bool:
    if field == "to_hours_map":
        return to_hours_map(d.get("weekday_text")) is not None
    if field == "reviews_publishTime":
        return any(r.get("publishTime") for r in d.get("reviews") or [])
    if field == "review_recente_90d":
        lr = last_review(d)
        return lr is not None and NOW - lr <= timedelta(days=90)
    if field == "telefone":
        return bool(d.get("international_phone_number"))
    if field == "site":
        return bool(d.get("website"))
    if field == "location":
        loc = d.get("location") or {}
        return loc.get("lat") is not None and loc.get("lng") is not None
    if field == "business_status":
        return d.get("business_status") not in (None, "", "UNKNOWN")
    return bool(d.get(field))


def gate(d: dict) -> str:
    """Veredito do Brave: descarta (não-atrativo) ou passa."""
    return "descarta" if _google_not_atrativo(d) else "passa"


def phone8(p: str | None) -> str:
    return re.sub(r"\D", "", p or "")[-8:]


def domain(u: str | None) -> str:
    host = urllib.parse.urlsplit(u or "").hostname or ""
    return host.removeprefix("www.")


def km(a: dict | None, b: dict | None) -> float | None:
    if not a or not b or None in (a.get("lat"), a.get("lng"), b.get("lat"), b.get("lng")):
        return None
    p1, p2 = math.radians(a["lat"]), math.radians(b["lat"])
    h = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(b["lng"] - a["lng"]) / 2) ** 2)
    return round(6371.0 * 2 * math.atan2(math.sqrt(h), math.sqrt(1 - h)), 3)


def pct(n: int, total: int) -> str:
    return f"{n}/{total} ({100 * n / total:.0f}%)" if total else "0/0"


# --------------------------------------------------------------------------- Places oficial

def _components(comps: list[dict]) -> tuple[str, str]:
    """Espelho de _extract_municipio_from_components para o JSON REST (longText/shortText)."""
    mun = loc = uf = ""
    for c in comps or []:
        t = c.get("types") or []
        if "administrative_area_level_2" in t:
            mun = c.get("longText", "")
        elif "administrative_area_level_1" in t:
            uf = c.get("shortText", "")
        elif "locality" in t:
            loc = c.get("longText", "")
    return mun or loc, uf.upper()


def norm_search(p: dict) -> dict:
    mun, uf = _components(p.get("addressComponents"))
    loc = p.get("location") or {}
    return {
        "place_id": p.get("id", ""), "name": (p.get("displayName") or {}).get("text", ""),
        "formatted_address": p.get("formattedAddress", ""), "types": p.get("types") or [],
        "location": {"lat": loc.get("latitude"), "lng": loc.get("longitude")},
        "municipio_nome": mun, "uf_short": uf,
        # ponytail: sem tabela IBGE (regra do POC); nome de município presente ≈ resolvido.
        "municipio_ibge": mun,
    }


def norm_details(p: dict) -> dict:
    mun, uf = _components(p.get("addressComponents"))
    loc = p.get("location")
    pl = p.get("priceLevel")
    return {
        "place_id": p.get("id", ""), "name": (p.get("displayName") or {}).get("text", ""),
        "formatted_address": p.get("formattedAddress", ""),
        "international_phone_number": p.get("internationalPhoneNumber"),
        "website": p.get("websiteUri"),
        "business_status": p.get("businessStatus") or "UNKNOWN",
        "weekday_text": (p.get("regularOpeningHours") or {}).get("weekdayDescriptions") or [],
        "reviews": [{"publishTime": r.get("publishTime"), "rating": r.get("rating"),
                     "text": (r.get("text") or {}).get("text", "")} for r in p.get("reviews") or []],
        "location": {"lat": loc["latitude"], "lng": loc["longitude"]} if loc else None,
        "price_level": None if pl in (None, "PRICE_LEVEL_UNSPECIFIED") else pl,
        "types": p.get("types") or [], "primary_type": p.get("primaryType") or "",
        "municipio_nome": mun, "uf_short": uf,
    }


def places_lookup(client: httpx.Client, key: str, nome: str, uf: str) -> dict:
    """O que o Brave faria: text_search(nome, uf) → filtra UF → _best_match → place_details."""
    r = client.post("https://places.googleapis.com/v1/places:searchText",
                    headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": _TEXT_SEARCH_FIELD_MASK},
                    json={"textQuery": f"{nome} {uf} Brasil", "maxResultCount": 20,
                          "languageCode": "pt-BR"})
    r.raise_for_status()
    results = [norm_search(p) for p in r.json().get("places") or []]
    results = [x for x in results if len(x["uf_short"]) != 2 or x["uf_short"] == uf.upper()]
    match = _best_match(results, nome, None, None, 20.0)
    out: dict[str, Any] = {"gt_requests": 1, "gt_candidates": [
        {k: x[k] for k in ("place_id", "name", "types", "municipio_nome", "uf_short")} for x in results]}
    if not match:
        return out
    r = client.get(f"https://places.googleapis.com/v1/places/{match['place_id']}",
                   params={"languageCode": "pt-BR"},
                   headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": _GET_PLACE_FIELD_MASK})
    r.raise_for_status()
    out["gt_requests"] = 2
    out["gt_details"] = norm_details(r.json())
    return out


def compare(s: dict | None, g: dict | None) -> dict:
    """Atrativo a atrativo: scraper (s) vs Places oficial (g). None = lado sem match."""
    if not s or not g:
        return {"ambos_match": False, "so_scraper": bool(s), "so_places": bool(g)}
    lr_s, lr_g = last_review(s), last_review(g)
    return {
        "ambos_match": True,
        "mesmo_place_id": s["place_id"] == g["place_id"],
        "dist_km": km(s.get("location"), g.get("location")),
        "mesmo_status": s["business_status"] == g["business_status"],
        "status": [s["business_status"], g["business_status"]],
        "mesmo_gate": gate(s) == gate(g),
        "gate": [gate(s), gate(g)],
        "primary_type": [s.get("primary_type"), g.get("primary_type")],
        "mesmo_hours_map": to_hours_map(s.get("weekday_text")) == to_hours_map(g.get("weekday_text")),
        "hours_map": [to_hours_map(s.get("weekday_text")), to_hours_map(g.get("weekday_text"))],
        "review_mais_recente": [lr_s and lr_s.date().isoformat(), lr_g and lr_g.date().isoformat()],
        "mesmo_telefone": phone8(s.get("international_phone_number"))
        == phone8(g.get("international_phone_number")),
        "mesmo_dominio": domain(s.get("website")) == domain(g.get("website")),
        "dominio": [domain(s.get("website")), domain(g.get("website"))],
    }


def pick_sample(rows: list[dict], n: int) -> list[int]:
    """Índices em round-robin por UF (com e sem match), na ordem do arquivo: amostra estável."""
    by_uf: dict[str, list[int]] = defaultdict(list)
    for i, r in enumerate(rows):
        if "details" in r or "no_match" in r:
            by_uf[r["input"].get("uf", "")].append(i)
    out: list[int] = []
    while len(out) < n and any(by_uf.values()):
        for uf in sorted(by_uf):
            if by_uf[uf] and len(out) < n:
                out.append(by_uf[uf].pop(0))
    return out


def ground_truth(rows: list[dict], n: int, path: pathlib.Path) -> list[dict]:
    """Reaproveita out/ground_truth.jsonl se já existe (Text Search + Details ≈ US$0,057).
    A comparação (cmp) é sempre recalculada contra o maps.jsonl atual."""
    if path.exists():
        gts = [json.loads(x) for x in path.open(encoding="utf-8") if x.strip()]
        for g in gts:
            s = rows[g["row"]].get("details")
            g["scraper_place_id"] = (s or {}).get("place_id")
            g["cmp"] = compare(s, g.get("gt_details"))
        return gts
    key = os.environ.get("BRAVE_PLACES_API_KEY")
    if not key:
        sys.exit("BRAVE_PLACES_API_KEY ausente na env")
    gts = []
    with httpx.Client(timeout=20) as client, path.open("w", encoding="utf-8") as fh:
        for i in pick_sample(rows, n):
            r = rows[i]
            inp = r["input"]
            gt = places_lookup(client, key, inp["nome"], inp["uf"])
            rec = {"row": i, "nome": inp["nome"], "municipio": inp.get("municipio"), "uf": inp["uf"],
                   "scraper_place_id": (r.get("details") or {}).get("place_id"),
                   "gt_place_id": (gt.get("gt_details") or {}).get("place_id"), **gt,
                   "cmp": compare(r.get("details"), gt.get("gt_details"))}
            gts.append(rec)
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            print(f"[gt] {inp['nome'][:40]:40s} -> {rec['gt_place_id']}", file=sys.stderr)
    return gts


# --------------------------------------------------------------------------- relatório

def report(rows: list[dict], gts: list[dict] | None) -> str:
    dets = [r["details"] for r in rows if r.get("details")]
    lines = [f"Entrada: {len(rows)} atrativos · com match no scraper: {pct(len(dets), len(rows))} · "
             f"sem match: {sum('no_match' in r for r in rows)} · erro: {sum('error' in r for r in rows)}"
             f" · bloqueio: {sum('blocked' in r for r in rows)}", ""]

    lat = [ms for r in rows for ms in r.get("latency_ms", [])]
    reqs = sum(r.get("requests", 0) for r in rows)
    status = defaultdict(int)
    for r in rows:
        for s in r.get("http", []):
            status[s] += 1
    lat_sorted = sorted(lat)
    lines += ["## Requisições ao Google Maps (scraper)", "", "| métrica | valor |", "|---|---|",
              f"| requisições | {reqs} |", f"| HTTP | {dict(status)} |",
              f"| latência média | {sum(lat) / len(lat):.0f} ms |" if lat else "| latência | - |",
              f"| latência p50 / p95 / máx | {lat_sorted[len(lat) // 2]} / "
              f"{lat_sorted[int(len(lat) * 0.95)]} / {lat_sorted[-1]} ms |" if lat else "", ""]

    lines += ["## Cobertura por campo (atrativos com match)", "", "| campo | scraper |", "|---|---|"]
    lines += [f"| {f} | {pct(sum(has(d, f) for d in dets), len(dets))} |" for f in FIELDS]

    verdicts = defaultdict(int)
    lines += ["", "## Gate do Brave por atrativo (scraper)", "",
              "| atrativo | UF | Maps | primary_type | _google_not_atrativo | _result_is_business |",
              "|---|---|---|---|---|---|"]
    for r in rows:
        d = r.get("details")
        if not d:
            continue
        v = gate(d)
        verdicts[v] += 1
        lines.append(f"| {r['input']['nome']} | {r['input']['uf']} | {d['name']} | "
                     f"{d.get('primary_type') or '-'} | {v} | {_result_is_business(d)} |")
    lines += ["", f"Veredito: {dict(verdicts)}", ""]

    if gts:
        both = [g for g in gts if g["cmp"]["ambos_match"]]
        c = lambda k: sum(1 for g in both if g["cmp"].get(k))  # noqa: E731
        dists = [g["cmp"]["dist_km"] for g in both if g["cmp"]["dist_km"] is not None]
        gdets = [g["gt_details"] for g in gts if g.get("gt_details")]
        sdets = [r["details"] for r in rows if r.get("details")
                 and r["details"]["place_id"] in {g["scraper_place_id"] for g in gts}]
        lines += [f"## Ground truth: Places API (New) oficial, {len(gts)} atrativos", "",
                  "| métrica | valor |", "|---|---|",
                  f"| match nos dois | {len(both)} |",
                  f"| só scraper / só Places / nenhum | {sum(g['cmp'].get('so_scraper', False) for g in gts)}"
                  f" / {sum(g['cmp'].get('so_places', False) for g in gts)}"
                  f" / {sum(not g['scraper_place_id'] and not g['gt_place_id'] for g in gts)} |",
                  f"| mesmo place_id | {pct(c('mesmo_place_id'), len(both))} |",
                  f"| distância média / máx | {sum(dists) / len(dists):.3f} / {max(dists):.3f} km |"
                  if dists else "| distância | - |",
                  f"| mesmo business_status | {pct(c('mesmo_status'), len(both))} |",
                  f"| mesmo veredito do gate | {pct(c('mesmo_gate'), len(both))} |",
                  f"| to_hours_map igual | {pct(c('mesmo_hours_map'), len(both))} |",
                  f"| telefone igual (8 dígitos) | {pct(c('mesmo_telefone'), len(both))} |",
                  f"| domínio do site igual | {pct(c('mesmo_dominio'), len(both))} |",
                  f"| requisições Places | {sum(g['gt_requests'] for g in gts)} |", "",
                  "Cobertura no mesmo recorte (atrativos com match em cada lado):", "",
                  "| campo | scraper | Places oficial |", "|---|---|---|"]
        lines += [f"| {f} | {pct(sum(has(d, f) for d in sdets), len(sdets))} | "
                  f"{pct(sum(has(d, f) for d in gdets), len(gdets))} |" for f in FIELDS]
        lines += ["", "| atrativo | UF | place_id | km | status S/P | gate S/P | horário | "
                  "review + recente S / P | tel | domínio S / P |", "|---|---|---|---|---|---|---|---|---|---|"]
        for g in gts:
            m = g["cmp"]
            if not m["ambos_match"]:
                lado = "só scraper" if m["so_scraper"] else "só Places" if m["so_places"] else "nenhum"
                lines.append(f"| {g['nome']} | {g['uf']} | {lado} | | | | | | | |")
                continue
            ok = lambda b: "=" if b else "≠"  # noqa: E731
            lines.append(
                f"| {g['nome']} | {g['uf']} | {ok(m['mesmo_place_id'])} | {m['dist_km']} | "
                f"{'/'.join(m['status'])} | {'/'.join(m['gate'])} | {ok(m['mesmo_hours_map'])} | "
                f"{m['review_mais_recente'][0]} / {m['review_mais_recente'][1]} | "
                f"{ok(m['mesmo_telefone'])} | {m['dominio'][0] or '-'} / {m['dominio'][1] or '-'} |")
    return "\n".join(lines) + "\n"


def selftest() -> None:
    assert phone8("+55 11 3149-5959") == phone8("(11) 3149-5959") == "31495959"
    assert domain("https://www.masp.org.br/x?y=1") == domain("http://masp.org.br") == "masp.org.br"
    assert km({"lat": -23.5614, "lng": -46.6559}, {"lat": -23.5614, "lng": -46.6559}) == 0.0
    rest = {"id": "ChIJx", "displayName": {"text": "MASP"}, "businessStatus": "OPERATIONAL",
            "types": ["art_museum", "museum", "point_of_interest"], "primaryType": "art_museum",
            "location": {"latitude": -23.56, "longitude": -46.65},
            "regularOpeningHours": {"weekdayDescriptions": [
                "segunda-feira: Fechado", "terça-feira: 10:00 – 20:00", "quarta-feira: 10:00 – 18:00",
                "quinta-feira: 10:00 – 18:00", "sexta-feira: 10:00 – 21:00",
                "sábado: 10:00 – 18:00", "domingo: 10:00 – 18:00"]},
            "reviews": [{"publishTime": "2026-08-15T12:00:00Z", "text": {"text": "ok"}}],
            "addressComponents": [{"longText": "São Paulo", "shortText": "SP",
                                   "types": ["administrative_area_level_2"]},
                                  {"longText": "São Paulo", "shortText": "SP",
                                   "types": ["administrative_area_level_1"]}],
            "priceLevel": "PRICE_LEVEL_UNSPECIFIED"}
    g = norm_details(rest)
    assert (g["municipio_nome"], g["uf_short"], g["price_level"]) == ("São Paulo", "SP", None)
    assert gate(g) == "passa" and has(g, "to_hours_map") and has(g, "reviews_publishTime")
    s = dict(g, weekday_text=[t.replace(" – ", "–") for t in g["weekday_text"]])  # forma do Maps
    m = compare(s, g)
    assert m["mesmo_place_id"] and m["mesmo_hours_map"] and m["mesmo_gate"], m
    assert gate(dict(g, primary_type="seafood_restaurant", types=["seafood_restaurant",
                                                                  "restaurant"])) == "descarta"
    assert compare(None, g) == {"ambos_match": False, "so_scraper": False, "so_places": True}
    print("selftest ok")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--in", dest="inp", default=str(OUT / "maps.jsonl"))
    ap.add_argument("--ground-truth", action="store_true", help="compara com Places API oficial")
    ap.add_argument("--n", type=int, default=20, help="tamanho da subamostra do ground truth")
    ap.add_argument("--gt-file", default=str(OUT / "ground_truth.jsonl"))
    ap.add_argument("--md", help="grava as tabelas neste arquivo além do stdout")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        selftest()
        return 0
    rows = [json.loads(x) for x in open(args.inp, encoding="utf-8") if x.strip()]
    gts = ground_truth(rows, args.n, pathlib.Path(args.gt_file)) if args.ground_truth else None
    text = report(rows, gts)
    print(text)
    if args.md:
        pathlib.Path(args.md).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
