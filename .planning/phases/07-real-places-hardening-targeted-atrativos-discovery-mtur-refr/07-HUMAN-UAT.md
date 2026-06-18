---
status: partial
phase: 07-real-places-hardening-targeted-atrativos-discovery-mtur-refr
source: [07-VERIFICATION.md]
started: 2026-06-18T00:00:00Z
updated: 2026-06-18T00:00:00Z
---

## Current Test

[awaiting live operator run — requires BRAVE_PLACES_API_KEY + BRAVE_LLM_OPENROUTER_API_KEY + clean DB]

## Tests

### 1. Load test — 10 destinos × ≥10 atrativos (real data)
expected: With `RUN_REAL_EXTERNALS=true` + Places + OpenRouter keys and a clean DB, `.venv/bin/python -m scripts.loadtest_destinos_atrativos BA` ingests Mtur destinos → promotes 10 to Mar → runs targeted `produce_for_destino` per destino → prints `ACCEPTANCE: PASS` with 10 mar destination records and ≥10 attraction rio records per `parent_mar_id`. Records visible in the dashboard.
result: [pending]

### 2. Mtur refresh (operator-manual)
expected: Operator downloads the Portaria MTUR 9/2025 XLSX from mapa.turismo.gov.br, runs `scripts/mtur_xlsx_to_csv.py` → `data/mtur/municipios_mtur_2025.csv`; the loader picks it up (newest-by-filename) for fresh categorization.
result: [pending]

## Summary

total: 2
passed: 0
issues: 0
pending: 2
skipped: 0
blocked: 0

## Gaps
