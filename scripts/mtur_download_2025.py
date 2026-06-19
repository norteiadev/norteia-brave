"""Download the full 2025 Mtur categorization dataset (all 27 UFs) → CSV.

Automates the manual XLSX download described in data/mtur/README. Pulls the
public "Categorização Simplificada" XLS per UF from the Mapa do Turismo
backend, extracts the 5 columns MturClient needs, and writes the merged
data/mtur/municipios_mtur_2025.csv (newest filename → loader picks it up).

Endpoint (discovered from the portal's AngularJS app.js / map.ctrl.js):
  GET {BASE}/api/public/relatorio/relatorio-categorizacao-simplificado-xls?sgUf={UF}
  BASE = https://sistema.mapa.turismo.gov.br/geolocalizacao

The simplified report's header row (row index 4) is:
  Município | UF | Código IBGE | Região Turística | Categoria | <metrics...>

Output schema (matches brave/clients/mtur.py MturClient._load_csv):
  co_municipio,no_municipio,sg_uf,categoria,no_regiao_turistica

Usage:
  .venv/bin/python -m scripts.mtur_download_2025            # all 27 UFs
  .venv/bin/python -m scripts.mtur_download_2025 BA RJ SP   # subset
  Requires: openpyxl (operator-only: `uv pip install openpyxl`).
"""

from __future__ import annotations

import csv
import io
import sys
import time
from pathlib import Path

import httpx

try:
    import openpyxl
except ImportError:  # pragma: no cover - operator guard
    print("ERROR: openpyxl not installed. Run: uv pip install openpyxl")
    sys.exit(1)

BASE = "https://sistema.mapa.turismo.gov.br/geolocalizacao"
ENDPOINT = "/api/public/relatorio/relatorio-categorizacao-simplificado-xls"

ALL_UFS = [
    "AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO",
    "MA", "MG", "MS", "MT", "PA", "PB", "PE", "PI", "PR",
    "RJ", "RN", "RO", "RR", "RS", "SC", "SE", "SP", "TO",
]

OUTPUT_SCHEMA = ["co_municipio", "no_municipio", "sg_uf", "categoria", "no_regiao_turistica"]

# Header labels in the simplified XLS → our CSV columns.
_COL = {
    "no_municipio": "Município",
    "sg_uf": "UF",
    "co_municipio": "Código IBGE",
    "no_regiao_turistica": "Região Turística",
    "categoria": "Categoria",
}


def _download_uf(client: httpx.Client, uf: str) -> bytes:
    """Fetch the simplified-categorization XLSX bytes for one UF."""
    resp = client.get(
        f"{BASE}{ENDPOINT}",
        params={"sgUf": uf, "nuLocalidade": 0},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=90,
    )
    resp.raise_for_status()
    return resp.content


def _ibge(raw: object) -> str:
    """Normalize an IBGE cell ('2900108.0' or 2900108) to a 7-digit string."""
    s = str(raw or "").strip()
    if not s:
        return ""
    try:
        return str(int(float(s)))
    except ValueError:
        return s


def _extract(xlsx_bytes: bytes, uf: str) -> list[dict[str, str]]:
    """Parse one UF's simplified XLSX into output-schema row dicts."""
    wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes), read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))

    # Find the header row (the one containing "Código IBGE").
    header_idx = next(
        (
            i
            for i, r in enumerate(rows)
            if any(str(c).strip() == "Código IBGE" for c in r if c is not None)
        ),
        None,
    )
    if header_idx is None:
        raise ValueError(f"{uf}: header row with 'Código IBGE' not found")

    header = [str(c).strip() if c is not None else "" for c in rows[header_idx]]
    idx = {key: header.index(label) for key, label in _COL.items() if label in header}
    missing = [label for key, label in _COL.items() if label not in header]
    if missing:
        raise ValueError(f"{uf}: missing expected columns {missing}")

    out: list[dict[str, str]] = []
    for r in rows[header_idx + 1:]:
        name = str(r[idx["no_municipio"]] or "").strip()
        if not name:
            continue  # trailing/blank rows
        out.append(
            {
                "co_municipio": _ibge(r[idx["co_municipio"]]),
                "no_municipio": name,
                "sg_uf": str(r[idx["sg_uf"]] or uf).strip().upper(),
                "categoria": str(r[idx["categoria"]] or "").strip(),
                "no_regiao_turistica": str(r[idx["no_regiao_turistica"]] or "").strip(),
            }
        )
    return out


def main() -> None:
    ufs = [a.upper() for a in sys.argv[1:]] or ALL_UFS
    out_path = Path(__file__).parent.parent / "data" / "mtur" / "municipios_mtur_2025.csv"

    all_rows: list[dict[str, str]] = []
    with httpx.Client(follow_redirects=True) as client:
        for uf in ufs:
            try:
                xlsx = _download_uf(client, uf)
                rows = _extract(xlsx, uf)
                all_rows.extend(rows)
                print(f"[{uf}] {len(rows)} municipalities ({len(xlsx)} bytes)")
            except Exception as exc:  # noqa: BLE001 - report and continue
                print(f"[{uf}] ERROR: {exc}")
            time.sleep(0.3)  # politeness toward the gov endpoint

    if not all_rows:
        print("No rows extracted — aborting (CSV not written).")
        sys.exit(1)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_SCHEMA)
        writer.writeheader()
        writer.writerows(all_rows)

    by_uf: dict[str, int] = {}
    for r in all_rows:
        by_uf[r["sg_uf"]] = by_uf.get(r["sg_uf"], 0) + 1
    print(f"\nWrote {len(all_rows)} rows across {len(by_uf)} UFs → {out_path}")
    print("Per-UF:", ", ".join(f"{k}={v}" for k, v in sorted(by_uf.items())))


if __name__ == "__main__":
    main()
