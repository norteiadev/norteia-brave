---
phase: 07-real-places-hardening-targeted-atrativos-discovery-mtur-refr
plan: "04"
subsystem: data
tags: [mtur, xlsx, csv, openpyxl, data-pipeline, operator-tooling]

# Dependency graph
requires: []
provides:
  - scripts/mtur_xlsx_to_csv.py: openpyxl XLSX-to-CSV converter with COLUMN_CANDIDATES multi-variant detection
  - data/mtur/README: Portaria MTUR 9/2025 source documentation and 5-step download instructions
affects:
  - MturClient._load_csv (auto-picks municipios_mtur_2025.csv when operator runs converter)
  - loadtest_destinos_atrativos.py harness (07-05, needs full Mtur dataset for all-Brazil destinos)

# Tech tracking
tech-stack:
  added: []  # openpyxl is operator-install only, not added to production dependencies
  patterns:
    - "COLUMN_CANDIDATES multi-variant header detection for resilient XLSX parsing across portal releases"
    - "Newest-by-filename glob loader: place new file, loader auto-picks it (no code change)"

key-files:
  created:
    - scripts/mtur_xlsx_to_csv.py
    - data/mtur/README
  modified: []

key-decisions:
  - "openpyxl listed as operator-only install in script header (not added to pyproject.toml production deps per threat T-07-SC)"
  - "COLUMN_CANDIDATES covers 6-variant fallback per column for resilience to 2025 portal XLSX header renames"
  - "Default output path resolves to data/mtur/municipios_mtur_2025.csv relative to repo root so operator just runs the script with no extra args"
  - "Dry-run mode prints first 5 rows without writing, plus full column detection output, so operator can verify mapping before committing 5000+ rows"

patterns-established:
  - "Operator scripts: argparse-free (sys.argv + --flag check), main() entrypoint, sys.exit(1) on errors, print-based operator feedback"

requirements-completed:
  - PLACE-05

# Metrics
duration: 3min
completed: 2026-06-18
---

# Phase 07 Plan 04: Mtur XLSX-to-CSV Converter and Dataset README Summary

**openpyxl XLSX converter (scripts/mtur_xlsx_to_csv.py) with COLUMN_CANDIDATES multi-variant header detection and --dry-run mode, plus data/mtur/README documenting Portaria MTUR 9/2025 download steps**

## Performance

- **Duration:** 3 min
- **Started:** 2026-06-18T16:25:22Z
- **Completed:** 2026-06-18T16:28:37Z
- **Tasks:** 1/1
- **Files modified:** 2

## Accomplishments

- Delivered scripts/mtur_xlsx_to_csv.py: converts Portaria MTUR 9/2025 XLSX to data/mtur/municipios_mtur_2025.csv with the exact schema MturClient._load_csv() expects
- COLUMN_CANDIDATES map covers 5-6 header variants per output column so minor portal renames between releases do not break conversion
- Delivered data/mtur/README: documents official source (Portaria MTUR no 9/2025, mapa.turismo.gov.br), 5-step manual download instructions, CSV schema, loader behavior (newest-by-filename), and 2025 nomenclature mapping
- 2024 sample preserved as CI fallback; offline suite stays green (398 passed, 1 skipped)

## Task Commits

1. **Task 1: Create scripts/mtur_xlsx_to_csv.py and data/mtur/README** - `36af070` (feat)

**Plan metadata:** (see final commit below)

## Files Created/Modified

- `/Users/leandro/Projects/norteia/norteia-brave/scripts/mtur_xlsx_to_csv.py` - XLSX-to-CSV converter with COLUMN_CANDIDATES detection, dry-run mode, openpyxl import guard, and operator-friendly output
- `/Users/leandro/Projects/norteia/norteia-brave/data/mtur/README` - Dataset source docs, 5-step download, schema, loader behavior, 2025 category nomenclature

## Decisions Made

- openpyxl is not added to pyproject.toml; the script header instructs the operator to `pip install openpyxl` — this is a one-off ops tool, not production code (threat T-07-SC)
- Default output path is computed from `Path(__file__).parent.parent / "data" / "mtur" / "municipios_mtur_2025.csv"` so running the script with just the XLSX path writes to the correct location without extra arguments
- --dry-run flag prints first 5 rows plus the column detection report, enabling operator verification before writing 5000+ rows

## Deviations from Plan

None — plan executed exactly as written.

## Issues Encountered

None.

## Threat Surface Scan

No new network endpoints, auth paths, file access patterns, or schema changes introduced. The script reads a local operator-supplied XLSX and writes a local CSV. openpyxl uses `data_only=True` which strips formulas (T-07-10 mitigation).

## Known Stubs

None. The script is fully functional; it requires only the operator to supply the XLSX file.

## User Setup Required

To use the 2025 dataset:

1. Download the XLSX from https://www.mapa.turismo.gov.br/mapa/init.html (see data/mtur/README for steps)
2. `pip install openpyxl`
3. `python scripts/mtur_xlsx_to_csv.py <downloaded.xlsx>`

The 2024 sample (`data/mtur/municipios_mtur_2024.csv`) continues to serve as the offline CI fallback — no setup needed for tests.

## Next Phase Readiness

- scripts/mtur_xlsx_to_csv.py ready for operator use once 2025 XLSX is obtained
- data/mtur/README instructs the operator on exactly what to download and how to convert it
- MturClient._load_csv() will auto-pick municipios_mtur_2025.csv once present (no code change needed)
- 07-05 (loadtest harness) can proceed in parallel; it works with the 2024 fallback for BA

---
*Phase: 07-real-places-hardening-targeted-atrativos-discovery-mtur-refr*
*Completed: 2026-06-18*
