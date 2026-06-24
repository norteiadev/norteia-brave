---
status: partial
phase: 12-tripadvisor-session-injection-seam-real-browser-bootstrap-ht
source: [12-VERIFICATION.md]
started: 2026-06-24T00:00:00Z
updated: 2026-06-24T00:00:00Z
---

## Current Test

[awaiting human testing]

## Tests

### 1. Real cURL parsing (ta_bootstrap end-to-end)
expected: Pasting an actual DevTools "Copy as cURL" string into `scripts/ta_bootstrap.py`
  extracts cookies + `extensions.preRegisteredQueryId` correctly and POSTs to
  `POST /api/v1/tripadvisor/session` without printing cookie values.
result: [pending]

### 2. Live canary validation (three branches)
expected: Inject a real DataDome session captured from a residential-IP browser.
  Confirm 200 `ready` on a valid session; 422 `invalid_session` on an expired
  session (key deleted); 503 `canary_unverified` on an infra fault (key preserved).
result: [pending]

### 3. Sweep operability (one UF, real externals)
expected: With `RUN_REAL_EXTERNALS=1` run one UF; confirm the EngineControl session
  pill transitions (Pronta / Precisa bootstrap / Expirada) and non-zero Nascente
  records are ingested (no silent 0-record retry-storm).
result: [pending]

## Summary

total: 3
passed: 0
issues: 0
pending: 3
skipped: 0
blocked: 0

## Gaps
