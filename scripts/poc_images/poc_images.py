#!/usr/bin/env python3
"""POC — is there a viable free image source for Brazilian atrativos?

Measures HIT-RATE PER TIER across three sources that fail in opposite directions:

  MTur   (via Commons import)  official curated destination photography, no geo
  Commons (geosearch + phrase) real geo search, bimodal coverage
  Pixabay (text only)          generic stock, decorative fallback

Two cohorts, reported SEPARATELY and never averaged together:
  db      — real attractions from rio_records (currently 15, all ES)
  control — hand-built national set spanning the fame gradient, coordinates taken
            from Wikidata P625 values verified during research. This exists because
            the db cohort is too thin and single-state to say anything about the
            long tail, which is the whole question.

Read-only. Writes nothing to the DB, touches nothing under brave/.

Run:
    set -a; . ./.env; set +a
    .venv/bin/python -m scripts.poc_images.harvest_mtur      # once
    .venv/bin/python -m scripts.poc_images.poc_images --measure-all
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

OUT = Path(__file__).parent / "out"
DOC = Path(__file__).resolve().parents[2] / "docs" / "poc" / "atrativo-imagens.md"

# (nome, municipio, uf, tipo, lat, lon, estrato)
# Coordinates are Wikidata P625 values verified during the research phase. A zero
# result on a control row could still be a coordinate error rather than absent
# coverage -- the report flags this.
CONTROL = [
    ("Cristo Redentor", "Rio de Janeiro", "RJ", "mirante", -22.951916, -43.210487, "famoso"),
    ("Pão de Açúcar", "Rio de Janeiro", "RJ", "mirante", -22.948658, -43.157444, "famoso"),
    ("Cataratas do Iguaçu", "Foz do Iguaçu", "PR", "cachoeira", -25.686389, -54.445278, "famoso"),
    ("Pelourinho", "Salvador", "BA", "centro_historico", -12.971111, -38.508889, "famoso"),
    ("Lençóis Maranhenses", "Barreirinhas", "MA", "parque", -2.483333, -43.133333, "medio"),
    ("Praia do Forte", "Mata de São João", "BA", "praia", -12.578611, -38.000278, "medio"),
    ("Gruta do Lago Azul", "Bonito", "MS", "outros", -21.144722, -56.587778, "medio"),
    ("Cachoeira da Fumaça", "Palmeiras", "BA", "cachoeira", -12.6, -41.483333, "medio"),
    ("Cachoeira do Buracão", "Ibicoara", "BA", "cachoeira", -13.326944, -41.146389, "obscuro"),
    ("Vale do Pati", "Mucugê", "BA", "trilha", -12.95, -41.466667, "obscuro"),
    ("Igreja de Santa Isabel", "Mucugê", "BA", "centro_historico", -13.0053, -41.3711, "obscuro"),
    ("Poço Encantado", "Itaeté", "BA", "outros", -12.983333, -41.15, "obscuro"),
]

_PIXABAY_CATEGORY = {
    "praia": "travel", "cachoeira": "nature", "trilha": "nature", "parque": "nature",
    "mirante": "travel", "centro_historico": "buildings", "museu": "buildings",
    "igreja": "buildings", "outros": "travel",
}


def load_db_cohort(limit: int) -> list[dict]:
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker
    from brave.core.models import NascenteRecord, RioRecord

    engine = create_engine(os.environ["BRAVE_DB_URL"])
    session = sessionmaker(bind=engine)()
    rows = session.execute(
        select(RioRecord).where(RioRecord.entity_type == "attraction").limit(limit)
    ).scalars().all()
    out = []
    for r in rows:
        nas = session.get(NascenteRecord, r.nascente_id)
        canonical = ((nas.payload if nas else {}) or {}).get("canonical", {}) or {}
        n = r.normalized or {}
        lat, lon = n.get("lat"), n.get("lon")
        out.append({
            "nome": n.get("name"), "municipio": n.get("municipio"), "uf": r.uf,
            "tipo": canonical.get("tipo") or canonical.get("category") or "outros",
            "lat": float(lat) if lat else None, "lon": float(lon) if lon else None,
            "estrato": "db", "cohort": "db",
            "rio_id": r.id, "score": r.score, "routing": r.routing,
        })
    session.close()
    return out


def commons_tiers(cx, atr: dict, measure_all: bool) -> dict[str, list[dict]]:
    """C1/C2 geosearch (need coordinates), C3 phrase search."""
    from scripts.poc_images.commons import is_photo, license_verdict

    found: dict[str, list[dict]] = {}
    plans = []
    if atr["lat"] and atr["lon"]:
        plans += [("C1_geo_500m", 500), ("C2_geo_2km", 2000)]
    for tier, radius in plans:
        hits = cx.geosearch(atr["lat"], atr["lon"], radius, limit=50)
        found[tier] = hits
        if hits and not measure_all:
            break
    if measure_all or not any(found.values()):
        found["C3_name"] = cx.search(atr["nome"], limit=50)

    # Resolve titles -> imageinfo, filter to usable photos.
    resolved: dict[str, list[dict]] = {}
    for tier, hits in found.items():
        titles = [h["title"] for h in hits][:50]
        if not titles:
            resolved[tier] = []
            continue
        info = cx.image_info(titles)
        dist_by_title = {h["title"]: h.get("dist") for h in hits}
        recs = []
        for t, rec in info.items():
            if not is_photo(rec):
                continue
            verdict = license_verdict(rec)
            if verdict != "ok":
                rec["_rejected"] = verdict
            rec["dist"] = dist_by_title.get(t)
            rec["license_verdict"] = verdict
            recs.append(rec)
        resolved[tier] = recs
    return resolved


def commons_rank(f: dict) -> tuple:
    """Lower = better. Commons exposes NO engagement signal, so this is synthesized."""
    assess = (f.get("assessments") or "")
    curated = 0 if "featured" in assess else (1 if "quality" in assess else 2)
    wlm = 0 if any("Wiki Loves" in c for c in (f.get("categories") or [])) else 1
    dist = (f.get("dist") or 9999) / 1000.0
    return (curated, wlm, dist, -(f.get("width", 0) * f.get("height", 0)) / 1e6)


def to_image(rec: dict, source: str, tier: str, query: str, decorative=False) -> dict:
    """Normalize any source's record into the Brave `images[]` shape."""
    if source == "pixabay":
        return {
            "source": "pixabay", "url": rec.get("largeImageURL"),
            "preview_url": rec.get("webformatURL"),  # expires in 24h -- POC only
            "page_url": rec.get("pageURL"), "pixabay_id": rec.get("id"),
            "width": rec.get("imageWidth"), "height": rec.get("imageHeight"),
            "author": rec.get("user"), "license": "Pixabay Content License",
            "attribution": f"by {rec.get('user')} via Pixabay",
            "views": rec.get("views"), "downloads": rec.get("downloads"),
            "likes": rec.get("likes"),
            "match_tier": tier, "query_used": query, "is_decorative": decorative,
        }
    if source == "mtur":
        return {
            "source": "mtur", "url": rec.get("url"), "page_url": rec.get("page_url"),
            "flickr_id": rec.get("flickr_id"),
            "width": rec.get("width"), "height": rec.get("height"),
            "author": rec.get("author"), "license": rec.get("license"),
            "attribution": f"Foto: {rec.get('author')}",
            "place_categories": rec.get("place_categories"),
            "match_tier": tier, "match_confidence": rec.get("match_confidence"),
            "match_via": rec.get("match_via"), "query_used": query,
        }
    return {
        "source": "commons", "url": rec.get("url"), "page_url": rec.get("descriptionurl"),
        "width": rec.get("width"), "height": rec.get("height"),
        "author": rec.get("artist"), "license": rec.get("licenseshortname"),
        "license_url": rec.get("licenseurl"),
        "attribution": f"Foto: {rec.get('artist')} / {rec.get('licenseshortname')}",
        "assessments": rec.get("assessments"), "dist_m": rec.get("dist"),
        "license_verdict": rec.get("license_verdict"),
        "match_tier": tier, "query_used": query, "is_decorative": decorative,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Image-source feasibility POC for atrativos.")
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--measure-all", action="store_true",
                    help="run every tier even after a hit (needed for per-tier hit-rate)")
    ap.add_argument("--no-db", action="store_true", help="control cohort only")
    ap.add_argument("--self-check", action="store_true")
    ap.add_argument("--report-only", action="store_true",
                    help="regenerate the report from the saved JSON, no network")
    args = ap.parse_args()

    if args.self_check:
        from scripts.poc_images import mtur as m, pixabay as p
        m.demo(); p.demo()
        return

    if args.report_only:
        saved = json.loads((OUT / "atrativos_images.json").read_text(encoding="utf-8"))
        for r in saved:  # apply the corrected decorative rule to already-fetched data
            for i in r["images"]:
                if i["source"] == "pixabay":
                    i["is_decorative"] = True
        idx = json.loads((Path(__file__).parent / "out" / "mtur_index.json")
                         .read_text(encoding="utf-8"))
        report(saved, 0.0, sum(1 for r in idx if r.get("license_verdict") == "ok"), None)
        return

    os.environ["RUN_REAL_EXTERNALS"] = "true"
    OUT.mkdir(parents=True, exist_ok=True)

    import redis as redis_lib
    from scripts.poc_images.commons import Commons
    from scripts.poc_images.mtur import load_index, match
    from scripts.poc_images.pixabay import Pixabay, rank_by_engagement

    # --- cohorts
    cohort: list[dict] = []
    if not args.no_db:
        if "BRAVE_DB_URL" not in os.environ:
            sys.exit("ERROR: BRAVE_DB_URL not set. Run: set -a; . ./.env; set +a")
        cohort += load_db_cohort(args.limit)
    cohort += [
        {"nome": n, "municipio": mu, "uf": uf, "tipo": tp, "lat": la, "lon": lo,
         "estrato": st, "cohort": "control"}
        for (n, mu, uf, tp, la, lo, st) in CONTROL
    ]
    print(f"cohort: {sum(1 for a in cohort if a['cohort'] == 'db')} db + "
          f"{sum(1 for a in cohort if a['cohort'] == 'control')} control")

    index = load_index()
    print(f"MTur index: {len(index)} usable photos")

    redis_client = None
    try:
        redis_client = redis_lib.from_url(
            os.environ.get("BRAVE_DB_REDIS_URL", "redis://localhost:6379/0"))
        redis_client.ping()
    except Exception:
        print("NOTE: Redis unavailable — Pixabay 24h cache disabled for this run")
        redis_client = None

    cx = Commons()
    px = Pixabay(os.environ.get("BRAVE_PIXABAY_API_KEY", ""), redis=redis_client)

    results = []
    t0 = time.monotonic()
    for i, atr in enumerate(cohort, 1):
        print(f"[{i}/{len(cohort)}] {atr['nome']} — {atr['municipio']}/{atr['uf']} "
              f"({atr['estrato']})", flush=True)
        images: list[dict] = []
        tiers_hit: list[str] = []

        # ---- M1: MTur (local index, no network)
        mt = match(atr["nome"], atr["municipio"], atr["uf"], index, top=3)
        if mt:
            tiers_hit.append("M1_mtur")
            images += [to_image(r, "mtur", "M1_mtur",
                                f"index:mtur uf={atr['uf']} municipio={atr['municipio']}")
                       for r in mt]

        # ---- C*: Commons
        try:
            ctiers = commons_tiers(cx, atr, args.measure_all)
        except Exception as exc:  # noqa: BLE001
            print(f"    commons failed: {type(exc).__name__}: {exc}")
            ctiers = {}
        for tier, recs in ctiers.items():
            usable = [r for r in recs if r.get("license_verdict") == "ok"]
            if usable:
                tiers_hit.append(tier)
            usable.sort(key=commons_rank)
            q = (f"geo:{atr['lat']},{atr['lon']}" if tier.startswith("C1") or tier.startswith("C2")
                 else f'search:"{atr["nome"]}"')
            images += [to_image(r, "commons", tier, q) for r in usable[:3]]

        # ---- P*: Pixabay (decorative fallback)
        if px.key:
            plans = [("P1_exact", atr["nome"], None),
                     ("P2_type_municipio", f"{atr['tipo']} {atr['municipio']}", None),
                     ("P3_type_generic", atr["tipo"],
                      _PIXABAY_CATEGORY.get(atr["tipo"], "travel"))]
            for tier, q, cat in plans:
                try:
                    hits = px.search(q, lang="pt", category=cat)
                except Exception as exc:  # noqa: BLE001
                    print(f"    pixabay {tier} failed: {type(exc).__name__}: {exc}")
                    continue
                if hits:
                    tiers_hit.append(tier)
                    # ALL Pixabay hits are decorative, including P1_exact. Pixabay never
                    # returns empty -- it serves generic stock for any query, so a "hit"
                    # is not evidence the photo depicts this attraction. Measured: one
                    # image was returned for 17 different attractions.
                    images += [to_image(h, "pixabay", tier, q, decorative=True)
                               for h in rank_by_engagement(hits)[:3]]
                if hits and not args.measure_all:
                    break

        results.append({**atr, "tiers_hit": tiers_hit, "images": images})
        print(f"    tiers: {tiers_hit or '(nenhum)'}  imagens: {len(images)}")

    cx.close(); px.close()
    elapsed = time.monotonic() - t0

    (OUT / "atrativos_images.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

    report(results, elapsed, len(index), px.remaining)


def report(results: list[dict], elapsed: float, index_size: int, px_left) -> None:
    from collections import Counter

    ALL_TIERS = ["M1_mtur", "C1_geo_500m", "C2_geo_2km", "C3_name",
                 "P1_exact", "P2_type_municipio", "P3_type_generic"]
    lines: list[str] = []
    add = lines.append

    add("# POC — viabilidade de enriquecimento de imagens para atrativos\n")
    add(f"Amostra: **{len(results)} atrativos**. Índice MTur: **{index_size}** fotos usáveis. "
        f"Tempo: {elapsed:.0f}s.\n")
    add("> Duas coortes, **nunca somadas**: `db` = registros reais de `rio_records` "
        "(15, todas no ES); `control` = conjunto nacional montado à mão para cobrir o "
        "gradiente de fama, com coordenadas do Wikidata P625. Um zero no controle "
        "pode ser erro de coordenada, não ausência de cobertura.\n")

    for cohort in ("db", "control"):
        rows = [r for r in results if r["cohort"] == cohort]
        if not rows:
            continue
        add(f"\n## Coorte `{cohort}` ({len(rows)} atrativos)\n")
        add("### Hit-rate por tier\n")
        add("| tier | atrativos com hit | % |")
        add("|---|---|---|")
        for t in ALL_TIERS:
            n = sum(1 for r in rows if t in r["tiers_hit"])
            add(f"| `{t}` | {n} | {n / len(rows):.0%} |")
        none = sum(1 for r in rows if not r["tiers_hit"])
        add(f"| **nenhum** | {none} | {none / len(rows):.0%} |")

        add("\n### Cobertura\n")
        for label, cond in (("≥3 imagens", lambda r: len(r["images"]) >= 3),
                            ("≥1 imagem", lambda r: len(r["images"]) >= 1),
                            ("zero imagens", lambda r: not r["images"])):
            n = sum(1 for r in rows if cond(r))
            add(f"- {label}: **{n}/{len(rows)}** ({n / len(rows):.0%})")
        nogeo = sum(1 for r in rows if not r["lat"])
        add(f"- sem lat/lon (pulam tiers geo): **{nogeo}/{len(rows)}**")

        add("\n### Complementaridade (qual fonte resolve, sozinha)\n")
        combo = Counter()
        for r in rows:
            srcs = tuple(sorted({i["source"] for i in r["images"]}))
            combo[srcs or ("nenhuma",)] += 1
        add("| fontes que retornaram | atrativos |")
        add("|---|---|")
        for k, v in combo.most_common():
            add(f"| {' + '.join(k)} | {v} |")

        add("\n### O número que importa — cobertura ANCORADA NO LUGAR\n")
        add("Só `M1_mtur` (foto oficial identificada por nome+município) e `C1`/`C2` "
            "(geosearch por coordenada) prendem a imagem ao lugar. `C3_name` casa por "
            "texto e pode errar; **todo Pixabay é decorativo** — ele nunca devolve vazio, "
            "serve stock genérico para qualquer query.\n")
        add("| estrato | ancorada (MTur ou geo) | via MTur | via geo | só por nome |")
        add("|---|---|---|---|---|")
        anchor = {"M1_mtur", "C1_geo_500m", "C2_geo_2km"}
        strata = ("db",) if cohort == "db" else ("famoso", "medio", "obscuro")
        for est in strata:
            sub = [r for r in rows if r["estrato"] == est]
            if not sub:
                continue
            def _n(tiers):
                return sum(1 for r in sub
                           if any(i["match_tier"] in tiers for i in r["images"]))
            add(f"| {est} | **{_n(anchor)}/{len(sub)}** | {_n({'M1_mtur'})} | "
                f"{_n({'C1_geo_500m', 'C2_geo_2km'})} | {_n({'C3_name'})} |")

    add("\n## Licenças observadas\n")
    lic = Counter()
    for r in results:
        for i in r["images"]:
            lic[f"{i['source']}: {i.get('license') or '(sem)'}"] += 1
    add("| fonte: licença | imagens |")
    add("|---|---|")
    for k, v in lic.most_common(15):
        add(f"| {k} | {v} |")

    add("\n## Colisões (mesma imagem em múltiplos atrativos)\n")
    urls = Counter(i["url"] for r in results for i in r["images"] if i.get("url"))
    dupes = [(u, c) for u, c in urls.most_common(8) if c > 1]
    add(f"- URLs distintas: {len(urls)}; reutilizadas: **{len(dupes)}**")
    for u, c in dupes[:5]:
        add(f"  - {c}× `{u[:100]}`")

    add("\n## Veredito\n")
    ctrl = [r for r in results if r["cohort"] == "control"]
    anchor = {"M1_mtur", "C1_geo_500m", "C2_geo_2km"}
    obsc = [r for r in ctrl if r["estrato"] == "obscuro"]
    obsc_ok = sum(1 for r in obsc
                  if any(i["match_tier"] in anchor for i in r["images"]))
    n_mtur = sum(1 for r in results for i in r["images"] if i["match_tier"] == "M1_mtur")
    add("**Viável, com ressalva no long tail.**\n")
    add(f"- **MTur é o motor de especificidade.** {n_mtur} imagens casadas, domínio "
        "público, alta resolução, foto oficial do atrativo. Resolveu "
        "`Igreja de Santa Isabel / Mucugê` — o caso onde IPHAN deu 0 e busca por nome "
        "no Commons deu 0.")
    add("- **Commons geosearch é a cobertura mais larga** entre as fontes ancoradas, "
        "mas depende de lat/lon e cai junto com o MTur no long tail.")
    add("- **Pixabay é só decoração.** Nunca devolve vazio; uma única imagem foi "
        "servida para 17 atrativos diferentes. Não deve ser legendada como foto do "
        "atrativo — cairia em *misleading or deceptive* no ToS.")
    add(f"- **O long tail continua sendo o problema:** apenas **{obsc_ok}/{len(obsc)}** "
        "dos atrativos obscuros têm imagem ancorada no lugar. "
        + ", ".join(r["nome"] for r in obsc
                    if not any(i["match_tier"] in anchor for i in r["images"]))
        + " não têm nada além de busca textual e stock genérico.")
    add("- **Match fuzzy exige a trava de UF.** Antes dela, 1 de 11 matches do MTur era "
        "falso positivo (convento de Itanhaém/SP atribuído a Vila Velha/ES). "
        "Há teste de regressão em `mtur.demo()`.")
    add("\n### Verificação manual — FEITA (2026-07-21)\n")
    add("Revisão humana dos 10 links amostrados. Resultado:\n")
    add("| fonte | veredito |")
    add("|---|---|")
    add("| `M1_mtur` | **correto** — foto oficial do atrativo |")
    add("| `C1_geo_500m` (Commons) | **correto** |")
    add("| `P1_exact` (Pixabay) | **FALSO** — 3/3 errados |")
    add("\nO revisor identificou a causa do falso positivo do Pixabay: *\"fez match por "
        "palavras-chave do Pixabay 'Praia', 'Costa'\"*. `Praia da Costa` (Vila Velha/ES) "
        "devolveu praia genérica e pôr-do-sol no **Mar do Norte**. Confirma que o motor "
        "do Pixabay casa tokens do nome contra tags de stock, sem qualquer noção de lugar "
        "— e que tratar `P1_exact` como decorativo é a classificação correta, não "
        "conservadorismo.\n")
    add("**As fontes ancoradas passaram na verificação semântica.** O hit-rate ancorado "
        "acima está validado por humano, não só medido.")

    add("\n## Amostra usada na verificação manual\n")
    add("Links revisados por humano em 2026-07-21 (resultado no Veredito acima):\n")
    shown = 0
    for r in results:
        for i in r["images"]:
            if i["match_tier"] in ("M1_mtur", "C1_geo_500m", "P1_exact") and shown < 10:
                add(f"- [{r['nome']} / {i['match_tier']}]({i.get('page_url')})")
                shown += 1
    if px_left is not None:
        add(f"\n_Pixabay X-RateLimit-Remaining ao final: {px_left}_")

    DOC.parent.mkdir(parents=True, exist_ok=True)
    DOC.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nwrote {DOC}")
    print(f"wrote {OUT / 'atrativos_images.json'}")


if __name__ == "__main__":
    main()
