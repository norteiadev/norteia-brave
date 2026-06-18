"""Convert Portaria MTUR 9/2025 XLSX export to the Norteia Mtur CSV schema.

The Mapa do Turismo Brasileiro XLSX is downloaded manually from:
  https://www.mapa.turismo.gov.br/mapa/init.html
Select 'Município Categorizado Detalhado Excel' -> year 2025, all UFs -> Download.

Usage:
  pip install openpyxl  # if not installed
  python scripts/mtur_xlsx_to_csv.py municipios_mtur_2025.xlsx
  # writes: data/mtur/municipios_mtur_2025.csv
  # dry-run (print first 5 rows only):
  python scripts/mtur_xlsx_to_csv.py municipios_mtur_2025.xlsx --dry-run
"""

from __future__ import annotations

import csv
import os
import sys
from pathlib import Path

# Output schema — must match MturClient._load_csv() expected columns
OUTPUT_SCHEMA = ["co_municipio", "no_municipio", "sg_uf", "categoria", "no_regiao_turistica"]

# COLUMN_CANDIDATES maps each output column to known XLSX header variants.
# All matching is case-insensitive + stripped. The first match in the list wins.
# This handles differences between Mtur portal releases (2024 vs 2025 column names).
COLUMN_CANDIDATES: dict[str, list[str]] = {
    "co_municipio": [
        "co_municipio",
        "codigo_ibge",
        "cod_ibge",
        "codibge",
        "co_municipio_ibge",
        "codigo_municipio",
    ],
    "no_municipio": [
        "no_municipio",
        "nome_municipio",
        "municipio",
        "nome",
    ],
    "sg_uf": [
        "sg_uf",
        "uf",
        "estado",
        "sigla_uf",
    ],
    "categoria": [
        "categoria",
        "categorizacao",
        "cat",
    ],
    "no_regiao_turistica": [
        "no_regiao_turistica",
        "regiao_turistica",
        "regiao",
        "nome_regiao_turistica",
    ],
}


def _detect_columns(headers: list[str]) -> dict[str, str | None]:
    """Match XLSX headers against COLUMN_CANDIDATES and return the mapping.

    Args:
        headers: List of raw header strings from the XLSX first row.

    Returns:
        Dict mapping each output column name to the actual XLSX column name that
        matched (case-preserved), or None if no candidate matched.

    Side effect:
        Prints the detected mapping to stdout for operator verification.
    """
    # Build a normalized lookup: lowercased+stripped header -> original header
    normalized_to_original: dict[str, str] = {
        h.strip().lower(): h for h in headers if h is not None
    }

    mapping: dict[str, str | None] = {}
    for output_col, candidates in COLUMN_CANDIDATES.items():
        matched: str | None = None
        for candidate in candidates:
            if candidate.lower() in normalized_to_original:
                matched = normalized_to_original[candidate.lower()]
                break
        mapping[output_col] = matched

    print("\n--- Column detection results ---")
    for output_col, xlsx_col in mapping.items():
        status = f"-> '{xlsx_col}'" if xlsx_col is not None else "NOT FOUND"
        print(f"  {output_col:<28} {status}")
    print("--------------------------------\n")

    return mapping


def convert(xlsx_path: Path, output_path: Path, dry_run: bool = False) -> None:
    """Convert a Mapa do Turismo Brasileiro XLSX to the Norteia Mtur CSV schema.

    Args:
        xlsx_path: Path to the downloaded XLSX file.
        output_path: Destination CSV path (e.g. data/mtur/municipios_mtur_2025.csv).
        dry_run: If True, print the first 5 rows to stdout and do not write any file.

    Raises:
        SystemExit(1): If openpyxl is not installed, required columns are missing,
                       or the XLSX cannot be opened.
    """
    try:
        import openpyxl
    except ImportError:
        print(
            "ERROR: openpyxl is not installed.\n"
            "Install it with: pip install openpyxl\n"
            "Then re-run this script."
        )
        sys.exit(1)

    print(f"Opening XLSX: {xlsx_path}")
    try:
        wb = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
    except Exception as exc:
        print(f"ERROR: Could not open '{xlsx_path}': {exc}")
        sys.exit(1)

    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))

    if not rows:
        print("ERROR: XLSX appears to be empty (no rows found).")
        sys.exit(1)

    # First row is headers — strip whitespace and handle None cells
    raw_headers = rows[0]
    headers: list[str] = [
        str(cell).strip() if cell is not None else "" for cell in raw_headers
    ]

    print(f"Detected {len(headers)} columns in XLSX:")
    for i, h in enumerate(headers):
        print(f"  [{i}] {h!r}")

    mapping = _detect_columns(headers)

    # Validate required columns (no_regiao_turistica is optional)
    required_cols = ["co_municipio", "no_municipio", "sg_uf", "categoria"]
    missing = [col for col in required_cols if mapping.get(col) is None]
    if missing:
        print(
            f"ERROR: Required XLSX columns not found: {missing}\n"
            "Check XLSX columns manually against COLUMN_CANDIDATES in this script.\n"
            f"Available columns: {headers}"
        )
        sys.exit(1)

    # Build column-index lookup from header name
    header_to_index: dict[str, int] = {h: i for i, h in enumerate(headers)}

    def get_cell(row: tuple, col_name: str) -> str:
        """Extract a cell value from a row by output column name."""
        xlsx_col = mapping.get(col_name)
        if xlsx_col is None:
            return ""
        idx = header_to_index.get(xlsx_col)
        if idx is None:
            return ""
        val = row[idx] if idx < len(row) else None
        return str(val).strip() if val is not None else ""

    # Process data rows (skip the header row at index 0)
    data_rows = rows[1:]

    if dry_run:
        print(f"DRY RUN — showing first {min(5, len(data_rows))} data rows:\n")
        print(",".join(OUTPUT_SCHEMA))
        for row in data_rows[:5]:
            values = [get_cell(row, col) for col in OUTPUT_SCHEMA]
            print(",".join(values))
        print(f"\nDRY RUN — no file written. Total data rows in XLSX: {len(data_rows)}")
        return

    # Ensure output directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    row_count = 0
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(OUTPUT_SCHEMA)
        for row in data_rows:
            # Skip completely empty rows (common in Excel exports)
            if all(cell is None or str(cell).strip() == "" for cell in row):
                continue
            values = [get_cell(row, col) for col in OUTPUT_SCHEMA]
            writer.writerow(values)
            row_count += 1

    print(f"Written {row_count} rows to {output_path}")
    print(
        "\nNext step: verify the output with:\n"
        f"  head -5 {output_path}\n"
        "Then run the destino ingest:\n"
        "  .venv/bin/python -m scripts.ingest_destinos BA"
    )


def main() -> None:
    """CLI entrypoint.

    Usage:
        python scripts/mtur_xlsx_to_csv.py <xlsx_path> [output_path] [--dry-run]

    Args (positional):
        xlsx_path   Path to the downloaded Mtur XLSX file (required).
        output_path Optional output CSV path.
                    Defaults to data/mtur/municipios_mtur_2025.csv relative to
                    the repo root (parent of the scripts/ directory).

    Flags:
        --dry-run   Print first 5 rows to stdout; do not write any file.
        --help      Print this usage message and exit.
    """
    args = [a for a in sys.argv[1:] if a != "--dry-run"]
    dry_run = "--dry-run" in sys.argv[1:]

    if not args or "--help" in sys.argv[1:] or "-h" in sys.argv[1:]:
        print(__doc__)
        print("Additional flags:")
        print("  --dry-run   Print first 5 rows without writing a file.")
        print("  --help      Show this message and exit.")
        sys.exit(0)

    xlsx_path = Path(args[0])
    if not xlsx_path.exists():
        print(f"ERROR: XLSX file not found: '{xlsx_path}'")
        print(
            "Download the 2025 dataset from:\n"
            "  https://www.mapa.turismo.gov.br/mapa/init.html\n"
            "  -> Município Categorizado Detalhado Excel -> year 2025, all UFs -> Download"
        )
        sys.exit(1)

    # Default output: data/mtur/municipios_mtur_2025.csv (repo root / data / mtur /)
    if len(args) >= 2:
        output_path = Path(args[1])
    else:
        # Repo root is the parent directory of the scripts/ folder
        repo_root = Path(__file__).parent.parent
        output_path = repo_root / "data" / "mtur" / "municipios_mtur_2025.csv"

    convert(xlsx_path, output_path, dry_run=dry_run)


if __name__ == "__main__":
    main()
