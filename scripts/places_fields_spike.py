"""SPIKE (not production): does Google Places carry the data for the 6 editorial
columns of norteia-api's `attractions` table?

Target columns: accessibility, how_to_get_there, tips, safety_alerts,
local_infrastructure, curiosities.

Flow per sample attraction:
  name + municipio + UF -> Places searchText -> place_id -> Places getPlace with an
  EXPANDED field mask -> tally coverage -> render what the PT-BR column value would
  look like.

Reads/writes NO database. Costs ~1 Text Search + ~1 Place Details per sample, both
inside their 10k/month free tiers. Adding fields does not raise the bill: a request
is billed at the HIGHEST SKU in its mask (one charge, not one per tier), and the
production mask already asks for `reviews` + `editorialSummary` = Enterprise +
Atmosphere, the top tier.

Run:
    set -a; . ./.env; set +a
    .venv/bin/python scripts/places_fields_spike.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx

BASE = "https://places.googleapis.com/v1"
# Mechanical output only. The hand-written analysis lives in the sibling
# places-extra-fields-spike.md and must NOT be clobbered by a re-run.
REPORT = Path("docs/poc/places-extra-fields-spike.auto.md")

# Diverse on purpose: attraction type (igreja/praia/parque/museu/gruta/mirante/
# cachoeira/centro historico) x region x capital-vs-interior. Google's coverage is
# known to thin out away from dense urban commerce, which is exactly the axis that
# decides whether these columns are fillable for OUR corpus (mostly non-commercial
# outdoor attractions in the interior).
SAMPLE: list[tuple[str, str, str]] = [
    ("Cristo Redentor", "Rio de Janeiro", "RJ"),
    ("Theatro Municipal do Rio de Janeiro", "Rio de Janeiro", "RJ"),
    ("Centro Historico de Paraty", "Paraty", "RJ"),
    ("Museu de Arte de Sao Paulo MASP", "Sao Paulo", "SP"),
    ("Convento da Penha", "Vila Velha", "ES"),
    ("Praia de Camburi", "Vitoria", "ES"),
    ("Elevador Lacerda", "Salvador", "BA"),
    ("Mirante do Pai Inacio", "Palmeiras", "BA"),
    ("Cachoeira da Fumaca", "Palmeiras", "BA"),
    ("Igreja de Sao Francisco de Assis", "Ouro Preto", "MG"),
    ("Gruta do Lago Azul", "Bonito", "MS"),
    ("Cataratas do Iguacu", "Foz do Iguacu", "PR"),
    ("Teatro Amazonas", "Manaus", "AM"),
    ("Praia dos Carneiros", "Tamandare", "PE"),
    ("Lencois Maranhenses", "Barreirinhas", "MA"),
]

# Already in production today (brave/clients/places.py:54-68).
CURRENT_FIELDS = [
    "id",
    "displayName",
    "formattedAddress",
    "types",
    "location",
    "businessStatus",
    "regularOpeningHours",
    "editorialSummary",
    "priceLevel",
]

# Candidates this spike is measuring. Grouped by the API column they would feed.
CANDIDATE_FIELDS = [
    # -> accessibility
    "accessibilityOptions",
    # -> local_infrastructure
    "parkingOptions",
    "restroom",
    "goodForChildren",
    "goodForGroups",
    "paymentOptions",
    "outdoorSeating",
    "allowsDogs",
    # -> how_to_get_there
    "addressDescriptor",
    # -> tips / curiosities (docs say EN-only, US+India; measuring to confirm)
    "generativeSummary",
    "reviewSummary",
    "neighborhoodSummary",
    # free riders: the call is already paid for, coverage is worth knowing
    "rating",
    "userRatingCount",
    "priceRange",
    "openingDate",
    "subDestinations",
    "containingPlaces",
    "googleMapsLinks",
    "timeZone",
]

BOOLEAN_OBJECTS = {"accessibilityOptions", "parkingOptions", "paymentOptions"}

PT_LABELS = {
    "wheelchairAccessibleEntrance": "entrada acessivel para cadeirantes",
    "wheelchairAccessibleParking": "estacionamento acessivel",
    "wheelchairAccessibleRestroom": "sanitario acessivel",
    "wheelchairAccessibleSeating": "assentos acessiveis",
    "freeParkingLot": "estacionamento gratuito",
    "paidParkingLot": "estacionamento pago",
    "freeStreetParking": "vagas gratuitas na rua",
    "paidStreetParking": "vagas pagas na rua",
    "valetParking": "servico de manobrista",
    "freeGarageParking": "garagem gratuita",
    "paidGarageParking": "garagem paga",
    "acceptsCreditCards": "aceita cartao de credito",
    "acceptsDebitCards": "aceita cartao de debito",
    "acceptsCashOnly": "aceita somente dinheiro",
    "acceptsNfc": "aceita pagamento por aproximacao",
}


def api_key() -> str:
    key = os.environ.get("BRAVE_PLACES_API_KEY", "").strip()
    if not key:
        sys.exit("BRAVE_PLACES_API_KEY not set. Run: set -a; . ./.env; set +a")
    return key


def search_place_id(client: httpx.Client, key: str, name: str, uf: str) -> str | None:
    r = client.post(
        f"{BASE}/places:searchText",
        headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": "places.id,places.displayName"},
        json={
            "textQuery": f"{name} {uf} Brasil",
            "maxResultCount": 1,
            "languageCode": "pt-BR",
            "regionCode": "BR",
        },
        timeout=30.0,
    )
    if r.status_code != 200:
        print(f"  ! searchText {r.status_code}: {r.text[:200]}")
        return None
    places = r.json().get("places") or []
    return places[0]["id"] if places else None


def get_place(
    client: httpx.Client, key: str, place_id: str, fields: list[str]
) -> tuple[dict[str, Any] | None, list[str]]:
    """Fetch with the full mask. On a 400, bisect field-by-field to find the culprits.

    Returns (payload, rejected_fields). A single unsupported field must not sink the
    whole measurement, so the rejects are reported and dropped instead of raising.
    """
    r = client.get(
        f"{BASE}/places/{place_id}",
        headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": ",".join(fields)},
        params={"languageCode": "pt-BR", "regionCode": "BR"},
        timeout=30.0,
    )
    if r.status_code == 200:
        return r.json(), []
    if r.status_code != 400:
        print(f"  ! getPlace {r.status_code}: {r.text[:200]}")
        return None, []

    # ponytail: linear probe, not a real bisect. 20 fields, runs once per spike.
    rejected = []
    for f in fields:
        probe = client.get(
            f"{BASE}/places/{place_id}",
            headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": f"id,{f}"},
            params={"languageCode": "pt-BR", "regionCode": "BR"},
            timeout=30.0,
        )
        if probe.status_code == 400:
            rejected.append(f)
    good = [f for f in fields if f not in rejected]
    retry = client.get(
        f"{BASE}/places/{place_id}",
        headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": ",".join(good)},
        params={"languageCode": "pt-BR", "regionCode": "BR"},
        timeout=30.0,
    )
    return (retry.json() if retry.status_code == 200 else None), rejected


def render_accessibility(payload: dict[str, Any]) -> str | None:
    opts = payload.get("accessibilityOptions") or {}
    on = [PT_LABELS[k] for k, v in opts.items() if v is True and k in PT_LABELS]
    if not on:
        return None
    return "Local com " + ", ".join(on) + "."


def render_local_infrastructure(payload: dict[str, Any]) -> str | None:
    bits: list[str] = []
    for group in ("parkingOptions", "paymentOptions"):
        opts = payload.get(group) or {}
        bits += [PT_LABELS[k] for k, v in opts.items() if v is True and k in PT_LABELS]
    flags = {
        "restroom": "sanitarios disponiveis",
        "goodForChildren": "adequado para criancas",
        "goodForGroups": "adequado para grupos",
        "outdoorSeating": "area externa com assentos",
        "allowsDogs": "permite animais",
    }
    bits += [txt for k, txt in flags.items() if payload.get(k) is True]
    if not bits:
        return None
    return "Estrutura no local: " + ", ".join(bits) + "."


def render_how_to_get_there(payload: dict[str, Any]) -> str | None:
    desc = payload.get("addressDescriptor") or {}
    landmarks = desc.get("landmarks") or []
    areas = desc.get("areas") or []
    parts: list[str] = []
    for lm in landmarks[:3]:
        nome = (lm.get("displayName") or {}).get("text")
        if not nome:
            continue
        dist = lm.get("straightLineDistanceMeters")
        parts.append(f"{nome} (~{int(dist)} m)" if dist else nome)
    out = []
    if areas:
        area_names = [(a.get("displayName") or {}).get("text") for a in areas[:2]]
        area_names = [a for a in area_names if a]
        if area_names:
            out.append("Fica em " + " / ".join(area_names) + ".")
    if parts:
        out.append("Referencias proximas: " + ", ".join(parts) + ".")
    return " ".join(out) or None


def main() -> None:
    key = api_key()
    fields = CURRENT_FIELDS + CANDIDATE_FIELDS
    rows: list[dict[str, Any]] = []
    rejected_global: set[str] = set()

    with httpx.Client() as client:
        for name, municipio, uf in SAMPLE:
            print(f"- {name} ({municipio}/{uf})")
            pid = search_place_id(client, key, name, uf)
            if not pid:
                print("  ! no place_id")
                rows.append({"name": name, "municipio": municipio, "uf": uf, "payload": None})
                continue
            payload, rejected = get_place(client, key, pid, fields)
            if rejected:
                print(f"  ! rejected fields: {', '.join(rejected)}")
                rejected_global.update(rejected)
                fields = [f for f in fields if f not in rejected]
            got = sorted(k for k in CANDIDATE_FIELDS if (payload or {}).get(k) not in (None, [], {}))
            print(f"  ok {pid} | candidates present: {', '.join(got) or '(none)'}")
            rows.append(
                {"name": name, "municipio": municipio, "uf": uf, "place_id": pid, "payload": payload}
            )

    write_report(rows, sorted(rejected_global))


def write_report(rows: list[dict[str, Any]], rejected: list[str]) -> None:
    resolved = [r for r in rows if r.get("payload")]
    n = len(resolved)

    def present(field: str) -> int:
        return sum(1 for r in resolved if r["payload"].get(field) not in (None, [], {}))

    def any_true(field: str) -> int:
        c = 0
        for r in resolved:
            obj = r["payload"].get(field) or {}
            if isinstance(obj, dict) and any(v is True for v in obj.values()):
                c += 1
        return c

    lines = [
        "# Spike: campos extras do Google Places para as 6 colunas editoriais",
        "",
        f"Amostra: {len(rows)} atrativos brasileiros, {n} resolvidos via Text Search.",
        "Chamadas: 1 Text Search + 1 Place Details por atrativo, `languageCode=pt-BR`, "
        "`regionCode=BR`. Nenhuma tabela do Postgres foi lida ou escrita.",
        "",
    ]
    if rejected:
        lines += [f"**Campos rejeitados pela API (400):** `{'`, `'.join(rejected)}`", ""]

    lines += [
        "## 1. Cobertura por campo",
        "",
        "| Campo Places | Presente | % | Com ao menos um `true` |",
        "|---|---|---|---|",
    ]
    for f in CANDIDATE_FIELDS:
        if f in rejected:
            lines.append(f"| `{f}` | rejeitado (400) | - | - |")
            continue
        p = present(f)
        pct = f"{(100 * p / n):.0f}%" if n else "-"
        extra = f"{any_true(f)}/{n}" if f in BOOLEAN_OBJECTS else "-"
        lines.append(f"| `{f}` | {p}/{n} | {pct} | {extra} |")

    lines += ["", "## 2. Veredito por coluna da API", ""]
    renders = {
        "accessibility": render_accessibility,
        "local_infrastructure": render_local_infrastructure,
        "how_to_get_there": render_how_to_get_there,
    }
    for col, fn in renders.items():
        filled = [(r["name"], fn(r["payload"])) for r in resolved]
        filled = [(nm, txt) for nm, txt in filled if txt]
        pct = (100 * len(filled) / n) if n else 0
        verdict = "VIAVEL" if pct >= 60 else ("PARCIAL" if pct > 0 else "SEM FONTE")
        lines += [f"### `{col}` — **{verdict}** ({len(filled)}/{n} = {pct:.0f}%)", ""]
        for nm, txt in filled[:3]:
            lines.append(f"- **{nm}**: {txt}")
        if not filled:
            lines.append("- nenhum atrativo da amostra produziu valor")
        lines.append("")

    ai = ["generativeSummary", "reviewSummary", "neighborhoodSummary"]
    ai_hits = sum(present(f) for f in ai if f not in rejected)
    for col in ("tips", "safety_alerts", "curiosities"):
        verdict = "SEM FONTE" if ai_hits == 0 else "REVISAR"
        lines += [
            f"### `{col}` — **{verdict}**",
            "",
            "Nao existe campo no Places para esta coluna. Os unicos candidatos "
            f"(`generativeSummary`/`reviewSummary`/`neighborhoodSummary`) retornaram "
            f"{ai_hits} ocorrencias em pt-BR/BR nesta amostra.",
            "",
        ]

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    raw = REPORT.with_suffix(".raw.json")
    raw.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nreport: {REPORT}\nraw:    {raw}")


if __name__ == "__main__":
    if "--selfcheck" in sys.argv:
        # Offline check of the renderers — no network, no key.
        p = {
            "accessibilityOptions": {
                "wheelchairAccessibleEntrance": True,
                "wheelchairAccessibleParking": False,
            },
            "restroom": True,
            "parkingOptions": {"freeParkingLot": True},
            "addressDescriptor": {
                "landmarks": [{"displayName": {"text": "Praca da Se"}, "straightLineDistanceMeters": 120.4}],
                "areas": [{"displayName": {"text": "Centro Historico"}}],
            },
        }
        assert render_accessibility(p) == "Local com entrada acessivel para cadeirantes."
        assert render_accessibility({}) is None
        assert "estacionamento gratuito" in render_local_infrastructure(p)
        assert "sanitarios disponiveis" in render_local_infrastructure(p)
        assert render_local_infrastructure({}) is None
        htg = render_how_to_get_there(p)
        assert "Centro Historico" in htg and "Praca da Se (~120 m)" in htg
        assert render_how_to_get_there({}) is None
        print("selfcheck ok")
    else:
        main()
