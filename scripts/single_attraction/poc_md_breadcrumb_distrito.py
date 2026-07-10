#!/usr/bin/env python3
"""POC — can the Melhores Destinos (MD) breadcrumb assign an attraction to an IBGE distrito?

The TripAdvisor lane localizes an attraction only to município (no sub-município text).
MD attraction pages carry a breadcrumb:

    Guia MD → Brasil → <Region> → <State> → <Place> → <Attraction>
                                              └─ município OR distrito (flattened) ─┘

MD does NOT encode the município→distrito hierarchy (Arraial d'Ajuda sits directly under
Bahia, as a peer of Porto Seguro). The IBGE CSV supplies the hierarchy: cross the <Place>
name against ibge_distritos.csv → distrito_code + parent município. That parent is then
cross-checked against TripAdvisor's município — three-source agreement = high confidence.

This POC, for a set of attractions:
  1. Uses the real MD client to find the attraction's MD page (find_attraction_url).
  2. Fetches the page HTML and parses id="breadcrumbs" (stdlib regex, house style).
  3. Crosses the breadcrumb <Place> against ibge_distritos.csv (UF-scoped) AND
     ibge_municipios.csv, and validates the distrito's parent município against the
     TripAdvisor município passed in — reporting the distrito assignment + verdict.

Run:
  set -a; . ./.env; set +a
  .venv/bin/python -m scripts.single_attraction.poc_md_breadcrumb_distrito

Needs RUN_REAL_EXTERNALS=true + Redis (MD sitemap/page cache). Read-only — writes nothing
to the DB; it only prints the feasibility matrix.
"""

from __future__ import annotations

import asyncio
import html
import os
import re
import sys
import unicodedata
from pathlib import Path

import httpx

_REPO = Path(__file__).resolve().parents[2]

# (attraction_name, uf, ta_municipio, ta_municipio_ibge, expected_distrito_or_None)
# Golden + controls: distrito-level places (Arraial/Trancoso) and município-level (Pampulha
# → MD stops at Belo Horizonte; a clean município attraction → no distrito).
TEST_CASES = [
    ("Igreja Nossa Senhora d'Ajuda", "BA", "Porto Seguro", "2925303", "Arraial d'Ajuda"),
    ("Pitinga", "BA", "Porto Seguro", "2925303", "Arraial d'Ajuda"),
    ("Mucugê", "BA", "Porto Seguro", "2925303", "Arraial d'Ajuda"),
    ("Centro Histórico de Arraial d'Ajuda", "BA", "Porto Seguro", "2925303", "Arraial d'Ajuda"),
    # Control: MD breadcrumb stops at município (Belo Horizonte). Pampulha is a subdistrito,
    # but MD does not surface it → the only IBGE hit is the SEAT distrito (== município name),
    # which is NOT a finer sub-município place → expect NO distinct distrito.
    ("Igreja da Pampulha São Francisco de Assis", "MG", "Belo Horizonte", "3106200", None),
]


def _fold(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", (s or "").lower().strip())
        if unicodedata.category(c) != "Mn"
    )


def _parse_breadcrumb(html_text: str) -> list[str]:
    """Extract the breadcrumb chain (minus 'Guia Melhores Destinos' + 'Brasil')."""
    m = re.search(r'id=["\']breadcrumbs["\'].*?</(?:nav|ul|ol|div)>', html_text, re.S | re.I)
    if not m:
        return []
    parts = [html.unescape(t.strip()) for t in re.findall(r">([^<>]+)<", m.group(0)) if t.strip()]
    # Drop the two fixed prefixes; remainder = [Region, State, Place, (Attraction)]
    drop = {"guia melhores destinos", "brasil"}
    return [p for p in parts if _fold(p) not in drop]


def _match_distrito(place: str, uf: str, distritos) -> object | None:
    """Fuzzy-match a breadcrumb place name to an IBGE distrito within the UF."""
    from rapidfuzz import fuzz, process, utils

    cands = [d for d in distritos if d.uf == uf]
    if not cands:
        return None
    choices = [_fold(d.nome) for d in cands]
    res = process.extractOne(
        _fold(place), choices, scorer=fuzz.token_sort_ratio,
        score_cutoff=88, processor=utils.default_process,
    )
    return cands[res[2]] if res else None


def _match_municipio(place: str, uf: str, municipios) -> object | None:
    from rapidfuzz import fuzz, process, utils

    cands = [m for m in municipios if m.uf == uf]
    choices = [_fold(m.nome) for m in cands]
    res = process.extractOne(
        _fold(place), choices, scorer=fuzz.token_sort_ratio,
        score_cutoff=88, processor=utils.default_process,
    )
    return cands[res[2]] if res else None


async def _fetch_html(url: str, ua: str) -> str:
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as hc:
        r = await hc.get(url, headers={"User-Agent": ua})
        r.raise_for_status()
        return r.text


async def _run() -> None:
    os.environ["RUN_REAL_EXTERNALS"] = "true"

    import redis as redis_lib
    from brave.config.settings import MelhoresDestinosConfig
    from brave.clients.melhores_destinos import RealMelhoresDestinosClient
    from brave.shared.ibge_distritos import load_distritos_csv
    from brave.domains.tripadvisor.ibge import load_ibge_csv

    md_config = MelhoresDestinosConfig()
    redis_client = redis_lib.from_url(os.environ.get("BRAVE_DB_REDIS_URL", "redis://localhost:6379/0"))
    md = RealMelhoresDestinosClient(config=md_config, redis=redis_client)

    distritos = load_distritos_csv(_REPO / "data" / "ibge" / "ibge_distritos.csv")
    municipios = load_ibge_csv(_REPO / "data" / "ibge" / "ibge_municipios.csv")

    print("POC: MD breadcrumb → IBGE distrito assignment (3-way cross: TA + MD + CSV)\n")
    ok = 0
    for name, uf, ta_muni, ta_muni_ibge, expected in TEST_CASES:
        print("=" * 78)
        print(f"attraction : {name}")
        print(f"TripAdvisor: município={ta_muni} (IBGE {ta_muni_ibge}), UF={uf}")

        url = await md.find_attraction_url(name, uf=uf, municipio=ta_muni)
        if not url:
            print("MD         : no attraction page matched (find_attraction_url → None)\n")
            continue
        print(f"MD page    : {url}")
        try:
            html_text = await _fetch_html(url, md_config.user_agent)
        except Exception as exc:  # noqa: BLE001
            print(f"MD fetch   : failed ({type(exc).__name__}: {exc})\n")
            continue
        chain = _parse_breadcrumb(html_text)
        print(f"breadcrumb : {' → '.join(chain)}")
        if len(chain) < 3:
            print("             (breadcrumb too short — no Place level)\n")
            continue
        # [Region, State, Place, (Attraction)] — Place is index 2.
        place = chain[2]
        print(f"MD place   : {place!r}")

        dist = _match_distrito(place, uf, distritos)
        muni = _match_municipio(place, uf, municipios)
        # A distrito whose name == its parent município name is the SEAT distrito — it is
        # NOT a finer sub-município place, just the município centre. Only a distrito whose
        # name differs from the município (Arraial d'Ajuda ≠ Porto Seguro) is a genuine
        # sub-município assignment worth writing.
        is_seat = dist is not None and _fold(dist.nome) == _fold(dist.municipio_nome)
        if dist is not None and not is_seat:
            parent_ok = dist.ibge_code == ta_muni_ibge
            print(f"IBGE match : DISTINCT DISTRITO {dist.nome} (code {dist.distrito_code}), "
                  f"parent município {dist.municipio_nome} ({dist.ibge_code})")
            print(f"3-way cross: parent município {'==' if parent_ok else '!='} TA município"
                  f"  → {'✓ CONFIRMED — assign distrito' if parent_ok else '✗ parent mismatch — reject'}")
            verdict_place = dist.nome if parent_ok else None
        elif is_seat:
            print(f"IBGE match : SEAT distrito {dist.nome} ({dist.distrito_code}) == município "
                  "name → município-level only, no finer distrito to assign")
            verdict_place = None
        elif muni is not None:
            print(f"IBGE match : MUNICÍPIO {muni.nome} ({muni.ibge_code}) — município-level, "
                  "no distrito")
            verdict_place = None
        else:
            print("IBGE match : place matched neither distrito nor município in this UF")
            verdict_place = None

        exp_fold = _fold(expected) if expected else None
        got_fold = _fold(verdict_place) if verdict_place else None
        match = exp_fold == got_fold
        ok += 1 if match else 0
        print(f"expected   : distrito={expected!r}   got: distrito={verdict_place!r}   "
              f"→ {'PASS' if match else 'FAIL'}\n")

    print("=" * 78)
    print(f"RESULT: {ok}/{len(TEST_CASES)} cases matched expectation.")


def main() -> None:
    if "BRAVE_DB_REDIS_URL" not in os.environ and "REDIS_URL" not in os.environ:
        print("NOTE: BRAVE_DB_REDIS_URL not set — using redis://localhost:6379/0", file=sys.stderr)
    asyncio.run(_run())


if __name__ == "__main__":
    main()
