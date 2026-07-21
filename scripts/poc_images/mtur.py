"""Match atrativos against the harvested MTur Destinos index.

Design note -- this differs from the original plan, which assumed Commons filenames
preserved Flickr's `Photographer_Attraction_Municipality_UF` convention. Inspection
of real records shows they do NOT; Commons renames on import and the surviving
patterns are heterogeneous:

    AdalmirChixaro RioUiacurapa Parintis AM (40242982805).jpg   <- Flickr-derived
    2022-05-17 - Estádio Ilha do Retiro, Recife - PE.jpg        <- date + place
    19-03-2018 Paço do Frevo.jpg                                <- date + place only

What IS reliable is the Commons PLACE CATEGORY, present on ~98% of files and
curated by a human at import time:

    ['Estádio Ilha do Retiro', 'Ilha do Retiro']
    ['Interior of Paço do Frevo']
    ['Parintins (Amazonas)']

So the match runs against a haystack of (place categories + object name), not a
parsed title. Categories carry the attraction name directly, which is a stronger
signal than anything the filename gives us.

No coordinates exist on this lane (0/100 sampled files geotagged), so there is no
geo fallback here -- name/municipality matching is the only path.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

from rapidfuzz import fuzz

INDEX = Path(__file__).parent / "out" / "mtur_index.json"

_UF_BY_STATE = {
    "acre": "AC", "alagoas": "AL", "amapa": "AP", "amazonas": "AM", "bahia": "BA",
    "ceara": "CE", "distrito federal": "DF", "espirito santo": "ES", "goias": "GO",
    "maranhao": "MA", "mato grosso": "MT", "mato grosso do sul": "MS",
    "minas gerais": "MG", "para": "PA", "paraiba": "PB", "parana": "PR",
    "pernambuco": "PE", "piaui": "PI", "rio de janeiro": "RJ",
    "rio grande do norte": "RN", "rio grande do sul": "RS", "rondonia": "RO",
    "roraima": "RR", "santa catarina": "SC", "sao paulo": "SP", "sergipe": "SE",
    "tocantins": "TO",
}
_UFS = set(_UF_BY_STATE.values())

# "Photos with people" carry an expired usage window -- see is_restricted().
_HUMANIZED = re.compile(r"fotos?\s*humanizadas?\s*2018|humanizada", re.I)


def fold(s: str) -> str:
    """Lowercase, strip accents, collapse whitespace."""
    s = "".join(
        c for c in unicodedata.normalize("NFKD", (s or "").lower())
        if unicodedata.category(c) != "Mn"
    )
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", s)).strip()


def uf_hint(rec: dict) -> str | None:
    """Best-effort UF for a record: state name in a category paren, or a UF code anywhere.

    The code must be scanned across the WHOLE title, not just the tail. Measured
    false positive that motivated this: "Convento Nossa Senhora da Penha"
    (Vila Velha/ES) matched

        ROGERIO_CASSIMIRO-SP_Itanhaem_convento_nossa_senhora_da_conceicao

    at confidence 89 -- a different convent, in a different state. The "SP" sits
    glued to the photographer at the START of the string, so a tail-only scan
    returned None, the UF gate was skipped, and the fuzzy name score did the rest.

    Case-sensitive on purpose (real UF codes are uppercase) and ambiguity-averse:
    two different UF codes in one title yields None rather than a coin flip.
    """
    for cat in rec.get("place_categories") or []:
        m = re.search(r"\(([^)]+)\)", cat)
        if m:
            uf = _UF_BY_STATE.get(fold(m.group(1)))
            if uf:
                return uf
    found = {t for t in re.findall(r"(?<![A-Za-z])([A-Z]{2})(?![A-Za-z])",
                                   rec.get("object_name") or "") if t in _UFS}
    return found.pop() if len(found) == 1 else None


def is_restricted(rec: dict) -> bool:
    """True for the ~842 'fotos humanizadas 2018' whose usage window expired 2023-04-03.

    MTur's Flickr profile states the free-use grant covers photos tagged
    `mturdestinos`, "exceto as imagens com a tag 'fotos humanizadas 2018' que
    possuem pessoas onde o direito de uso e pelo periodo de 05 (cinco) anos a
    contar do dia 03 de Abril de 2018."

    Commons has NO category for that tag, so it cannot be filtered upstream --
    this is a best-effort text heuristic on our side, and the POC reports how many
    records it could not classify either way.
    """
    blob = " ".join(str(rec.get(k) or "") for k in ("title", "object_name", "description"))
    return bool(_HUMANIZED.search(blob))


def load_index(path: Path = INDEX) -> list[dict]:
    """Load, drop license-rejected and restricted records, precompute haystacks."""
    recs = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for r in recs:
        if r.get("license_verdict") != "ok" or is_restricted(r):
            continue
        hay = " ".join((r.get("place_categories") or []) + [r.get("object_name") or ""])
        r["_hay"] = fold(hay)
        r["_uf"] = uf_hint(r)
        out.append(r)
    return out


def match(name: str, municipio: str, uf: str, index: list[dict], top: int = 3) -> list[dict]:
    """Score every candidate; return the best `top` above threshold.

    Gate on UF when the record exposes one -- Brazilian municipality names repeat
    across states (Bonito/MS vs Bonito/PE vs Bonito/BA), and an ungated name match
    would happily attach the wrong state's photo.
    """
    fname, fmuni = fold(name), fold(municipio)
    hits = []
    for r in index:
        if r["_uf"] and uf and r["_uf"] != uf:
            continue
        name_score = fuzz.token_set_ratio(fname, r["_hay"])
        muni_score = fuzz.partial_token_set_ratio(fmuni, r["_hay"]) if fmuni else 0
        # A strong name hit stands alone. A weaker one needs the municipality to
        # corroborate -- otherwise "Igreja Matriz" matches half of Brazil.
        if name_score >= 88:
            conf, via = name_score, "nome"
        elif name_score >= 72 and muni_score >= 88:
            conf, via = round(0.7 * name_score + 0.3 * muni_score), "nome+municipio"
        else:
            continue
        hits.append({**{k: v for k, v in r.items() if not k.startswith("_")},
                     "match_confidence": conf, "match_via": via,
                     "uf_hint": r["_uf"]})
    hits.sort(key=lambda h: (-h["match_confidence"], -(h["width"] * h["height"])))
    return hits[:top]


def demo() -> None:
    """Self-check: no network, no index file needed."""
    recs = [
        {"place_categories": ["Estádio Ilha do Retiro"], "object_name": "2022 - Recife - PE",
         "width": 4961, "height": 3508, "license_verdict": "ok", "title": "a"},
        {"place_categories": ["Parintins (Amazonas)"], "object_name": "RioUiacurapa Parintis AM",
         "width": 23400, "height": 15600, "license_verdict": "ok", "title": "b"},
        {"place_categories": ["Bonito (Mato Grosso do Sul)"], "object_name": "Gruta MS",
         "width": 4000, "height": 3000, "license_verdict": "ok", "title": "c"},
    ]
    for r in recs:
        r["_hay"] = fold(" ".join(r["place_categories"] + [r["object_name"]]))
        r["_uf"] = uf_hint(r)

    assert recs[1]["_uf"] == "AM", recs[1]["_uf"]
    assert recs[2]["_uf"] == "MS", recs[2]["_uf"]

    # Regression: UF code glued to the photographer at the START of the title.
    # This exact record produced a real false positive (see uf_hint docstring).
    itanhaem = {
        "place_categories": [],
        "object_name": "ROGERIO_CASSIMIRO-SP_Itanhaem_convento_nossa_senhora_da_conceicao",
        "width": 4000, "height": 3000, "license_verdict": "ok", "title": "d",
    }
    itanhaem["_hay"] = fold(itanhaem["object_name"])
    itanhaem["_uf"] = uf_hint(itanhaem)
    assert itanhaem["_uf"] == "SP", itanhaem["_uf"]
    assert not match("Convento Nossa Senhora da Penha", "Vila Velha", "ES", [itanhaem]), \
        "UF gate must reject an SP convent for an ES attraction"

    hit = match("Estádio Ilha do Retiro", "Recife", "PE", recs)
    assert hit and hit[0]["title"] == "a", hit

    # UF gate: same attraction name, wrong state -> no match.
    assert not match("Gruta do Lago Azul", "Bonito", "PE", recs)

    # Accent/case folding.
    assert fold("Cachoeira do Buracão") == "cachoeira do buracao"

    # Restricted-photo heuristic.
    assert is_restricted({"title": "x", "object_name": "fotos humanizadas 2018", "description": ""})
    assert not is_restricted({"title": "x", "object_name": "Praia", "description": ""})
    print("mtur self-check: OK")


if __name__ == "__main__":
    demo()
