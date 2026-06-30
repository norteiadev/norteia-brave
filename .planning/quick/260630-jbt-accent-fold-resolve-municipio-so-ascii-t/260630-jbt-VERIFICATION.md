---
phase: quick-260630-jbt
verified: 2026-06-30
status: passed
score: 2/2 must-haves verified + live-confirmed
---

# Quick 260630-jbt — resolve_municipio accent-fold — Verification

**Goal:** ASCII TripAdvisor city names (e.g. "Maringa") match accented IBGE municípios
("Maringá") in `resolve_municipio`.

## Offline (deterministic)

- `brave/lanes/tripadvisor/ibge.py`: `_fold_accents` (unicodedata NFKD + drop "Mn", stdlib —
  no new dep). `resolve_municipio` Step 2 pre-folds query + choices, returns the original
  accented `uf_records[index]`. Threshold 88 / token_sort_ratio / haversine / signature
  unchanged. False "accent-agnostic" comment removed.
- `tests/unit/lanes/tripadvisor/test_ibge.py`: separate `PR_ROWS_CSV` + `_make_pr_records()`
  (MINIMAL_CSV + its `len==5` assertion untouched). 4 new tests: Maringa→Maringá,
  Carambei→Carambeí, Curitiba exact, ZZZFantasia→None.
- **Full TA offline suite: 218 passed, 0 failed** (214 prior + 4 new; RUN_REAL_EXTERNALS unset).

## Live end-to-end (the reason for this task) — Paraná, same 60 real attractions

Real TA session, full shipped path (`fetch_attractions_paginated` → `fetch_attraction_geo`
→ `state_name_to_uf` → `resolve_municipio`):

| Metric | Before (b3b3758) | After (this fix) |
|--------|------------------|------------------|
| geo resolved (cityName) | 60/60 | 60/60 |
| UF derived | 60/60 | 60/60 |
| **IBGE matched** | **54/60 (90%)** | **59/60 (98.3%)** |

Recovered: 4× **Maringá** + 1× **Carambeí** (pure accent misses, now exact after fold).
Remaining 1 unmatched = "Caioba" (Caiobá) — a **district of Matinhos, NOT an IBGE município**
→ legitimately unmatched (out of scope; would need coords→haversine→Matinhos).

**100% of real municípios in the dense sample now resolve.** Verdict: PASSED.
