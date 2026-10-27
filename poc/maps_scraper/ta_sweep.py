#!/usr/bin/env python3
"""TripAdvisor sweep standalone para a POC "scraper do Google Maps no lugar da Places API".

Por quê: a POC precisa de uma lista de atrativos (nome + município + UF) para alimentar
o scraper do Maps SEM tocar no Brave (sem Redis/Postgres/Celery, sem importar `brave`).
Replica o mínimo de brave/domains/tripadvisor/client.py (GraphQL AttractionsFusion
paginado por `pagee`, ou o HTML SSR `-oa{offset}-` com a ilha JSON FlexCard) e o
parse dos cards. A sessão vem de --curl-file ("Copy as cURL" do DevTools): cookies e
User-Agent são lidos só em memória e NUNCA impressos. Anônimo = 403 DataDome, então
sem cURL o único caminho é --from-pilot (lista do piloto de 100 atrativos).

O que o listing expõe por card: locationId, nome, categoria (primaryInfo), rating,
review_count e o webLinkUrl — município/UF saem do slug da URL (ASCII, às vezes
"distrito + município"). Lat/lng NÃO vêm no listing (o Brave geocodifica depois).

  uv run python poc/maps_scraper/ta_sweep.py --selftest
  uv run python poc/maps_scraper/ta_sweep.py --from-pilot
  uv run python poc/maps_scraper/ta_sweep.py --uf ES --max-pages 2 --curl-file ~/ta.curl
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import unicodedata
import uuid
from pathlib import Path
from urllib.parse import unquote

import httpx

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
OUT_DEFAULT = HERE / "out" / "ta_atrativos.jsonl"
PILOT = REPO / "docs" / "poc" / "pilot-100" / "atrativos.json"
UF_GEOIDS = REPO / "data" / "tripadvisor" / "uf_geoids.json"
FIXTURE = REPO / "tests" / "fixtures" / "tripadvisor" / "attractions_oa30.html"

TA_BASE = "https://www.tripadvisor.com.br"
GQL_URL = f"{TA_BASE}/data/graphql/ids"
# .com no SSR, igual ao Brave (o template do client.py); nomes saem em inglês nesse host.
HTML_URL = "https://www.tripadvisor.com/Attractions-g{geo_id}-Activities-a_allAttractions.true-oa{offset}-Brazil.html"
LISTING_QID = "0e26761de5110a17"  # client.py _LISTING_QID_GQL (TA rotaciona: --qid)
FLEXCARD = "WebPresentation_SingleFlexCardSection"
PAGE_SIZE, MAX_PAGE = 30, 334  # TA corta o listing em 10000 resultados (oa9990)
MIN_SLEEP = 3.0
ACCEPT_LANGUAGE = "pt-BR,pt;q=0.9,en;q=0.8"

# uf_names.py (_TA_STATE_CANONICAL), chave ASCII minúscula.
STATE_TO_UF = {
    "acre": "AC", "alagoas": "AL", "amapa": "AP", "amazonas": "AM", "bahia": "BA",
    "ceara": "CE", "distrito federal": "DF", "federal district": "DF",
    "espirito santo": "ES", "goias": "GO", "maranhao": "MA", "mato grosso": "MT",
    "mato grosso do sul": "MS", "minas gerais": "MG", "para": "PA", "paraiba": "PB",
    "parana": "PR", "pernambuco": "PE", "piaui": "PI", "rio de janeiro": "RJ",
    "rio grande do norte": "RN", "rio grande do sul": "RS", "rondonia": "RO",
    "roraima": "RR", "santa catarina": "SC", "sao paulo": "SP", "sergipe": "SE",
    "tocantins": "TO",
}
# Último segmento do slug: "{Cidade}_State_of_{Estado}" ou "{Cidade}_Federal_District".
_LOC_SLUG = re.compile(
    r"^(?P<city>.+?)_(?:(?:State_of|Estado_d[aeo])_(?P<state>.+)|(?P<df>Federal_District|Distrito_Federal))$"
)


class Blocked(Exception):
    """403/429/captcha — o loop para na hora (sem retry, sem bypass)."""


# --------------------------------------------------------------------------- sessão


def parse_curl(text: str) -> tuple[dict[str, str], str]:
    """(cookies, user_agent) de um "Copy as cURL" — mesma regex de scripts/ta_bootstrap.py."""
    m = re.search(r"""-H\s+\$?['"]Cookie:\s*([^'"]+)['"]""", text, re.IGNORECASE) or re.search(
        r"""(?:-b|--cookie)\s+\$?['"]([^'"]+)['"]""", text
    )
    cookies: dict[str, str] = {}
    for pair in (m.group(1) if m else "").split(";"):
        name, sep, value = pair.strip().partition("=")
        if sep and name:
            cookies[name.strip()] = value.strip()
    ua = re.search(r"""-H\s+\$?['"]User-Agent:\s*([^'"]+)['"]""", text, re.IGNORECASE) or re.search(
        r"""(?:-A|--user-agent)\s+\$?['"]([^'"]+)['"]""", text
    )
    return cookies, ua.group(1).strip() if ua else ""


# --------------------------------------------------------------------------- parse


def _fold(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower().strip()


def location_from_url(web_link_url: str) -> tuple[str | None, str | None]:
    """(municipio, uf) do slug ".../Reviews-Nome-Cidade_State_of_Estado.html".

    ponytail: slug ASCII sem acento e às vezes "Distrito_Municipio" (Trancoso_Porto_Seguro);
    o Brave resolve isso com a query geo d3d4987463b78a39 + IBGE — aqui fica aproximado.
    """
    last = web_link_url.rsplit("-", 1)[-1].removesuffix(".html")
    m = _LOC_SLUG.match(last)
    if not m:
        return None, None
    state = "federal district" if m["df"] else m["state"].replace("_", " ")
    return m["city"].replace("_", " "), STATE_TO_UF.get(_fold(state))


def _find_flexcards(node, acc: list[dict], depth: int = 0) -> None:
    """Coleta seções FlexCard; string que ainda carrega o marcador é JSON aninhado."""
    if depth > 40:
        return
    if isinstance(node, dict):
        if node.get("__typename") == FLEXCARD:
            acc.append(node)
        for v in node.values():
            _find_flexcards(v, acc, depth + 1)
    elif isinstance(node, list):
        for v in node:
            _find_flexcards(v, acc, depth + 1)
    elif isinstance(node, str) and FLEXCARD in node:
        try:
            _find_flexcards(json.loads(node), acc, depth + 1)
        except ValueError:
            pass


def sections_from_html(html: str) -> list[dict]:
    """Ilha JSON FlexCard do SSR (data: URI percent-encoded, escapes aninhados)."""
    i = html.find(FLEXCARD)
    if i == -1:
        return []
    start, end = html.rfind("<script", 0, i), html.find("</script>", i)
    if start == -1 or end == -1:
        return []
    blob = html[start:end]
    src = blob.find('src="')
    decoded = unquote(blob[src + 5:] if src != -1 else blob)
    literals = [m.group(0) for m in re.finditer(r'"((?:[^"\\]|\\.)*)"', decoded, re.DOTALL) if FLEXCARD in m.group(0)]
    if not literals:
        return []
    try:
        peeled = json.loads(max(literals, key=len))
    except ValueError:
        return []
    acc: list[dict] = []
    _find_flexcards(peeled, acc)
    return acc


def card_to_row(section: dict, uf_hint: str | None) -> dict | None:
    """Seção FlexCard → linha JSONL. None se não tiver detailId (card de produto/anúncio)."""
    card = section.get("singleFlexCardContent")
    if not isinstance(card, dict):
        return None
    route = ((card.get("cardLink") or {}).get("webRoute") or {})
    loc_id = (route.get("typedParams") or {}).get("detailId")
    if loc_id is None:
        return None
    link = route.get("webLinkUrl") or ""
    municipio, uf = location_from_url(link)
    bubble = card.get("bubbleRating") or {}
    return {
        "ta_location_id": int(loc_id),
        "canonical_key": f"tripadvisor:attraction:{int(loc_id)}",
        "nome": (card.get("cardTitle") or {}).get("text") or "",
        "categoria": (card.get("primaryInfo") or {}).get("text") or "",
        "bairro": (card.get("secondaryInfo") or {}).get("text"),
        "municipio": municipio,
        "uf": uf or uf_hint,
        "lat": None,  # o listing não traz coordenadas
        "lng": None,
        "rating": bubble.get("rating"),
        "review_count": bubble.get("reviewCount"),
        "url": f"{TA_BASE}{link}" if link.startswith("/") else None,
        "fonte": "ta_listing",
    }


def rows_from_sections(sections: list[dict], uf_hint: str | None) -> list[dict]:
    rows = [r for s in sections if (r := card_to_row(s, uf_hint))]
    return list({r["ta_location_id"]: r for r in rows}.values())  # SSR repete cards às vezes


# --------------------------------------------------------------------------- fetch


def _check(resp: httpx.Response) -> None:
    if resp.status_code in (403, 429) or "captcha-delivery.com" in resp.text[:20000]:
        raise Blocked(f"HTTP {resp.status_code} (DataDome/captcha)")
    resp.raise_for_status()


def _gql_payload(geo_id: int, offset: int, session_id: str, qid: str) -> list:
    tracking = {"screenName": "AttractionsFusion", "pageviewUid": str(uuid.uuid4())}
    return [{
        "variables": {
            "request": {
                "tracking": tracking,
                "routeParameters": {
                    "geoId": geo_id, "filters": [], "contentType": "attraction",
                    "webVariant": "AttractionsFusion", "pagee": str(offset),  # pagee="0" explícito
                },
                "updateToken": None,
            },
            "commerce": {"attractionCommerce": {"pax": [{"ageBand": "ADULT", "count": 2}]}},
            "tracking": tracking, "sessionId": session_id, "unitLength": "KILOMETERS",
            "currency": "BRL", "currentGeoPoint": None, "mapSurface": False,
            "debug": False, "polling": False,
        },
        "extensions": {"preRegisteredQueryId": qid},
    }]


def sweep(geo_id: int, uf: str | None, max_pages: int, cookies: dict, ua: str,
          transport: str, qid: str, sleep_s: float) -> tuple[list[dict], dict]:
    """Pagina até max_pages; para no primeiro bloqueio, página vazia ou fim do listing."""
    rows: list[dict] = []
    log = {"geo_id": geo_id, "uf": uf, "transport": transport, "requests": 0, "pages": 0, "stop": "max_pages"}
    headers = {"Accept-Language": ACCEPT_LANGUAGE, "User-Agent": ua}
    total: int | None = None
    with httpx.Client(cookies=cookies, headers=headers, follow_redirects=True, timeout=30) as hc:
        for page in range(1, min(max_pages, MAX_PAGE) + 1):
            offset = (page - 1) * PAGE_SIZE
            if total is not None and offset >= total:
                log["stop"] = "end_of_listing"
                break
            if page > 1:
                time.sleep(sleep_s)
            log["requests"] += 1
            t0 = time.monotonic()
            try:
                if transport == "gql":
                    resp = hc.post(GQL_URL, json=_gql_payload(geo_id, offset, cookies.get("TASID", ""), qid),
                                   headers={"Content-Type": "application/json"})
                    _check(resp)
                    data = resp.json()
                    first = data[0] if isinstance(data, list) and data else {}
                    if first.get("errors"):
                        log["stop"] = f"graphql_errors (qid rotacionado?): {first['errors'][:1]}"
                        break
                    result0 = ((first.get("data") or {}).get("Result") or [{}])[0] or {}
                    total = result0.get("totalResults") if isinstance(result0.get("totalResults"), int) else total
                    sections = result0.get("sections") or []
                else:
                    resp = hc.get(HTML_URL.format(geo_id=geo_id, offset=offset),
                                  headers={"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"})
                    _check(resp)
                    sections = sections_from_html(resp.text)
            except Blocked as exc:
                log["stop"] = f"blocked: {exc}"
                break
            except (httpx.HTTPError, ValueError) as exc:
                log["stop"] = f"error: {type(exc).__name__}"
                break
            page_rows = rows_from_sections(sections, uf)
            print(f"[ta] page={page} offset={offset} cards={len(page_rows)} {time.monotonic() - t0:.1f}s", file=sys.stderr)
            if not page_rows:
                log["stop"] = "empty_page"
                break
            rows.extend(page_rows)
            log["pages"] += 1
    log["rows"] = len(rows)
    return rows, log


# --------------------------------------------------------------------------- pilot


def from_pilot(path: Path = PILOT) -> list[dict]:
    """Piloto de 100 → mesmo schema (sem cookie). 19 são `places:*`, sem ta_location_id."""
    rows = []
    for a in json.loads(path.read_text(encoding="utf-8")):
        key = a.get("canonical_key") or ""
        m = re.fullmatch(r"tripadvisor:attraction:(\d+)", key)
        rows.append({
            "ta_location_id": int(m.group(1)) if m else None,
            "canonical_key": key,
            "nome": a.get("nome") or "",
            "categoria": None,  # o piloto não guarda a categoria do TA
            "bairro": None,
            "municipio": a.get("municipio"),
            "uf": a.get("uf"),
            "lat": None,
            "lng": None,
            "rating": None,
            "review_count": None,
            "url": None,  # sem o slug não dá para montar a URL canônica do TA
            "fonte": "pilot-100",
        })
    return rows


# --------------------------------------------------------------------------- main


def selftest() -> None:
    html = FIXTURE.read_text(encoding="utf-8")
    rows = rows_from_sections(sections_from_html(html), None)
    assert len(rows) == 30, len(rows)
    for r in rows:
        assert isinstance(r["ta_location_id"], int) and r["ta_location_id"] > 0, r
        assert r["nome"] and r["categoria"] and r["municipio"], r
        assert r["uf"] in STATE_TO_UF.values(), r
        assert r["url"].startswith(f"{TA_BASE}/Attraction_Review-"), r
        assert isinstance(r["review_count"], int), r
    lage = next(r for r in rows if r["ta_location_id"] == 311472)
    assert (lage["nome"], lage["municipio"], lage["uf"], lage["review_count"]) == (
        "Parque Lage", "Rio de Janeiro", "RJ", 5924), lage
    assert location_from_url("/Attraction_Review-g1-d2-Reviews-Pontao-Brasilia_Federal_District.html") == ("Brasilia", "DF")
    assert location_from_url("/Attraction_Review-g1-d2-Reviews-X-Trancoso_Porto_Seguro_State_of_Bahia.html") == (
        "Trancoso Porto Seguro", "BA")
    assert location_from_url("/garbage") == (None, None)
    assert sections_from_html("<html>nada</html>") == []
    c, ua = parse_curl("curl 'https://x' -H 'User-Agent: Moz/5' -H 'Cookie: datadome=abc; TASID=t1=x'")
    assert c == {"datadome": "abc", "TASID": "t1=x"} and ua == "Moz/5", (c, ua)
    assert parse_curl("curl 'https://x' -b 'a=1' -A 'UA'") == ({"a": "1"}, "UA")
    pilot = from_pilot()
    assert len(pilot) == 100 and all(p["nome"] and p["uf"] for p in pilot)
    print("selftest ok: fixture 30 cards, slug/UF, cURL, pilot 100")


def write_jsonl(rows: list[dict], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--geo-id", type=int)
    p.add_argument("--uf", help="usa data/tripadvisor/uf_geoids.json para o geoId")
    p.add_argument("--max-pages", type=int, default=2)
    p.add_argument("--curl-file", type=Path, help='arquivo com o "Copy as cURL" de uma página do TA')
    p.add_argument("--transport", choices=("gql", "html"), default="gql")
    p.add_argument("--qid", default=LISTING_QID, help="preRegisteredQueryId do listing (se rotacionou)")
    p.add_argument("--sleep", type=float, default=MIN_SLEEP, help=f"pausa entre páginas (mín {MIN_SLEEP}s)")
    p.add_argument("--out", type=Path, default=OUT_DEFAULT)
    p.add_argument("--from-pilot", action="store_true", help="emite o piloto de 100 (sem rede)")
    p.add_argument("--selftest", action="store_true")
    a = p.parse_args()

    if a.selftest:
        selftest()
        return 0
    if a.from_pilot:
        rows = from_pilot()
        write_jsonl(rows, a.out)
        print(f"[ta] from-pilot: {len(rows)} linhas → {a.out}", file=sys.stderr)
        return 0
    if not a.curl_file:
        print("Sem --curl-file: o TripAdvisor anônimo responde 403 (DataDome). Passe --curl-file "
              "com o 'Copy as cURL' do browser, ou use --from-pilot.", file=sys.stderr)
        return 2
    uf = a.uf.upper() if a.uf else None
    geo_id = a.geo_id or (json.loads(UF_GEOIDS.read_text()).get(uf) if uf else None)
    if not isinstance(geo_id, int):
        print("Informe --geo-id ou um --uf válido.", file=sys.stderr)
        return 2
    cookies, ua = parse_curl(a.curl_file.expanduser().read_text(encoding="utf-8"))
    if not cookies or not ua:
        print("cURL sem Cookie ou User-Agent — recopie do DevTools.", file=sys.stderr)  # nunca os valores
        return 2
    rows, log = sweep(geo_id, uf, a.max_pages, cookies, ua, a.transport, a.qid, max(a.sleep, MIN_SLEEP))
    write_jsonl(rows, a.out)
    print(f"[ta] {json.dumps(log, ensure_ascii=False)} → {a.out}", file=sys.stderr)
    return 0 if rows else 1


if __name__ == "__main__":
    sys.exit(main())
