---
phase: 02-destinos-lane
plan: "03"
subsystem: destinos-clients
tags: [destinos, mtur, notebooklm, clients, fakes, schemas, seed-data]
dependency_graph:
  requires:
    - "01-brave-core: brave/clients/base.py (MturClientProtocol, NotebookLMClientProtocol)"
    - "01-brave-core: brave/lanes/__init__.py, brave/lanes/base.py (D-18 package boundary)"
  provides:
    - "brave/lanes/destinos/__init__.py — lane package marker"
    - "brave/lanes/destinos/schemas.py — DesmembramentoResult + DestinoItem (consumed by 02-05, 02-07)"
    - "brave/clients/mtur.py — MturClient (consumed by 02-05 MturSeedIngest)"
    - "brave/clients/notebooklm.py — NotebookLMClient (consumed by 02-07 NotebookLMIngest)"
    - "brave/clients/null_mtur.py — offline stub (consumed by 02-05 offline mode)"
    - "brave/clients/null_notebooklm.py — offline stub (consumed by 02-07 offline mode)"
    - "tests/fakes/fake_mtur.py — FakeMturClient (consumed by all destinos tests)"
    - "tests/fakes/fake_notebooklm.py — FakeNotebookLMClient (consumed by all destinos tests)"
    - "data/mtur/municipios_mtur_2024.csv — bundled Mtur seed (16 rows BA/RJ/SP)"
  affects:
    - "02-05 MturSeedIngest (depends on MturClient + DesmembramentoResult schema)"
    - "02-07 NotebookLMIngest (depends on NotebookLMClient + FakeNotebookLMClient)"
    - "02-08 DesmembramentoAgent (depends on DesmembramentoResult schema + FakeMturClient)"
tech_stack:
  added: []
  patterns:
    - "Structural typing via _check_protocol_compliance() on all client implementations"
    - "CSV seed file under data/ with SHA-256 placeholder for integrity verification"
    - "Graceful degradation: clients return [] or {} rather than raising on absent data"
key_files:
  created:
    - brave/lanes/destinos/__init__.py
    - brave/lanes/destinos/schemas.py
    - brave/clients/mtur.py
    - brave/clients/null_mtur.py
    - brave/clients/notebooklm.py
    - brave/clients/null_notebooklm.py
    - tests/fakes/fake_mtur.py
    - tests/fakes/fake_notebooklm.py
    - data/mtur/municipios_mtur_2024.csv
    - data/mtur/municipios_mtur_2024.csv.sha256
  modified: []
decisions:
  - "D-01 implemented: MturClient reads bundled static CSV (data/mtur/municipios_mtur_YYYY.csv) sorted by filename descending to pick the most recent year"
  - "D-02 implemented: NotebookLMClient returns {} for missing reports (graceful degradation); no pre-filter by Mtur presence — dedup in Rio handles overlap"
  - "_map_categoria handles both old nomenclature (A/B/C/D/E) and new 2025 nomenclature (turísticos/complementar/apoio)"
  - "FileNotFoundError with clear message on absent CSV (T-02-03-03 mitigation)"
metrics:
  duration: "3 minutes"
  completed_date: "2026-06-12"
  tasks_completed: 2
  files_created: 10
---

# Phase 02 Plan 03: Data Contracts, Clients, Fakes, and Seed Data Summary

**One-liner:** DesmembramentoResult + DestinoItem Pydantic v2 schemas, MturClient reading bundled CSV with old/new categoria mapping, NotebookLMClient reading local JSON reports, four protocol-compliant stubs/fakes, and a 16-row BA/RJ/SP seed dataset.

## What Was Built

This plan delivers the **interface-first Wave 1 contracts** for the Destinos lane. All three producer plans (02-05 MturSeedIngest, 02-07 NotebookLMIngest, 02-08 DesmembramentoAgent) consume these seams.

### Task 1: DesmembramentoResult schema + Mtur seed CSV + lane package marker (commit 776c3cf)

- **`brave/lanes/destinos/__init__.py`** — empty package marker maintaining the D-18 boundary (lanes import core, never the reverse)
- **`brave/lanes/destinos/schemas.py`** — `DestinoItem` (nome min_length=2, tipo Literal of 7 values, posicionamento min_length=5) + `DesmembramentoResult` (municipio_ibge pattern=`^\d{7}$`, destinos list defaulting to empty). Both validate correctly with Pydantic v2.
- **`data/mtur/municipios_mtur_2024.csv`** — 16-row synthetic fixture with BA (7), RJ (5), SP (4) municipalities. Includes Porto Seguro (IBGE 2927408). Contains both old categoria names (A/B/C/D/E) and new 2025 nomenclature ("Municípios turísticos", "com oferta turística complementar", "de apoio ao turismo") to exercise both `_map_categoria` code paths.
- **`data/mtur/municipios_mtur_2024.csv.sha256`** — SHA-256 placeholder for integrity verification on real dataset download.

### Task 2: MturClient + NullMturClient + NotebookLMClient + NullNotebookLMClient + fakes (commit 4fabcb8)

- **`brave/clients/mtur.py`** — `MturClient.fetch_municipalities(uf)` globs `data/mtur/municipios_mtur_*.csv` (latest by filename), reads with `csv.DictReader(encoding='utf-8-sig')`, maps raw categoria via `_map_categoria`. Raises `FileNotFoundError` with clear message when no CSV exists (T-02-03-03 mitigation).
- **`brave/clients/null_mtur.py`** — `NullMturClient` returns `[]` always; production offline stub.
- **`brave/clients/notebooklm.py`** — `NotebookLMClient.fetch_report(municipio)` parses "nome:uf:ibge" format to find `data/notebooklm/{uf}/{ibge}.json`; returns `{}` on any `OSError` (graceful degradation, D-02).
- **`brave/clients/null_notebooklm.py`** — `NullNotebookLMClient` returns `{}` always; production offline stub.
- **`tests/fakes/fake_mtur.py`** — `FakeMturClient` with configurable `fixtures` list, UF-filtered return, `calls` recording.
- **`tests/fakes/fake_notebooklm.py`** — `FakeNotebookLMClient` with configurable `reports` dict, `calls` recording.
- All six client files have `_check_protocol_compliance()` structural type guards at module bottom.

## Verification Results

```
BA municipalities: 7
{'ibge_code': '2927408', 'name': 'Porto Seguro', 'categoria': 'Oferta Principal', 'uf': 'BA'}
{'ibge_code': '2919207', 'name': 'Ilhéus', 'categoria': 'Oferta Principal', 'uf': 'BA'}
{'ibge_code': '2910800', 'name': 'Camaçari', 'categoria': 'Complementar', 'uf': 'BA'}
Mtur protocol OK
NotebookLM protocol OK
NullMtur protocol OK
NullNotebookLM protocol OK
FakeMtur protocol OK
FakeNotebookLM protocol OK
```

## Deviations from Plan

None - plan executed exactly as written.

## Known Stubs

| File | Line | Description |
|------|------|-------------|
| `data/mtur/municipios_mtur_2024.csv.sha256` | 1 | SHA-256 placeholder — intentional; the CSV is synthetic seed data. Replace with real SHA-256 after downloading the official Mtur dataset from dados.gov.br. Will be resolved when the real Mtur dataset is integrated. |
| `data/mtur/municipios_mtur_2024.csv` | all | Synthetic fixture seed (16 rows). Satisfies MturClient parser and category mapping for both old/new nomenclature. Intentional for offline testing — replace with real Mtur export for production. |

Both stubs are intentional for this wave. The plan explicitly specifies a synthetic fixture seed CSV. The real dataset integration is deferred (D-01).

## Threat Flags

No new unplanned threat surface introduced. All boundaries match the plan's threat register:
- MturClient: file I/O only, no network, no user input (T-02-03-01 accepted)
- NotebookLMClient: file I/O only, no network (T-02-03-02 accepted)
- FileNotFoundError message is clear without exposing sensitive paths (T-02-03-03 mitigated)

## Self-Check: PASSED

Files verified:
- `brave/lanes/destinos/__init__.py` — FOUND
- `brave/lanes/destinos/schemas.py` — FOUND
- `brave/clients/mtur.py` — FOUND
- `brave/clients/null_mtur.py` — FOUND
- `brave/clients/notebooklm.py` — FOUND
- `brave/clients/null_notebooklm.py` — FOUND
- `tests/fakes/fake_mtur.py` — FOUND
- `tests/fakes/fake_notebooklm.py` — FOUND
- `data/mtur/municipios_mtur_2024.csv` — FOUND
- `data/mtur/municipios_mtur_2024.csv.sha256` — FOUND

Commits verified:
- 776c3cf — FOUND (Task 1: schema + seed CSV + package marker)
- 4fabcb8 — FOUND (Task 2: clients + fakes)
