#!/usr/bin/env python
"""POC: Wikipedia + Wikidata substituem o web_search como fonte de fato?

Mede, para uma lista de atrativos: quantos tokens o contexto custa e quantos dos fatos
que o `web_search` do Sonnet trouxe estão presentes. Sem key, sem LLM, sem browser,
sem custo. Só a API pública da Wikimedia sobre o httpx que o projeto já usa.

O ponto que este probe existe para medir: o `web_search` da Anthropic injeta ~11.900
tokens de resultado por atrativo (61% da conta, medido em gemini-viability.md §9.3).
A Wikipedia entrega o mesmo tipo de fato em ~400-1000 tokens.

Uso:
    .venv/bin/python scripts/poc/wikifacts_probe.py
    .venv/bin/python scripts/poc/wikifacts_probe.py "Pico da Bandeira" "Pedra Azul"
"""

from __future__ import annotations

import sys

import httpx

API = "https://pt.wikipedia.org/w/api.php"
UA = {"user-agent": "norteia-brave-poc/1.0 (avaliacao de fontes; contato via repo)"}

# Propriedades do Wikidata que interessam a um atrativo. Valor estruturado, sem LLM.
WD_PROPS = {
    "P571": "inception",
    "P2044": "elevation",
    "P2048": "height",
    "P625": "coord",
    "P1435": "heritage_status",
    "P84": "architect",
    "P149": "architectural_style",
    "P131": "admin_area",
    "P276": "location",
}

# Fatos que o web_search do Sonnet produziu na POC — o alvo a bater.
TARGETS: dict[str, list[str]] = {
    "Convento da Penha": ["1558", "Pedro Palácios", "154", "IPHAN", "1943", "rococó"],
    "Cachoeira da Fumaça (Espírito Santo)": ["144", "1984", "Braço Norte", "Itapemirim", "lontra"],
}


def approx_tokens(text: str) -> int:
    """~3.6 chars por token em PT-BR. Aproximação suficiente para ordem de grandeza."""
    return round(len(text) / 3.6)


def search(client: httpx.Client, query: str, limit: int = 3) -> list[str]:
    r = client.get(
        API,
        params={
            "action": "query",
            "list": "search",
            "srsearch": query,
            "srlimit": limit,
            "format": "json",
        },
    )
    return [hit["title"] for hit in r.json().get("query", {}).get("search", [])]


def article(client: httpx.Client, title: str) -> str:
    r = client.get(
        API,
        params={
            "action": "query",
            "prop": "extracts",
            "explaintext": 1,
            "exlimit": 1,
            "titles": title,
            "redirects": 1,
            "format": "json",
        },
    )
    pages = r.json()["query"]["pages"]
    return next(iter(pages.values())).get("extract", "")


def wikidata(client: httpx.Client, title: str) -> dict[str, str]:
    r = client.get(
        API,
        params={
            "action": "query",
            "prop": "pageprops",
            "titles": title,
            "redirects": 1,
            "format": "json",
        },
    )
    page = next(iter(r.json()["query"]["pages"].values()))
    qid = (page.get("pageprops") or {}).get("wikibase_item")
    if not qid:
        return {}
    ent = client.get(f"https://www.wikidata.org/wiki/Special:EntityData/{qid}.json").json()
    claims = ent["entities"][qid].get("claims", {})
    out: dict[str, str] = {"qid": qid}
    for prop, label in WD_PROPS.items():
        if prop not in claims:
            continue
        vals = []
        for c in claims[prop]:
            v = (c.get("mainsnak") or {}).get("datavalue", {}).get("value")
            if v is None:
                continue
            if isinstance(v, dict):
                vals.append(str(v.get("time") or v.get("amount") or v.get("id") or v))
            else:
                vals.append(str(v))
        if vals:
            out[label] = "; ".join(vals)
    return out


def main() -> None:
    names = sys.argv[1:] or list(TARGETS)
    with httpx.Client(headers=UA, timeout=30.0, follow_redirects=True) as client:
        for name in names:
            hits = search(client, name)
            # O 1º resultado NÃO é confiável: para "Cachoeira da Fumaça Alegre" a busca
            # devolve o município antes do atrativo (mesmo modo de falha do
            # resolve_municipio first-match). Casar por coordenada do Places antes de usar.
            title = name if name in hits else (hits[0] if hits else "")
            if not title:
                print(f"\n=== {name}: sem página")
                continue
            text = article(client, title)
            wd = wikidata(client, title)
            targets = TARGETS.get(name, [])
            found = [t for t in targets if t.lower() in text.lower()]

            print(f"\n=== {name}")
            print(f"  candidatos da busca: {' | '.join(hits)}")
            print(f"  página usada: {title!r}")
            print(f"  artigo: {len(text)} chars ≈ {approx_tokens(text)} tokens")
            if targets:
                print(f"  fatos-alvo presentes: {len(found)}/{len(targets)} → {', '.join(found)}")
            print(f"  wikidata: {wd or '(sem item)'}")

    print("\nBaseline para comparação: o web_search do Sonnet injeta ~11.900 tokens/atrativo")
    print("(medido em docs/poc/gemini-viability.md §9.3) ao custo de $0,0358 por descrição.")


if __name__ == "__main__":
    main()
