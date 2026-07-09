"""Convert the IBGE DTB (Divisão Territorial Brasileira) 2025 .xls reports into the
Norteia IBGE CSV seeds, and reconcile the municipio base against the live geo file.

The DTB is the authoritative administrative territory of Brazil (all UFs, municipios,
distritos, subdistritos) published yearly by IBGE. Unlike the kelvins geo dataset used
by ``data/ibge/ibge_municipios.csv``, the DTB carries **no lat/lng** — it is a pure
administrative hierarchy (UF -> Região Intermediária -> Região Imediata -> Município ->
Distrito -> Subdistrito) keyed by official IBGE codes.

Download (manual):
  https://www.ibge.gov.br/geociencias/organizacao-do-territorio/estrutura-territorial/
    23701-divisao-territorial-brasileira.html
  -> "Municípios, Distritos e Subdistritos" -> year 2025 -> the RELATORIO_DTB_*.xls files

This script does three things:

  1. Emits ``data/ibge/ibge_distritos.csv``     (distrito_code, nome, ibge_code, municipio_nome, uf)
  2. Emits ``data/ibge/ibge_subdistritos.csv``  (subdistrito_code, nome, distrito_code, ibge_code, uf)
     — these are NEW seeds (sub-municipio hierarchy) used to enrich attraction localization.
  3. Reconciles the DTB municipio list against ``data/ibge/ibge_municipios.csv`` and prints
     adds / removes / renames. With ``--write-municipios`` it emits a merged candidate file
     ``data/ibge/ibge_municipios_dtb2025.csv`` that keeps existing lat/lng, applies DTB
     canonical names, and leaves lat/lng BLANK for newly-added municipios (which then need
     geocoding before they replace the live file). The live file is never overwritten.

Usage:
  # xlrd reads the old-format .xls (BIFF/OLE2); openpyxl cannot.
  uv run --with xlrd python scripts/dtb_reconcile.py ~/Downloads/DTB_2025
  # or point at individual files / another dir; --dry-run prints without writing.
  uv run --with xlrd python scripts/dtb_reconcile.py ~/Downloads/DTB_2025 --dry-run
  uv run --with xlrd python scripts/dtb_reconcile.py ~/Downloads/DTB_2025 --write-municipios
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Numeric UF code (DTB col "UF") -> 2-letter sigla, to match ibge_municipios.csv `uf`.
UF_SIGLA: dict[str, str] = {
    "11": "RO", "12": "AC", "13": "AM", "14": "RR", "15": "PA", "16": "AP", "17": "TO",
    "21": "MA", "22": "PI", "23": "CE", "24": "RN", "25": "PB", "26": "PE", "27": "AL",
    "28": "SE", "29": "BA", "31": "MG", "32": "ES", "33": "RJ", "35": "SP",
    "41": "PR", "42": "SC", "43": "RS", "50": "MS", "51": "MT", "52": "GO", "53": "DF",
}

# DTB report layout. The three reports share the leading columns; each adds trailing ones.
# Header sits at a fixed offset; data follows. Indices are 0-based into the row tuple.
HEADER_ROW = 6  # row index of the "UF | Nome_UF | ... | Nome_Município" header
COL_UF = 0
COL_MUNICIPIO_COMPLETO = 7   # 7-digit IBGE municipio code
COL_MUNICIPIO_NOME = 8
COL_DISTRITO_COMPLETO = 10   # 9-digit distrito code   (distritos + subdistritos reports)
COL_DISTRITO_NOME = 11
COL_SUBDISTRITO_COMPLETO = 13  # 11-digit subdistrito code (subdistritos report)
COL_SUBDISTRITO_NOME = 14

DISTRITOS_SCHEMA = ["distrito_code", "nome", "ibge_code", "municipio_nome", "uf"]
SUBDISTRITOS_SCHEMA = ["subdistrito_code", "nome", "distrito_code", "ibge_code", "uf"]
MUNICIPIOS_SCHEMA = ["ibge_code", "nome", "uf", "lat", "lng"]


# ---------------------------------------------------------------------------
# .xls loading
# ---------------------------------------------------------------------------


def _open_sheet(path: Path):
    """Open the first sheet of a DTB .xls report. Exits with a hint if xlrd is missing."""
    try:
        import xlrd
    except ImportError:
        print(
            "ERROR: xlrd is not installed (needed to read the old-format .xls).\n"
            "Run this script with:  uv run --with xlrd python scripts/dtb_reconcile.py ...\n"
            "or install it into the venv:  uv pip install xlrd"
        )
        sys.exit(1)
    try:
        wb = xlrd.open_workbook(path)
    except Exception as exc:  # noqa: BLE001 — operator-facing tool, report and stop
        print(f"ERROR: could not open '{path}': {exc}")
        sys.exit(1)
    return wb.sheet_by_index(0)


def _data_rows(sheet) -> list[list]:
    """Return DTB data rows below the header, dropping title/blank/footer rows.

    A valid data row has a 2-digit numeric UF in col 0 AND a 7-digit numeric municipio
    code in col 7. This filters the report title block, the blank spacer rows, and any
    trailing "FONTE:" footnotes without hard-coding a row count.
    """
    rows: list[list] = []
    for r in range(HEADER_ROW + 1, sheet.nrows):
        vals = [sheet.cell_value(r, c) for c in range(sheet.ncols)]
        uf = str(vals[COL_UF]).strip()
        if len(uf) != 2 or not uf.isdigit():
            continue
        cod = _digits(vals[COL_MUNICIPIO_COMPLETO])
        if len(cod) != 7:
            continue
        rows.append(vals)
    return rows


def _digits(cell) -> str:
    """Normalise a code cell to a plain digit string.

    xlrd may hand back a float (e.g. 1100015.0) for numeric cells, or a str. Strip any
    trailing ".0" and surrounding whitespace so codes compare cleanly against the CSV.
    """
    if isinstance(cell, float):
        return str(int(cell))
    s = str(cell).strip()
    if s.endswith(".0") and s[:-2].isdigit():
        return s[:-2]
    return s


def _text(cell) -> str:
    return str(cell).strip()


def _find(dtb_dir: Path, *needles: str, exclude: tuple[str, ...] = ()) -> Path | None:
    """Locate a DTB report .xls whose filename contains ALL ``needles`` and none of
    ``exclude`` (all case-insensitive).

    ``exclude`` is needed because two files contain the substring "SUBDISTRITOS":
    the ``RELATORIO_DTB_BRASIL_2025_SUBDISTRITOS.xls`` full report AND the
    ``DISTRITOS-SUBDISTRITOS_NOVOS_E_EXTINTOS_2025.xls`` delta. We want the report.
    """
    def ok(name: str) -> bool:
        low = name.lower()
        return all(n.lower() in low for n in needles) and not any(x.lower() in low for x in exclude)

    if dtb_dir.is_file():
        return dtb_dir if ok(dtb_dir.name) else None
    for p in sorted(dtb_dir.glob("*.xls")):
        if ok(p.name):
            return p
    return None


# ---------------------------------------------------------------------------
# Extractors
# ---------------------------------------------------------------------------


def extract_municipios(sheet) -> dict[str, tuple[str, str]]:
    """DTB municipios -> {ibge_code: (nome, uf_sigla)}."""
    out: dict[str, tuple[str, str]] = {}
    for row in _data_rows(sheet):
        code = _digits(row[COL_MUNICIPIO_COMPLETO])
        uf = UF_SIGLA.get(str(row[COL_UF]).strip(), "")
        out[code] = (_text(row[COL_MUNICIPIO_NOME]), uf)
    return out


def extract_distritos(sheet) -> list[list[str]]:
    """DTB distritos -> rows of DISTRITOS_SCHEMA (deduped on distrito_code)."""
    seen: set[str] = set()
    out: list[list[str]] = []
    for row in _data_rows(sheet):
        dcode = _digits(row[COL_DISTRITO_COMPLETO])
        if len(dcode) != 9 or dcode in seen:
            continue
        seen.add(dcode)
        out.append([
            dcode,
            _text(row[COL_DISTRITO_NOME]),
            _digits(row[COL_MUNICIPIO_COMPLETO]),
            _text(row[COL_MUNICIPIO_NOME]),
            UF_SIGLA.get(str(row[COL_UF]).strip(), ""),
        ])
    return out


def extract_subdistritos(sheet) -> list[list[str]]:
    """DTB subdistritos -> rows of SUBDISTRITOS_SCHEMA (deduped on subdistrito_code)."""
    seen: set[str] = set()
    out: list[list[str]] = []
    for row in _data_rows(sheet):
        scode = _digits(row[COL_SUBDISTRITO_COMPLETO])
        if len(scode) != 11 or scode in seen:
            continue
        seen.add(scode)
        out.append([
            scode,
            _text(row[COL_SUBDISTRITO_NOME]),
            _digits(row[COL_DISTRITO_COMPLETO]),
            _digits(row[COL_MUNICIPIO_COMPLETO]),
            UF_SIGLA.get(str(row[COL_UF]).strip(), ""),
        ])
    return out


# ---------------------------------------------------------------------------
# Municipio reconcile
# ---------------------------------------------------------------------------


def load_live_municipios(path: Path) -> dict[str, dict[str, str]]:
    """Load the live ibge_municipios.csv -> {ibge_code: row_dict}."""
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        return {row["ibge_code"].strip(): row for row in csv.DictReader(f)}


def reconcile_municipios(
    dtb: dict[str, tuple[str, str]],
    live: dict[str, dict[str, str]],
) -> tuple[list[str], list[str], list[tuple[str, str, str]]]:
    """Diff DTB municipios against the live base.

    Returns (added, removed, renamed) where:
      added   = codes in DTB but not live (new municipios — need geocoding)
      removed = codes in live but not DTB (extinct / wrong code in base)
      renamed = (code, live_nome, dtb_nome) where names differ (accent-insensitive)
    """
    dtb_codes = set(dtb)
    live_codes = set(live)
    added = sorted(dtb_codes - live_codes)
    removed = sorted(live_codes - dtb_codes)
    renamed: list[tuple[str, str, str]] = []
    for code in sorted(dtb_codes & live_codes):
        live_nome = live[code]["nome"].strip()
        dtb_nome = dtb[code][0]
        if _fold(live_nome) != _fold(dtb_nome):
            renamed.append((code, live_nome, dtb_nome))
    return added, removed, renamed


def _fold(s: str) -> str:
    import unicodedata

    return "".join(
        c for c in unicodedata.normalize("NFKD", s.lower().strip())
        if unicodedata.category(c) != "Mn"
    )


def write_merged_municipios(
    dtb: dict[str, tuple[str, str]],
    live: dict[str, dict[str, str]],
    out_path: Path,
) -> int:
    """Write a merged candidate municipio file (DTB names + preserved lat/lng).

    Row set = DTB municipios (authoritative list). lat/lng carried over from the live
    file when the code matches; BLANK for new municipios (operator geocodes before this
    replaces the live file). DTB canonical nome + uf always win. Live-only codes (DTB
    removals) are dropped — surfaced in the reconcile report instead.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(MUNICIPIOS_SCHEMA)
        for code in sorted(dtb):
            nome, uf = dtb[code]
            existing = live.get(code)
            lat = existing["lat"] if existing else ""
            lng = existing["lng"] if existing else ""
            w.writerow([code, nome, uf, lat, lng])
            n += 1
    return n


# ---------------------------------------------------------------------------
# IO helper
# ---------------------------------------------------------------------------


def _write_csv(path: Path, schema: list[str], rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(schema)
        w.writerows(rows)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    args = sys.argv[1:]
    if not args or "--help" in args or "-h" in args:
        print(__doc__)
        sys.exit(0)

    dry_run = "--dry-run" in args
    write_municipios = "--write-municipios" in args
    positional = [a for a in args if not a.startswith("--")]
    if not positional:
        print("ERROR: pass the DTB_2025 directory (or a single .xls file).")
        sys.exit(1)

    dtb_dir = Path(positional[0]).expanduser()
    if not dtb_dir.exists():
        print(f"ERROR: path not found: {dtb_dir}")
        sys.exit(1)

    repo_root = Path(__file__).parent.parent
    ibge_dir = repo_root / "data" / "ibge"
    live_path = ibge_dir / "ibge_municipios.csv"

    # "RELATORIO" scopes to the full reports (excludes the NOVOS_E_EXTINTOS delta).
    # "_DISTRITOS" with exclude=("SUBDISTRITOS",) disambiguates distritos from subdistritos.
    mun_xls = _find(dtb_dir, "RELATORIO", "MUNICIPIOS") or _find(dtb_dir, "MUNICIPIOS")
    dis_xls = _find(dtb_dir, "RELATORIO", "_DISTRITOS", exclude=("SUBDISTRITOS",))
    sub_xls = _find(dtb_dir, "RELATORIO", "SUBDISTRITOS")
    if not (mun_xls and dis_xls and sub_xls):
        print(
            "ERROR: could not find all three DTB reports in "
            f"{dtb_dir}.\n  municipios={mun_xls}\n  distritos={dis_xls}\n  subdistritos={sub_xls}"
        )
        sys.exit(1)

    print(f"MUNICIPIOS  : {mun_xls.name}")
    print(f"DISTRITOS   : {dis_xls.name}")
    print(f"SUBDISTRITOS: {sub_xls.name}\n")

    dtb_mun = extract_municipios(_open_sheet(mun_xls))
    distritos = extract_distritos(_open_sheet(dis_xls))
    subdistritos = extract_subdistritos(_open_sheet(sub_xls))

    print(f"parsed: {len(dtb_mun)} municipios, {len(distritos)} distritos, "
          f"{len(subdistritos)} subdistritos")

    # --- Municipio reconcile ---
    live = load_live_municipios(live_path)
    added, removed, renamed = reconcile_municipios(dtb_mun, live)
    print(f"\n--- Municipio reconcile vs {live_path.name} ({len(live)} live rows) ---")
    print(f"  added (in DTB, not in base, need geocoding): {len(added)}")
    for c in added[:25]:
        print(f"    + {c}  {dtb_mun[c][1]}  {dtb_mun[c][0]}")
    if len(added) > 25:
        print(f"    ... and {len(added) - 25} more")
    print(f"  removed (in base, not in DTB): {len(removed)}")
    for c in removed[:25]:
        print(f"    - {c}  {live[c].get('uf', '')}  {live[c].get('nome', '')}")
    print(f"  renamed (same code, different name): {len(renamed)}")
    for code, ln, dn in renamed[:25]:
        print(f"    ~ {code}  '{ln}' -> '{dn}'")
    if len(renamed) > 25:
        print(f"    ... and {len(renamed) - 25} more")

    if dry_run:
        print("\nDRY RUN — no files written.")
        print("Sample distritos:")
        for row in distritos[:5]:
            print("   ", row)
        print("Sample subdistritos:")
        for row in subdistritos[:5]:
            print("   ", row)
        return

    # --- Write seed CSVs ---
    dis_path = ibge_dir / "ibge_distritos.csv"
    sub_path = ibge_dir / "ibge_subdistritos.csv"
    _write_csv(dis_path, DISTRITOS_SCHEMA, distritos)
    _write_csv(sub_path, SUBDISTRITOS_SCHEMA, subdistritos)
    print(f"\nwrote {len(distritos)} rows -> {dis_path}")
    print(f"wrote {len(subdistritos)} rows -> {sub_path}")

    if write_municipios:
        merged_path = ibge_dir / "ibge_municipios_dtb2025.csv"
        n = write_merged_municipios(dtb_mun, live, merged_path)
        print(f"wrote {n} rows -> {merged_path} (lat/lng BLANK for the {len(added)} new; "
              "geocode before replacing the live file)")
    else:
        print("\n(municipio base left untouched; re-run with --write-municipios to emit "
              "ibge_municipios_dtb2025.csv)")


if __name__ == "__main__":
    main()
