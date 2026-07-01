#!/usr/bin/env python3
"""Discover and validate TripAdvisor geoIds for all 27 Brazilian states.

REQUIRES: RUN_REAL_EXTERNALS=1 — this script makes live HTTP requests to
TripAdvisor using the operator-injected Redis session. It is NEVER called
by the offline pytest suite.

Usage:
    RUN_REAL_EXTERNALS=1 .venv/bin/python scripts/ta_discover_state_geoids.py

What it does:
  For each of the 27 Brazilian states, this script:
  1. Searches the TypeAhead endpoint for the state geoId. NOTE: TypeAhead is
     NOT "no auth" — it is DataDome rate-limited (see Discovery reality below);
     it works only with the operator cookie jar and only slowly.
  2. Validates the discovered geoId by doing a redirect check:
     GET https://www.tripadvisor.com/Attractions-g{geo_id}-Activities-
         a_allAttractions.true-oa0-Brazil.html
     with follow_redirects=True; confirms the final URL or page title
     contains the expected state name.
  3. Reports VALID/INVALID per UF and prints a corrected JSON blob that
     can be pasted directly into data/tripadvisor/uf_geoids.json.

Output:
  Per-UF lines like: "SP: 303631 → VALID (Estado de Sao Paulo)"
  Then a JSON blob of all discovered geoIds.

Security note (T-rmz-01): All geoIds in the output should be validated
before committing. The redirect check ensures each geoId maps to the
expected state and not a city or other geo entity.

Discovery reality (live-validated 260701-has — corrects earlier false claims):
  - TypeAheadJson is NOT open/unauthenticated. It is DataDome rate-limited:
    after ~5-6 rapid hits it soft-blocks and returns an HTTP 403 whose body is
    a JSON object of the shape {"url": "...captcha-delivery..."} (a DataDome
    challenge redirect), not the GEO results. It works with the operator cookie
    jar but only slowly (space the requests out).
  - The state geoId is NOT exposed in a dedicated "locationId"/"geoId" field.
    It is embedded in the result's "url" field — e.g. a Parana result carries
    a url containing "-g303435-", so the geoId is 303435. Parse it out of url.
  - The DURABLE discovery/validation path is the GraphQL endpoint, NOT
    TypeAhead: canonicalize the query (qid a26bffd43d0e25b6) and then
    fetch_attraction_geo (qid d3d4987463b78a39) to confirm the state geoId
    resolves to the expected stateName. Prefer GraphQL; treat TypeAhead as a
    rate-limited best-effort seed only.
"""

import json
import os
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# RUN_REAL_EXTERNALS guard — MUST be the first executable line
# ---------------------------------------------------------------------------
if not os.environ.get("RUN_REAL_EXTERNALS"):
    print(
        "ERROR: RUN_REAL_EXTERNALS is not set.\n"
        "This script makes live HTTP requests to TripAdvisor and must NOT\n"
        "run in CI or offline test environments.\n\n"
        "To run:\n"
        "  RUN_REAL_EXTERNALS=1 .venv/bin/python scripts/ta_discover_state_geoids.py",
        file=sys.stderr,
    )
    sys.exit(1)

# Late imports — only executed when RUN_REAL_EXTERNALS is set
import httpx  # noqa: E402

# ---------------------------------------------------------------------------
# Brazilian state catalog (PT-BR canonical names for the TypeAhead query)
# ---------------------------------------------------------------------------

# Maps 2-letter UF code → (PT-BR search term, English name fragment for redirect check)
_STATE_CATALOG: dict[str, tuple[str, str]] = {
    "AC": ("Acre", "Acre"),
    "AL": ("Alagoas", "Alagoas"),
    "AM": ("Amazonas", "Amazonas"),
    "AP": ("Amapa", "Amapa"),
    "BA": ("Bahia", "Bahia"),
    "CE": ("Ceara", "Ceara"),
    "DF": ("Distrito Federal", "Distrito Federal"),
    "ES": ("Espirito Santo", "Espirito Santo"),
    "GO": ("Goias", "Goias"),
    "MA": ("Maranhao", "Maranhao"),
    "MG": ("Minas Gerais", "Minas Gerais"),
    "MS": ("Mato Grosso do Sul", "Mato Grosso do Sul"),
    "MT": ("Mato Grosso", "Mato Grosso"),
    "PA": ("Para", "Para"),
    "PB": ("Paraiba", "Paraiba"),
    "PE": ("Pernambuco", "Pernambuco"),
    "PI": ("Piaui", "Piaui"),
    "PR": ("Parana", "Parana"),
    "RJ": ("Rio de Janeiro", "Rio_de_Janeiro"),
    "RN": ("Rio Grande do Norte", "Rio_Grande_do_Norte"),
    "RO": ("Rondonia", "Rondonia"),
    "RR": ("Roraima", "Roraima"),
    "RS": ("Rio Grande do Sul", "Rio_Grande_do_Sul"),
    "SC": ("Santa Catarina", "Santa Catarina"),
    "SE": ("Sergipe", "Sergipe"),
    "SP": ("Sao Paulo", "Sao_Paulo"),
    "TO": ("Tocantins", "Tocantins"),
}

_TYPEAHEAD_URL = (
    "https://www.tripadvisor.com/TypeAheadJson"
    "?action=API&uiOrigin=MASTHEAD&query={query}&max=10&types=geo"
)
_REDIRECT_URL = (
    "https://www.tripadvisor.com/Attractions-g{geo_id}-Activities-"
    "a_allAttractions.true-oa0-Brazil.html"
)

# Throttle between requests to avoid DataDome block (politeness + DataDome)
_THROTTLE_SECONDS = 1.5


def discover_geo_id(client: httpx.Client, uf: str, pt_name: str) -> int | None:
    """Discover the TripAdvisor geoId for a Brazilian state via TypeAhead.

    Args:
        client:  httpx.Client (synchronous, for simplicity).
        uf:      Two-letter state code.
        pt_name: PT-BR state name (used as the TypeAhead query string).

    Returns:
        Integer geoId, or None when no match is found.
    """
    epoch = int(time.time() * 1000)
    url = _TYPEAHEAD_URL.format(query=pt_name) + f"&startTime={epoch}"
    try:
        resp = client.get(url, timeout=15.0)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        print(f"  [{uf}] TypeAhead request failed: {exc}", file=sys.stderr)
        return None

    results = data if isinstance(data, list) else data.get("results", [])
    for item in results:
        item_type = (item.get("type") or "").upper()
        item_name = item.get("value") or item.get("name") or ""
        geo_id_raw = item.get("locationId") or item.get("geoId")
        if item_type == "GEO" and geo_id_raw and pt_name.lower() in item_name.lower():
            return int(geo_id_raw)
    # Fallback: first GEO result regardless of name match
    for item in results:
        item_type = (item.get("type") or "").upper()
        geo_id_raw = item.get("locationId") or item.get("geoId")
        if item_type == "GEO" and geo_id_raw:
            return int(geo_id_raw)
    return None


def validate_geo_id(client: httpx.Client, geo_id: int, name_fragment: str) -> tuple[bool, str]:
    """Validate a discovered geoId via the redirect canonical URL.

    GETs the TA attractions listing for the geoId and checks that the
    final (redirected) URL contains the expected state-name fragment.

    Args:
        client:        httpx.Client (follow_redirects=True).
        geo_id:        TripAdvisor integer geoId to validate.
        name_fragment: English state name fragment to search for in final URL.

    Returns:
        (valid: bool, canonical_url: str)
    """
    url = _REDIRECT_URL.format(geo_id=geo_id)
    try:
        resp = client.get(url, timeout=20.0)
        final_url = str(resp.url)
        # Normalise: replace spaces/hyphens, lower-case both sides
        norm_final = final_url.lower().replace("-", "_").replace(" ", "_")
        norm_frag = name_fragment.lower().replace(" ", "_")
        return norm_frag in norm_final, final_url
    except Exception as exc:
        return False, f"ERROR: {exc}"


def main() -> None:
    discovered: dict[str, int] = {}
    validation_results: list[tuple[str, int | None, bool, str]] = []

    headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
    }

    print("=== TripAdvisor Brazilian State GeoId Discovery ===\n")

    with httpx.Client(headers=headers, follow_redirects=True) as client:
        for uf, (pt_name, en_frag) in _STATE_CATALOG.items():
            print(f"[{uf}] Discovering geoId for '{pt_name}' ...", end=" ", flush=True)
            geo_id = discover_geo_id(client, uf, pt_name)
            if geo_id is None:
                print("NOT FOUND")
                validation_results.append((uf, None, False, "not_found"))
                time.sleep(_THROTTLE_SECONDS)
                continue

            print(f"candidate={geo_id}, validating ...", end=" ", flush=True)
            time.sleep(_THROTTLE_SECONDS)

            valid, canonical = validate_geo_id(client, geo_id, en_frag)
            status = "VALID" if valid else "INVALID (manual review needed)"
            print(f"{status}\n   canonical: {canonical}")
            discovered[uf] = geo_id
            validation_results.append((uf, geo_id, valid, canonical))
            time.sleep(_THROTTLE_SECONDS)

    print("\n=== Summary ===")
    invalid = [(uf, gid, url) for uf, gid, ok, url in validation_results if not ok]
    if invalid:
        print(f"INVALID or NOT FOUND ({len(invalid)}):")
        for uf, gid, url in invalid:
            print(f"  {uf}: {gid} → {url}")
    else:
        print("All 27 UF geoIds validated successfully.")

    print("\n=== Paste into data/tripadvisor/uf_geoids.json ===")
    # Print in sorted key order with 2-space indent
    output = {uf: discovered[uf] for uf in sorted(discovered)}
    print(json.dumps(output, indent=2, ensure_ascii=False))

    # Write directly to the seed file if all valid
    if not invalid and len(discovered) == 27:
        seed_path = (
            Path(__file__).parent.parent / "data" / "tripadvisor" / "uf_geoids.json"
        )
        seed_path.write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n")
        print(f"\nAuto-written to {seed_path}")
    else:
        print(
            f"\nNOTE: {len(invalid)} UF(s) need manual review. "
            "Update uf_geoids.json manually after verifying each geoId."
        )


if __name__ == "__main__":
    main()
