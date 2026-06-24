---
phase: 12-tripadvisor-session-injection-seam-real-browser-bootstrap-ht
plan: "01"
subsystem: tripadvisor-session-bootstrap
tags: [operator-runbook, session-injection, ta-bootstrap, datadome, tripadvisor]

dependency_graph:
  requires: []
  provides:
    - data/tripadvisor/README#OPERATOR_GATE
    - scripts/ta_bootstrap
    - scripts/ta_bootstrap.py
  affects:
    - brave/lanes/tripadvisor/client.py (session injection endpoint used by plans 02–04)

tech_stack:
  added: []
  patterns:
    - stdlib-only helper script (no pip install required to run)
    - DevTools Copy-as-cURL → parse_curl → POST /api/v1/tripadvisor/session
    - Bearer token via env var (BRAVE_DASHBOARD_BEARER_TOKEN) to avoid shell history exposure

key_files:
  created:
    - scripts/ta_bootstrap
    - scripts/ta_bootstrap.py
  modified:
    - data/tripadvisor/README

decisions:
  - "stdlib-only (argparse, json, re, os, datetime, urllib.request) keeps the helper runnable before venv activation"
  - "Single-query fallback: if cURL only contains one preRegisteredQueryId, store under both 'destinations' and 'attractions' keys"
  - "Heuristic for query_id typing: presence of 'ATTRACTION' in variables JSON → attractions key"
  - "Print only cookie_count and query_ids keys; never print cookie values (T-12-01-01 mitigation)"
  - "BRAVE_DASHBOARD_BEARER_TOKEN env var preferred over --bearer arg to keep token off shell history (T-12-01-03)"

metrics:
  duration: "12 minutes"
  completed: "2026-06-24"
  tasks_completed: 2
  tasks_total: 2
  files_created: 2
  files_modified: 1
---

# Phase 12 Plan 01: Operator Acquisition Runbook + ta_bootstrap Helper Summary

**One-liner:** Operator-gate README rewritten with DevTools Copy-as-cURL session-injection runbook and stdlib-only ta_bootstrap.py that parses real browser cURL into a POST /api/v1/tripadvisor/session call.

## What Was Built

### Task 1 — data/tripadvisor/README updated (commit abecbc7)

The stale OPERATOR GATE section (which referenced `playwright install chromium` and `pip install 'norteia-brave[scraper]'`) was replaced with a new runbook structured in three clear phases:

1. **ACQUISITION (human step)** — step-by-step instructions to open Chrome/Firefox, navigate to a Brazilian TripAdvisor destination page, filter the Network tab for `graphql/ids`, and Right-click → Copy as cURL (bash).
2. **INJECTION (script step)** — how to run `scripts/ta_bootstrap.py --curl /tmp/ta_session.curl --endpoint http://localhost:8000`.
3. **SWEEP (engine step)** — set `RUN_REAL_EXTERNALS=1`, optional proxy, POST `/api/v1/engine/start`.

Also added:
- SESSION LIFETIME section: explains `BRAVE_TA_SESSION_TTL` default (1800 s), when to re-run ta_bootstrap, and mid-sweep expiry behaviour.
- geoId VERIFICATION NOTE: ES 303516 redirected to MG 303380 on 2026-06-24; operator must verify geoId matches the UF being swept.
- MITIGATIONS (b): replaced "Playwright-driven session initialisation" with the spike findings (httpx/headless/headed browsers all 403; only genuine human browser passes).

### Task 2 — scripts/ta_bootstrap + scripts/ta_bootstrap.py created (commit 9f2d191)

`scripts/ta_bootstrap` — chmod+x shell wrapper:
```bash
#!/usr/bin/env bash
exec python "$(dirname "$0")/ta_bootstrap.py" "$@"
```

`scripts/ta_bootstrap.py` — stdlib-only Python helper with:

- **`parse_curl(curl_str) -> dict`**: extracts cookies from `Cookie:` header or `-b/--cookie` flag; User-Agent from header or `-A` flag; `preRegisteredQueryId` from `extensions.preRegisteredQueryId` in the batch-array `--data-raw` JSON. Heuristic: `ATTRACTION` in variables JSON → attractions key; first query → destinations. Single-query fallback stores same ID under both keys.
- **`inject_session(payload, endpoint, bearer) -> None`**: POSTs to `{endpoint}/api/v1/tripadvisor/session` via `urllib.request`. 200/201/202 → prints canary result; 422 → prints validation error + body; 4xx/5xx → `SystemExit`.
- **`main()`**: argparse with `--curl FILE`, `--endpoint URL` (default `http://localhost:8000`), `--bearer TOKEN` (env fallback `BRAVE_DASHBOARD_BEARER_TOKEN`). Prints `Parsed: N cookies, query_ids={...}` before injection.

## Verification

```
syntax ok
--help: shows --curl, --endpoint, --bearer
stdlib-only: PASS (no third-party imports)
ta_bootstrap in README: 8 occurrences
playwright install chromium in README: 0 occurrences
geoId ES→MG note: present (303516 / 303380)
```

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None — this plan ships documentation and a helper script; no data-rendering or pipeline stubs.

## Threat Surface Scan

No new network endpoints or trust boundaries introduced beyond what the plan's threat model covers:
- `scripts/ta_bootstrap.py` → `POST /api/v1/tripadvisor/session`: already in T-12-01 threat register.
- T-12-01-01 (cookie values in terminal output): mitigated — script prints only `cookie_count` and `query_ids` keys.
- T-12-01-03 (bearer token in shell history): mitigated — `--bearer` documented as secondary; env var `BRAVE_DASHBOARD_BEARER_TOKEN` is the preferred path.

## Self-Check

```
FOUND: data/tripadvisor/README
FOUND: scripts/ta_bootstrap
FOUND: scripts/ta_bootstrap.py
FOUND: abecbc7 (task 1 commit)
FOUND: 9f2d191 (task 2 commit)
```

## Self-Check: PASSED
