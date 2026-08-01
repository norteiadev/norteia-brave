"""SPIKE (not production): guia.melhoresdestinos.com.br consegue preencher as 6 colunas
editoriais de `attractions` que o Google Places nao preenche?

Colunas-alvo: accessibility, how_to_get_there, tips, safety_alerts,
local_infrastructure, curiosities.

Roda o MESMO conjunto de 15 atrativos do spike do Places
(`scripts/places_fields_spike.py`) para dar um comparativo direto, incluindo os 4 que o
Places nao resolveu para POI (Convento da Penha, Praia de Camburi, Praia dos Carneiros,
centro historico de Paraty).

Duas passadas:
  A) sizing  - amostra aleatoria do sitemap so para medir a fatia BR do universo de
               4470 paginas `-l`.
  B) conteudo - as 15 paginas alvo, com scan deterministico de sinal por coluna
               + o trecho casado, para dar pra julgar qualidade sem LLM.

Nao usa LLM, nao usa banco, nao altera producao. Educado: 1 req/s, UA identificavel.

Run:
    .venv/bin/python scripts/melhores_destinos_columns_spike.py
    .venv/bin/python scripts/melhores_destinos_columns_spike.py --selfcheck
"""

from __future__ import annotations

import html as htmllib
import json
import random
import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx

BASE = "https://guia.melhoresdestinos.com.br"
SITEMAP = f"{BASE}/sitemap.xml"
REPORT = Path("docs/poc/melhores-destinos-columns-spike.auto.md")
UA = "norteia-brave-research/0.1 (+avaliacao de fonte; contato: leandro.freire08@gmail.com)"
DELAY = 1.0
SIZING_N = 60

# Mesmos 15 do spike do Places, por slug (a URL carrega citycode+id que mudam).
TARGET_SLUGS: list[tuple[str, str]] = [
    ("cristo-redentor", "RJ"),
    ("theatro-municipal", "RJ"),
    ("paraty", "RJ"),
    ("masp", "SP"),
    ("convento-da-penha", "ES"),
    ("camburi", "ES"),
    ("elevador-lacerda", "BA"),
    ("pai-inacio", "BA"),
    ("cachoeira-da-fumaca", "BA"),
    ("sao-francisco-de-assis", "MG"),
    ("gruta-do-lago-azul", "MS"),
    ("cataratas-do-iguacu", "PR"),
    ("teatro-amazonas", "AM"),
    ("carneiros", "PE"),
    ("lencois-maranhenses", "MA"),
]

# Scan deterministico. Cada coluna: regex sobre headings + corpo. Word-boundary para nao
# casar "dica" dentro de "indicado".
SIGNALS: dict[str, str] = {
    "how_to_get_there": r"\b(como chegar|acesso ao|acesso a |como ir|chegar (?:a|ao|à|em|até)|de ônibus|de carro|aeroporto mais próximo|transfer|linha de ônibus|estacionar)\b",
    "accessibility": r"\b(acessibilidade|acessível|acessíveis|cadeirante\w*|mobilidade reduzida|rampa\w*|deficiente\w*|degraus)\b",
    "local_infrastructure": r"\b(estrutura|infraestrutura|estacionamento|banheiro\w*|lanchonete\w*|quiosque\w*|centro de visitantes|bilheteria|loja de souvenir\w*|vestiário\w*|guarda-volumes)\b",
    # "recomend\w+" sozinho e ruidoso demais ("nao e recomendado a cadeirantes" e
    # acessibilidade, nao dica) — so conta com um verbo de acao depois.
    "tips": r"\b(dica\w*|melhor época|melhor horário|melhor hora|o que levar|leve |vale a pena|recomend\w+ (?:levar|visitar|ir|chegar|reservar|contratar|usar|comprar)|evite|chegue cedo|reserve com antecedência)\b",
    "safety_alerts": r"\b(segurança|cuidado\w*|perigo\w*|risco\w*|não é recomendado|atenção|correnteza|afogamento|não se aventure|acidente\w*)\b",
    "curiosities": r"\b(curiosidade\w*|fundad\w+|século \w+|em 1[5-9]\d\d|lenda\w*|origem do nome|tombad\w+|patrimônio|construíd\w+ em)\b",
}


def clean(fragment: str) -> str:
    """HTML fragment -> plain text, images and tags removed."""
    s = re.sub(r"<(script|style)[\s\S]*?</\1>", " ", fragment)
    s = re.sub(r"<img[^>]*>", " ", s)
    s = re.sub(r"<h2[^>]*>", "\n## ", s)
    s = re.sub(r"</(p|h2|h3|li|div)>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = htmllib.unescape(s)
    s = re.sub(r"[ \t]+", " ", s)
    return re.sub(r"\n\s*\n+", "\n\n", s).strip()


def post_body(page: str) -> str:
    """The authored article only — between <div class="post-body"> and the author card."""
    i = page.find('class="post-body"')
    if i < 0:
        return ""
    j = page.find('class="author-card', i)
    return clean(page[i : j if j > i else i + 80_000])


def breadcrumb(page: str) -> list[str]:
    for m in re.finditer(r'<script[^>]*application/ld\+json[^>]*>([\s\S]*?)</script>', page):
        try:
            data = json.loads(m.group(1))
        except Exception:  # noqa: BLE001 — malformed JSON-LD is a data fact, not a crash
            continue
        if data.get("@type") == "BreadcrumbList":
            return [it.get("name", "") for it in data.get("itemListElement", [])]
    return []


def headings(page: str) -> list[str]:
    return [
        re.sub(r"<[^>]+>", "", m.group(1)).strip()
        for m in re.finditer(r"<h2[^>]*>([\s\S]*?)</h2>", page)
    ]


def scan(text: str) -> dict[str, str | None]:
    """Per column: the first matching segment, or None.

    A heading ("## Acesso ao X") is the strongest signal but is too short to read on its
    own, so a short match is glued to the segment that follows it.
    """
    segs = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]
    out: dict[str, str | None] = {}
    for col, pattern in SIGNALS.items():
        rx = re.compile(pattern, re.IGNORECASE)
        hit = None
        for i, seg in enumerate(segs):
            if not rx.search(seg):
                continue
            hit = seg if len(seg) >= 60 else " ".join(segs[i : i + 2])
            break
        out[col] = hit
    return out


def fetch(client: httpx.Client, url: str) -> str | None:
    try:
        r = client.get(url, headers={"User-Agent": UA}, timeout=30.0, follow_redirects=True)
    except Exception as exc:  # noqa: BLE001
        print(f"  ! {url} -> {exc}")
        return None
    if r.status_code != 200:
        print(f"  ! {url} -> HTTP {r.status_code}")
        return None
    return r.text


def main() -> None:
    with httpx.Client() as client:
        print("robots.txt:")
        print("  " + (fetch(client, f"{BASE}/robots.txt") or "").replace("\n", "\n  ").strip())
        tos = client.get(f"{BASE}/termos-de-uso", headers={"User-Agent": UA}, timeout=20.0)
        print(f"/termos-de-uso -> HTTP {tos.status_code}")

        sm = fetch(client, SITEMAP) or ""
        locs = re.findall(r"<loc>(.*?)</loc>", sm)
        attrs = [u for u in locs if re.search(r"-\d+-\d+-l\.html$", u)]
        print(f"\nsitemap: {len(locs)} locs, {len(attrs)} paginas de atrativo (-l)")

        # --- Passada A: qual fatia do universo e Brasil? ---
        print(f"\n[A] sizing sobre {SIZING_N} paginas aleatorias")
        rng = random.Random(20260731)
        sizing: list[dict[str, Any]] = []
        for url in rng.sample(attrs, SIZING_N):
            time.sleep(DELAY)
            page = fetch(client, url)
            if not page:
                continue
            crumbs = breadcrumb(page)
            sizing.append({"url": url, "crumbs": crumbs, "br": "Brasil" in crumbs})
        br = sum(1 for s in sizing if s["br"])
        pct = 100 * br / len(sizing) if sizing else 0
        print(f"  BR: {br}/{len(sizing)} = {pct:.0f}%  -> ~{int(len(attrs) * pct / 100)} de {len(attrs)}")

        # --- Passada B: conteudo das 15 alvo ---
        print("\n[B] conteudo dos 15 atrativos-alvo")
        content: list[dict[str, Any]] = []
        for slug, uf in TARGET_SLUGS:
            cands = [u for u in attrs if slug in u]
            row: dict[str, Any] = {"slug": slug, "uf": uf, "candidates": len(cands)}
            picked = None
            for url in cands[:4]:
                time.sleep(DELAY)
                page = fetch(client, url)
                if not page:
                    continue
                crumbs = breadcrumb(page)
                if "Brasil" not in crumbs:
                    continue
                picked = (url, page, crumbs)
                break
            if not picked:
                print(f"  - {slug}: SEM PAGINA BR ({len(cands)} candidatos)")
                row["found"] = False
                content.append(row)
                continue
            url, page, crumbs = picked
            body = post_body(page)
            row |= {
                "found": True,
                "url": url,
                "crumbs": crumbs,
                "headings": headings(page),
                "chars": len(body),
                "signals": scan(body),
            }
            hits = [c for c, v in row["signals"].items() if v]
            print(f"  - {slug}: {len(body)} chars, {len(row['headings'])} h2 | {', '.join(hits) or '(nenhum)'}")
            content.append(row)

    write_report(len(attrs), sizing, content)


def write_report(
    total_attrs: int, sizing: list[dict[str, Any]], content: list[dict[str, Any]]
) -> None:
    found = [r for r in content if r.get("found")]
    n = len(found)
    br = sum(1 for s in sizing if s["br"])
    pct = 100 * br / len(sizing) if sizing else 0

    lines = [
        "# Spike (auto) — Melhores Destinos para as 6 colunas editoriais",
        "",
        f"Universo: {total_attrs} paginas `-l` no sitemap. Amostra de sizing: {br}/{len(sizing)} "
        f"= {pct:.0f}% Brasil -> ~{int(total_attrs * pct / 100)} paginas BR.",
        f"Amostra de conteudo: {n}/{len(content)} dos alvos encontrados com pagina BR.",
        "",
        "## Sinal por coluna",
        "",
        "| Coluna | Paginas com sinal | % |",
        "|---|---|---|",
    ]
    for col in SIGNALS:
        c = sum(1 for r in found if r["signals"].get(col))
        lines.append(f"| `{col}` | {c}/{n} | {(100 * c / n if n else 0):.0f}% |")

    lines += ["", "## Trechos casados (2 por coluna)", ""]
    for col in SIGNALS:
        lines.append(f"### `{col}`")
        shown = 0
        for r in found:
            hit = r["signals"].get(col)
            if hit and shown < 2:
                lines.append(f"- **{r['slug']}**: {hit[:400]}")
                shown += 1
        if not shown:
            lines.append("- (nenhum)")
        lines.append("")

    lines += ["## Headings (h2) por pagina", ""]
    for r in found:
        lines.append(f"- **{r['slug']}** ({r['chars']} chars): {' · '.join(r['headings']) or '(sem h2)'}")
    missing = [r["slug"] for r in content if not r.get("found")]
    if missing:
        lines += ["", f"**Sem pagina BR no site:** {', '.join(missing)}"]

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    raw = REPORT.with_suffix(".raw.json")
    raw.write_text(
        json.dumps({"sizing": sizing, "content": content}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nreport: {REPORT}\nraw:    {raw}")


if __name__ == "__main__":
    if "--selfcheck" in sys.argv:
        page = (
            '<div class="post-body"><h1>X</h1><p>Fica no alto de um morro.</p>'
            "<h2>Acesso ao X</h2><p>Sobe-se uma escadaria com cerca de 365 degraus, "
            "e por isso pode nao ser recomendado a pessoas com mobilidade reduzida.</p>"
            '<img src="a.jpg"><h2>Historia</h2><p>Sua construcao comecou em 1568 no cume.</p>'
            '<div class="author-card"><h2>Fulano</h2></div>'
        )
        body = post_body(page)
        assert "Fulano" not in body and "a.jpg" not in body
        assert "## Acesso ao X" in body
        assert headings(page) == ["Acesso ao X", "Historia", "Fulano"]
        sig = scan(body)
        assert sig["how_to_get_there"] and "escadaria" in sig["how_to_get_there"]
        assert sig["accessibility"] and "mobilidade reduzida" in sig["accessibility"]
        assert sig["curiosities"] and "1568" in sig["curiosities"]
        assert sig["tips"] is None
        assert breadcrumb('<script type="application/ld+json">{"@type":"BreadcrumbList",'
                          '"itemListElement":[{"name":"Brasil"}]}</script>') == ["Brasil"]
        print("selfcheck ok")
    else:
        main()
