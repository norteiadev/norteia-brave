"""SPIKE (not production): validate OpenStreetMap Nominatim geocoding for
coordless TripAdvisor attraction → IBGE municipality resolution.

Flow per attraction:
  name (+UF, Brazil) → Nominatim search → lat/lon → nearest IBGE municipality
  (haversine, within the UF) → compare to expected municipality.

Free, no API key. Respects Nominatim usage policy: custom User-Agent + >=1 req/s.
Run: .venv/bin/python scripts/spike_nominatim_geo.py
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request

from brave.lanes.tripadvisor.ibge import haversine_km, load_ibge_csv

UA = "norteia-brave-spike/0.1 (leandro.freire08@gmail.com)"
UF_NAME = {
    "RJ": "Rio de Janeiro",
    "MG": "Minas Gerais",
    "BA": "Bahia",
    "PR": "Paraná",
    "MA": "Maranhão",
    "PE": "Pernambuco",
    "AM": "Amazonas",
    "RS": "Rio Grande do Sul",
}

# (attraction name as TripAdvisor would list it, UF, expected IBGE município)
CASES = [
    ("Cristo Redentor", "RJ", "Rio de Janeiro"),
    ("Pão de Açúcar", "RJ", "Rio de Janeiro"),
    ("Instituto Inhotim", "MG", "Brumadinho"),
    ("Cachoeira do Tabuleiro", "MG", "Conceição do Mato Dentro"),
    ("Pelourinho", "BA", "Salvador"),
    ("Cataratas do Iguaçu", "PR", "Foz do Iguaçu"),
    ("Parque Nacional dos Lençóis Maranhenses", "MA", "Barreirinhas"),
    ("Marco Zero", "PE", "Recife"),
    ("Teatro Amazonas", "AM", "Manaus"),
    ("Cânion do Itaimbezinho", "RS", "Cambará do Sul"),
]


def nominatim(name: str, uf: str) -> tuple[float, float] | None:
    q = f"{name}, {UF_NAME.get(uf, uf)}, Brazil"
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
        {"q": q, "format": "json", "limit": 1, "countrycodes": "br"}
    )
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode())
    if not data:
        return None
    return float(data[0]["lat"]), float(data[0]["lon"])


def nearest_municipio(lat: float, lng: float, uf: str, records):
    uf_recs = [r for r in records if r.uf == uf]
    best = min(uf_recs, key=lambda r: haversine_km(lat, lng, r.lat, r.lng))
    return best, haversine_km(lat, lng, best.lat, best.lng)


def main() -> None:
    records = load_ibge_csv("data/ibge/ibge_municipios.csv")
    print(f"Loaded {len(records)} IBGE municipalities\n")
    hits = 0
    geo_ok = 0
    for name, uf, expected in CASES:
        try:
            coords = nominatim(name, uf)
        except Exception as exc:  # noqa: BLE001
            print(f"✗ {name:42s} [{uf}] Nominatim ERROR: {exc}")
            time.sleep(1.2)
            continue
        if coords is None:
            print(f"✗ {name:42s} [{uf}] no Nominatim result")
            time.sleep(1.2)
            continue
        geo_ok += 1
        lat, lng = coords
        muni, dist = nearest_municipio(lat, lng, uf, records)
        ok = muni.nome == expected
        hits += ok
        mark = "✓" if ok else "≈"
        print(
            f"{mark} {name:42s} [{uf}] → {muni.nome:28s} "
            f"{dist:6.1f}km  (expected {expected})"
        )
        time.sleep(1.2)  # Nominatim rate limit: >=1 req/s
    print(
        f"\nGeocoded: {geo_ok}/{len(CASES)}   "
        f"Exact municipality match: {hits}/{len(CASES)}"
    )


if __name__ == "__main__":
    main()
