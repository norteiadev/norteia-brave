"""POC: Google Maps scraper que devolve o MESMO dict de RealPlacesClient.place_details().

Por quê: medir se os endpoints internos do site do Maps (busca ``tbm=map`` + ``preview/place``)
entregam o que o Brave consome do Places API (New) — place_id, status, horário, reviews, tipos —
sem pagar SKU Enterprise+Atmosphere. Standalone (stdlib + httpx): não importa ``brave``, não toca
Postgres/Redis/norteia-api.

Regras: requisições ANÔNIMAS, UA fixo, sem cookies/proxy/captcha. Primeiro sinal de bloqueio
(429, redirect /sorry/, página de "unusual traffic"/captcha) para o loop e grava o motivo.

Respostas: ``)]}'`` + "\\n" + JSON posicional. O nó do lugar é ``data[6]`` no detalhe e
``data[0][1][i][14]`` na busca (mesma numeração). Mapa campo→posição e evidência no docstring de
``_place``; amostras cruas em out/raw/.

    uv run --frozen python poc/maps_scraper/maps_scraper.py --in in.jsonl --out out/maps.jsonl --limit 10
    uv run --frozen python poc/maps_scraper/maps_scraper.py --selftest
"""

from __future__ import annotations

import argparse
import base64
import difflib
import json
import math
import pathlib
import re
import sys
import time
import unicodedata
import urllib.parse
from datetime import datetime, timezone
from typing import Any

import httpx

HERE = pathlib.Path(__file__).resolve().parent
RAW = HERE / "out" / "raw"

# Um UA de navegador FIXO (sem rotação): o endpoint responde HTML de erro a UAs de biblioteca.
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0 Safari/537.36"
)
BRASIL_CENTER = (-14.2, -51.9)

SEARCH_URL = (
    "https://www.google.com/search?tbm=map&authuser=0&hl=pt-BR&gl=br&q={Q}"
    "&pb=!4m12!1m3!1d{D}!2d{LNG}!3d{LAT}!2m3!1f0!2f0!3f0!3m2!1i832!2i763!4f13.1!7i20!10b1"
    "!12m4!2m3!1i360!2i120!4i8!19m1!1e1"
)
DETAIL_URL = (
    "https://www.google.com/maps/preview/place?authuser=0&hl=pt-BR&gl=br&pb=!1m14!1s{FTID}"
    "!3m12!1m3!1d14863.829886985215!2d-40.2986506!3d-20.370168800000002!2m3!1f0!2f0!3f0!3m2"
    "!1i832!2i763!4f13.1!12m4!2m3!1i360!2i120!4i8!13m57!2m2!1i203!2i100!3m2!2i4!5b1!6m6!1m2"
    "!1i86!2i86!1m2!1i408!2i240!7m33!1m3!1e1!2b0!3e3!1m3!1e2!2b1!3e2!1m3!1e2!2b0!3e3!1m3!1e8"
    "!2b0!3e3!1m3!1e10!2b0!3e3!1m3!1e10!2b1!3e2!1m3!1e10!2b0!3e4!1m3!1e9!2b1!3e2!2b1!9b0!15m8"
    "!1m7!1m2!1m1!1e2!2m2!1i195!2i195!3i20!14m5!1swjPZarq-E4qz1sQP68aJ-As%3A34!2s1i%3A0%2Ct"
    "%3A150714%2Cp%3AwjPZarq-E4qz1sQP68aJ-As%3A34!7e81!12e3!17swjPZarq-E4qz1sQP68aJ-As%3A39"
    "!15m110!1m28!13m9!2b1!3b1!4b1!6i1!8b1!9b1!14b1!20b1!25b1!18m17!3b1!4b1!5b1!6b1!9b1!13b1"
    "!14b1!17b1!20b1!21b1!22b1!30b1!32b1!33m1!1b1!34b1!36e2!10m1!8e3!11m1!3e1!17b1!20m2!1e3"
    "!1e6!24b1!25b1!26b1!27b1!29b1!30m1!2b1!36b1!37b1!39m3!2m2!2i1!3i1!43b1!52b1!54m1!1b1!55b1"
    "!56m1!1b1!61m2!1m1!1e1!65m5!3m4!1m3!1m2!1i224!2i298!72m22!1m8!2b1!5b1!7b1!12m4!1b1!2b1"
    "!4m1!1e1!4b1!8m10!1m6!4m1!1e1!4m1!1e3!4m1!1e4"
    "!3sother_user_google_review_posts__and__hotel_and_vr_partner_review_posts!6m1!1e1!9b1"
    "!89b1!90m2!1m1!1e2!98m3!1b1!2b1!3b1!103b1!113b1!114m3!1b1!2m1!1b1!117b1!122m1!1b1!126b1"
    "!127b1!128m1!1b1!21m28!1m6!1m2!1i0!2i0!2m2!1i530!2i763!1m6!1m2!1i782!2i0!2m2!1i832!2i763"
    "!1m6!1m2!1i0!2i0!2m2!1i832!2i20!1m6!1m2!1i0!2i743!2m2!1i832!2i763!22m1!1e81!29m0!30m6"
    "!3b1!6m1!2b1!7m1!2b1!9b1!34m5!7b1!10b1!14b1!15m1!1b0!37i800&q={Q}"
)

UF_BY_STATE = {
    "acre": "AC", "alagoas": "AL", "amapa": "AP", "amazonas": "AM", "bahia": "BA", "ceara": "CE",
    "distrito federal": "DF", "espirito santo": "ES", "goias": "GO", "maranhao": "MA",
    "mato grosso": "MT", "mato grosso do sul": "MS", "minas gerais": "MG", "para": "PA",
    "paraiba": "PB", "parana": "PR", "pernambuco": "PE", "piaui": "PI", "rio de janeiro": "RJ",
    "rio grande do norte": "RN", "rio grande do sul": "RS", "rondonia": "RO", "roraima": "RR",
    "santa catarina": "SC", "sao paulo": "SP", "sergipe": "SE", "tocantins": "TO",
}

# Enum TYPE_* de [88][1] → tipo Places quando o nome difere (as 3 vistas nas amostras).
_ENUM_ALIAS = {
    "performing_arts": "performing_arts_theater",
    "shopping": "store",
    "tourist_destination": "tourist_attraction",
}
# Rótulo pt-BR de [13] → tipo Places. Só o que mexe no gate do Brave (_PLACE_TYPES e
# _NOT_ATRATIVO_PRIMARY_TYPES). ponytail: dicionário curto à mão; rótulo fora dele é ignorado
# (vai cru em raw_categories). Ampliar com o vocabulário real de um sweep de UF.
_CATEGORY_TYPES = {
    "atracao turistica": "tourist_attraction", "praia": "beach", "praia publica": "beach",
    "museu": "museum", "museu de arte": "art_museum", "museu de historia": "history_museum",
    "parque": "park", "parque nacional": "national_park", "parque estadual": "park",
    "parque municipal": "city_park", "igreja": "church", "igreja catolica": "church",
    "catedral": "church", "santuario": "place_of_worship", "teatro": "performing_arts_theater",
    "monumento": "monument", "marco historico": "historical_landmark",
    "mirante": "scenic_spot", "ilha": "island", "lago": "lake", "zoologico": "zoo",
    "aquario": "aquarium", "centro cultural": "cultural_center", "galeria de arte": "art_gallery",
    "area de trilhas": "hiking_area", "reserva natural": "nature_preserve",
    "parque de diversoes": "amusement_park",
    "restaurante": "restaurant", "bar": "bar", "pub": "pub", "cafe": "cafe", "cafeteria": "cafe",
    "padaria": "bakery", "hotel": "hotel", "pousada": "lodging", "loja": "store",
    "livraria": "book_store", "supermercado": "supermarket", "shopping center": "shopping_mall",
    "agencia de viagens": "travel_agency", "agencia de turismo": "tour_agency",
    "operadora de turismo": "tour_agency", "condominio": "condominium_complex",
    "academia": "gym", "spa": "spa",
}
# gcid de [209] que é área geográfica (bairro/município): marcado "political" como no Places,
# que _best_match do Brave descarta.
_GEO_PREFIXES = ("sublocality", "locality", "political", "administrative_area", "neighborhood",
                 "country", "postal_code")

_BLOCK_MARKERS = ("unusual traffic", "/sorry/", "captcha", "tráfego incomum")


class Blocked(RuntimeError):
    """Google sinalizou bloqueio: o loop inteiro para."""


def g(x: Any, *path: int) -> Any:
    """x[p0][p1]… tolerante: índice ausente ou nó não-lista → None."""
    for k in path:
        if not isinstance(x, list) or len(x) <= k:
            return None
        x = x[k]
    return x


def fold(text: str) -> str:
    nfd = unicodedata.normalize("NFD", (text or "").lower().strip())
    return nfd.encode("ascii", "ignore").decode()


def load(text: str) -> Any:
    if not text.startswith(")]}'"):
        raise ValueError("resposta sem prefixo )]}'")
    return json.loads(text[text.index("\n") + 1:])


def block_reason(status: int, location: str, body: str) -> str | None:
    """Motivo do bloqueio, ou None. Corpo só é inspecionado quando não é o JSON esperado."""
    if status == 429:
        return "http_429"
    if "/sorry/" in (location or ""):
        return "redirect_sorry"
    if not body.startswith(")]}'"):
        low = body[:20000].lower()
        for m in _BLOCK_MARKERS:
            if m in low:
                return f"body:{m}"
    return None


# --------------------------------------------------------------------------- parsing

def _pb_type(b64: Any) -> str | None:
    """[209] é um protobuf base64; o campo 18 (string) é o gcid do lugar no vocabulário do
    Places API (New): 'art_museum', 'beach', 'seafood_restaurant', 'sublocality1'…"""
    if not isinstance(b64, str):
        return None
    try:
        raw = base64.urlsafe_b64decode(b64.replace("+", "-").replace("/", "_") + "=" * (-len(b64) % 4))
        i = 0

        def varint() -> int:
            nonlocal i
            shift = val = 0
            while True:
                b = raw[i]
                i += 1
                val |= (b & 0x7F) << shift
                shift += 7
                if b < 0x80:
                    return val

        while i < len(raw):
            tag = varint()
            field, wire = tag >> 3, tag & 7
            if wire == 0:
                varint()
            elif wire == 2:
                n = varint()
                chunk = raw[i:i + n]
                i += n
                if field == 18:
                    return chunk.decode()
            elif wire == 1:
                i += 8
            elif wire == 5:
                i += 4
            else:
                return None
    except Exception:  # noqa: BLE001 — blob opaco; sem tipo é melhor que crash
        return None
    return None


def _municipio_uf(d: list) -> tuple[str, str]:
    """[183][1][3]=cidade; [166]="Cidade - UF" ou só o estado (praia: "Espírito Santo")."""
    city = g(d, 183, 1, 3) or ""
    region = g(d, 166) or ""
    m = re.match(r"^(.*?)\s+-\s+([A-Z]{2})$", region)
    if m:
        return city or m.group(1), m.group(2)
    state = g(d, 183, 1, 5) or region
    return city, UF_BY_STATE.get(fold(state), "")


def _types(d: list) -> tuple[list[str], str]:
    gcid = _pb_type(g(d, 209))
    enum = g(d, 88, 1)
    enum_t = ""
    if isinstance(enum, str) and ".TYPE_" in enum:
        enum_t = enum.split(".TYPE_", 1)[1].lower()
        enum_t = _ENUM_ALIAS.get(enum_t, enum_t)
    cats = [_CATEGORY_TYPES.get(fold(c)) for c in (g(d, 13) or [])]
    types = list(dict.fromkeys(t for t in (gcid, enum_t, *cats) if t))
    if gcid and gcid.startswith(_GEO_PREFIXES):
        types.append("political")
    return types, gcid or enum_t or next((c for c in cats if c), "")


def _place(d: list) -> dict[str, Any]:
    """Campos comuns ao nó de busca e de detalhe. Evidência (out/raw/):
    [78] place_id ChIJ (= Places id: MASP ChIJH3frsc5ZzpQRoU10aab2BOk) · [10] ftid ·
    [11] nome · [39] endereço sem nome (≈ formattedAddress; [18] = nome + endereço) ·
    [9][2],[9][3] lat,lng · [13] categorias pt-BR · [88][1] enum TYPE_* · [209] gcid ·
    [4][7] nota · [4][8] nº avaliações · [23]==1 fechado (Saraiva Sé, Fnac, quiosque K3) ·
    [183][1] [bairro, rua, rua, cidade, CEP, estado, país] · [166] "Cidade - UF"."""
    types, primary = _types(d)
    municipio, uf = _municipio_uf(d)
    return {
        "ftid": g(d, 10) or "",
        "place_id": g(d, 78) or "",
        "name": g(d, 11) or "",
        "formatted_address": g(d, 39) or g(d, 18) or "",
        "location": {"lat": g(d, 9, 2), "lng": g(d, 9, 3)},
        "types": types,
        "primary_type": primary,
        "raw_categories": g(d, 13) or [],
        "municipio_nome": municipio,
        "uf_short": uf,
        "rating": g(d, 4, 7),
        "user_rating_count": g(d, 4, 8),
        "closed_flag": g(d, 23) == 1,
    }


def parse_search(text: str) -> list[dict[str, Any]]:
    """data[0][1][i][14]: com 1 hit o próprio lugar vem em [0]; com vários, [0] é meta."""
    items = g(load(text), 0, 1) or []
    return [_place(it[14]) for it in items if isinstance(g(it, 14), list) and g(it, 14, 10)]


def _weekday_text(d: list) -> list[str]:
    """[203][0] = 7 dias [nome, 1=seg..7=dom, [a,m,d], [[texto, …], …], ?, 1 aberto/2 fechado].
    Semana CORRENTE (datas reais), não regularOpeningHours: feriado da semana entra como é."""
    days = g(d, 203, 0) or []
    out = []
    for day in sorted(days, key=lambda x: g(x, 1) or 0):
        shifts = [s[0] for s in (g(day, 3) or []) if g(s, 0)]
        if g(day, 0) and shifts:
            out.append(f"{day[0]}: {', '.join(shifts)}")
    return out if len(out) == 7 else []


def _business_status(d: list) -> str:
    """[23]==1 nos 3 fechados de verdade vistos (sem horário em [203]); "temporariamente" no
    selo [88][0] ou na linha de status [203][1][4][0] → temporário (NÃO observado; ver gaps).
    Palavra inteira: "tempor" casava "contemporânea" no selo do Inhotim."""
    badge = g(d, 88, 0) if isinstance(g(d, 88, 0), str) else ""
    status_line = g(d, 203, 1, 4, 0) if isinstance(g(d, 203, 1, 4, 0), str) else ""
    if "temporariamente" in fold(badge) or "temporariamente" in fold(status_line):
        return "CLOSED_TEMPORARILY"
    return "CLOSED_PERMANENTLY" if g(d, 23) == 1 else "OPERATIONAL"


def _reviews(d: list) -> list[dict[str, Any]]:
    """[175][9][0][0][i][0]: [1][2] criação em µs · [2][0][0] nota · [2][15][0][0] texto."""
    out = []
    for r in g(d, 175, 9, 0, 0) or []:
        node = g(r, 0)
        us = g(node, 1, 2)
        out.append({
            "publishTime": datetime.fromtimestamp(us / 1e6, tz=timezone.utc).isoformat()
            if isinstance(us, int) else None,
            "rating": g(node, 2, 0, 0),
            "text": g(node, 2, 15, 0, 0) or "",
        })
    return out


def _phone(d: list) -> str | None:
    """[178][0][1] = [[formato, tipo]]; tipo 2 = internacional "+55 11 3149-5959"."""
    for fmt in g(d, 178, 0, 1) or []:
        if g(fmt, 1) == 2:
            return fmt[0]
    return None


def _website(d: list) -> str | None:
    url = g(d, 7, 0)
    if isinstance(url, str) and url.startswith("/url?"):
        url = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).get("q", [None])[0]
    return url or None


def parse_details(text: str, fallback_primary: str = "") -> dict[str, Any]:
    """data[6] no shape de RealPlacesClient.place_details() + extras do POC."""
    d = load(text)[6]
    base = _place(d)
    if not g(d, 209) and fallback_primary:  # Camburi: detalhe sem [209], busca com
        base["primary_type"] = fallback_primary
        base["types"] = list(dict.fromkeys([fallback_primary, *base["types"]]))
    return {
        "place_id": base["place_id"],
        "name": base["name"],
        "formatted_address": base["formatted_address"],
        "international_phone_number": _phone(d),
        "website": _website(d),
        "business_status": _business_status(d),
        "weekday_text": _weekday_text(d),
        "reviews": _reviews(d),
        "location": base["location"],
        "distrito_hint": "",
        "price_level": None,  # sem nível de preço do lugar nas respostas (ver gaps)
        "types": base["types"],
        "primary_type": base["primary_type"],
        "municipio_nome": base["municipio_nome"],
        "uf_short": base["uf_short"],
        "rating": base["rating"],
        "user_rating_count": base["user_rating_count"],
        "ftid": base["ftid"],
        "raw_categories": base["raw_categories"],
    }


# --------------------------------------------------------------------------- match

def token_set_ratio(a: str, b: str) -> float:
    """rapidfuzz.token_set_ratio com difflib (stdlib): ≈ o score que o _best_match usa."""
    ta, tb = set(fold(a).split()), set(fold(b).split())
    if not ta or not tb:
        return 0.0
    inter = " ".join(sorted(ta & tb))
    da, db = " ".join(sorted(ta - tb)), " ".join(sorted(tb - ta))
    if inter and (not da or not db):
        return 100.0
    sa, sb = f"{inter} {da}".strip(), f"{inter} {db}".strip()
    r = lambda x, y: 100 * difflib.SequenceMatcher(None, x, y, autojunk=False).ratio()  # noqa: E731
    return max(r(inter, sa), r(inter, sb), r(sa, sb))


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 6371.0 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def best_match(cands: list[dict], nome: str, lat: float | None, lng: float | None,
               municipio: str = "", uf: str = "", max_km: float = 20.0) -> dict | None:
    """Espelho enxuto do _best_match do Brave: sem área geográfica, nome ≥85 (token_set),
    ≤max_km das coords do TA; sem coords, mesma UF. O município do TA só DESEMPATA: o Brave não
    exige igualdade (TA diz Vargem Alta, Google diz Domingos Martins para a Pedra Azul)."""
    passers = []
    for c in cands:
        if "political" in c["types"]:
            continue
        score = token_set_ratio(nome, c["name"])
        if score < 85:
            continue
        clat, clng = c["location"]["lat"], c["location"]["lng"]
        if lat is not None and lng is not None:
            if clat is not None and haversine_km(lat, lng, clat, clng) > max_km:
                continue
        else:
            if uf and c["uf_short"] and c["uf_short"] != uf.upper():
                continue
        same_mun = bool(municipio) and fold(municipio) == fold(c["municipio_nome"])
        passers.append(((same_mun, score), c))
    return max(passers, key=lambda p: p[0])[1] if passers else None


# --------------------------------------------------------------------------- HTTP

class Session:
    """Um httpx.Client anônimo + espaçamento entre requisições + contadores por linha."""

    def __init__(self, delay: float):
        self.client = httpx.Client(headers={"User-Agent": UA, "Accept-Language": "pt-BR,pt;q=0.9"},
                                   timeout=20, follow_redirects=False)
        self.delay = delay
        self._last = 0.0
        self.log: list[dict] = []

    def get(self, url: str) -> str:
        wait = self._last + self.delay - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        t0 = time.monotonic()
        r = self.client.get(url)
        self._last = time.monotonic()
        self.log.append({"status": r.status_code, "ms": int((self._last - t0) * 1000)})
        reason = block_reason(r.status_code, r.headers.get("location", ""), r.text)
        if reason:
            raise Blocked(reason)
        r.raise_for_status()
        return r.text


def search(s: Session, query: str, lat: float | None, lng: float | None) -> list[dict]:
    has = lat is not None and lng is not None
    clat, clng = (lat, lng) if has else BRASIL_CENTER
    url = SEARCH_URL.format(Q=urllib.parse.quote(query), D=14863.8 if has else 4_000_000,
                            LAT=clat, LNG=clng)
    return parse_search(s.get(url))


def details(s: Session, ftid: str, query: str, fallback_primary: str = "") -> dict:
    url = DETAIL_URL.format(FTID=urllib.parse.quote(ftid, safe=""), Q=urllib.parse.quote(query))
    return parse_details(s.get(url), fallback_primary)


def run(args: argparse.Namespace) -> int:
    rows = [json.loads(line) for line in open(args.inp, encoding="utf-8") if line.strip()]
    if args.limit:
        rows = rows[: args.limit]
    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    s = Session(args.delay)
    blocked = None
    with out.open("w", encoding="utf-8") as fh:
        for row in rows:
            nome = row.get("nome") or row.get("name") or ""
            municipio, uf = row.get("municipio") or "", row.get("uf") or ""
            lat, lng = row.get("lat"), row.get("lng")
            query = ", ".join(p for p in (nome, f"{municipio} {uf}".strip()) if p)
            start = len(s.log)
            rec: dict[str, Any] = {"input": row, "query": query}
            try:
                cands = search(s, query, lat, lng)
                rec["candidates"] = len(cands)
                match = best_match(cands, nome, lat, lng, municipio, uf)
                if match:
                    rec["details"] = details(s, match["ftid"], query, match["primary_type"])
                else:
                    rec["no_match"] = [{k: c[k] for k in ("name", "types", "municipio_nome", "uf_short")}
                                       for c in cands[:5]]
            except Blocked as exc:
                blocked = rec["blocked"] = str(exc)
            except Exception as exc:  # noqa: BLE001 — uma linha ruim não derruba o sweep
                rec["error"] = f"{type(exc).__name__}: {exc}"[:300]
            calls = s.log[start:]
            rec["http"] = [c["status"] for c in calls]
            rec["latency_ms"] = [c["ms"] for c in calls]
            rec["requests"] = len(calls)
            rec["requests_total"] = len(s.log)
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            d = rec.get("details") or {}
            print(f"[{len(s.log):3d}] {nome[:40]:40s} http={rec['http']} ms={rec['latency_ms']} "
                  f"-> {d.get('name') or rec.get('blocked') or rec.get('error') or 'sem match'}",
                  flush=True)
            if blocked:
                print(f"BLOQUEADO ({blocked}) após {len(s.log)} requisições — parando.", flush=True)
                return 2
    return 0


# --------------------------------------------------------------------------- selftest

def selftest() -> None:
    raw = lambda name: (RAW / name).read_text(encoding="utf-8")  # noqa: E731

    cands = parse_search(raw("search_praias_vitoria.txt"))
    assert len(cands) == 20 and all(c["ftid"].startswith("0x") for c in cands), len(cands)
    assert all(c["place_id"].startswith("ChIJ") for c in cands)
    canto = next(c for c in cands if c["place_id"] == "ChIJoc7O6ekXuAARe39-WoE4NdQ")
    assert "political" in canto["types"], canto  # bairro Praia do Canto
    hit = best_match(cands, "Praia de Camburi", -20.2801, -40.2868)
    assert hit and hit["name"] == "Praia de Camburi" and "beach" in hit["types"], hit
    assert best_match(cands, "Praia de Camburi", -23.56, -46.65) is None  # 700 km: outra cidade
    a, b = (dict(hit, place_id=k, municipio_nome=m) for k, m in (("A", "Serra"), ("B", "Vitória")))
    assert best_match([a, b], "Praia de Camburi", None, None, "Vitoria", "ES")["place_id"] == "B"
    assert best_match([a], "Praia de Camburi", None, None, "Vitoria", "ES")["place_id"] == "A"

    masp = parse_details(raw("detail_masp.txt"))
    assert masp["place_id"] == "ChIJH3frsc5ZzpQRoU10aab2BOk"
    assert masp["business_status"] == "OPERATIONAL"
    assert masp["primary_type"] == "art_museum"
    assert {"museum", "tourist_attraction"} <= set(masp["types"]), masp["types"]
    assert masp["weekday_text"][0] == "segunda-feira: Fechado", masp["weekday_text"]
    assert masp["weekday_text"][1] == "terça-feira: 10:00–20:00"
    assert masp["international_phone_number"] == "+55 11 3149-5959"
    assert masp["website"] == "https://masp.com.br/pt-br"
    assert (masp["municipio_nome"], masp["uf_short"]) == ("São Paulo", "SP")
    assert masp["formatted_address"].startswith("Av. Paulista, 1578")
    assert len(masp["reviews"]) == 5 and all(r["rating"] in (1, 2, 3, 4, 5) for r in masp["reviews"])
    assert masp["reviews"][0]["publishTime"].startswith("2026-08-15"), masp["reviews"][0]
    assert masp["reviews"][0]["text"].startswith("Conheci o Masp")
    assert masp["rating"] == 4.7 and masp["user_rating_count"] == 112315

    coco = parse_details(raw("detail_cocobambu.txt"))
    assert coco["primary_type"] == "seafood_restaurant" and "restaurant" in coco["types"]
    assert "segunda-feira: 11:30–15:00, 17:00–22:30" in coco["weekday_text"], coco["weekday_text"]
    assert (coco["municipio_nome"], coco["uf_short"]) == ("Vitória", "ES")

    closed = parse_details(raw("detail_saraiva_closed.txt"))
    assert closed["business_status"] == "CLOSED_PERMANENTLY" and closed["weekday_text"] == []
    assert closed["primary_type"] == "book_store"

    inhotim = parse_details(raw("detail_inhotim.txt"))  # selo "…arte contemporânea"
    assert inhotim["business_status"] == "OPERATIONAL", inhotim["business_status"]

    theatro = parse_details(raw("detail_theatro.txt"))
    assert theatro["primary_type"] == "performing_arts_theater" and theatro["weekday_text"] == []

    camburi = parse_details(raw("detail_camburi.txt"), fallback_primary="beach")
    assert camburi["primary_type"] == "beach" and camburi["uf_short"] == "ES"
    assert camburi["municipio_nome"] == "" and camburi["weekday_text"] == []

    assert block_reason(429, "", "") == "http_429"
    assert block_reason(302, "https://www.google.com/sorry/index?continue=x", "") == "redirect_sorry"
    assert block_reason(200, "", "<html>Our systems have detected unusual traffic") == "body:unusual traffic"
    assert block_reason(200, "", raw("search_masp.txt")) is None
    assert token_set_ratio("Museu de Arte de São Paulo", "Museu de Arte de São Paulo Assis Chateaubriand") == 100

    # Compatibilidade real com o conversor do Brave (módulo puro, sem conexão).
    try:
        sys.path.insert(0, str(HERE.parents[1]))
        from brave.shared.opening_hours import to_hours_map
    except ImportError:
        print("aviso: brave.shared.opening_hours indisponível; pulei o check de horário")
    else:
        assert to_hours_map(masp["weekday_text"]) == {
            "mon": "closed", "tue": "10:00-20:00", "wed_thu": "10:00-18:00",
            "fri": "10:00-21:00", "sat_sun": "10:00-18:00",
        }, to_hours_map(masp["weekday_text"])
        assert to_hours_map(coco["weekday_text"]) is not None
    print("selftest ok")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--in", dest="inp", help="JSONL do ta_sweep (nome, municipio, uf, lat?, lng?)")
    ap.add_argument("--out", default=str(HERE / "out" / "maps_details.jsonl"))
    ap.add_argument("--delay", type=float, default=1.5, help="segundos entre requisições")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        selftest()
        return 0
    if not args.inp:
        ap.error("--in é obrigatório (ou --selftest)")
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
