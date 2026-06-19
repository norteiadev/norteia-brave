---
phase: 08-ops-cms-destinos-atrativos-crud-process-observability-cores
audited: 2026-06-19
asvs_level: L2
status: secured
threats_total: 22
threats_closed: 22
threats_open: 0
result: SECURED
---

# SECURITY.md — Phase 08: Ops CMS (Destinos/Atrativos CRUD) + Process Observability

**Audited:** 2026-06-19
**ASVS Level:** L2 (default)
**Block-on:** default (open threats block)
**Result:** SECURED — 22/22 threats resolved (16 mitigate CLOSED, 6 accept documented)

Implementation files were treated as READ-ONLY. No implementation was modified.
This audit verifies declared mitigations exist in code — documentation/intent was
not accepted as evidence; each `mitigate` threat was confirmed at file:line.

---

## Threat Verification

| Threat ID | Category | Disposition | Status | Evidence |
|-----------|----------|-------------|--------|----------|
| T-08-01 | Spoofing | mitigate | CLOSED | `require_bearer` on all reads: cms.py:112 (`/destinos`), :170 (`/destinos/{id}`), :460 (`/atrativos`), :520 (`/atrativos/{id}`). Tests: test_cms_endpoints.py:159-168, 341-350. Fail-closed hmac.compare_digest in deps.py:52-82. |
| T-08-02 | Tampering | mitigate | CLOSED | `require_steward_or_bearer` on every mutation: cms.py:254 (promote), :310 (descarte destino), :361 (reprocess), :416 (edit destino), :587 (advance), :629 (descarte atrativo), :665 (edit atrativo). deps.py:85-121 (constant-time, fail-closed both paths). |
| T-08-03 | Tampering | mitigate | CLOSED | advance_atrativo_state calls advance_sub_state(expected_state=…, lock=True); 409 raised on mismatch: cms.py:606-620. Test test_cms_endpoints.py:497-511 asserts 409 on state mismatch. |
| T-08-04 | Information Disclosure (CR-01 FIX) | mitigate | CLOSED | **CR-01 fix verified, not just phone.** Allow-listed `_safe_contacts` (cms.py:58-82) returns ONLY `website` + `phone_masked`; `email`/`ig_handle` dropped by deny-by-default. Applied at list (cms.py:512 `contacts_summary`) and detail via `_safe_normalized` (cms.py:85-104, used at :570). Fix commit a369ea0. Test test_cms_endpoints.py:451-494 asserts owner email AND ig_handle absent in both list and detail. |
| T-08-05 | Tampering | mitigate | CLOSED | edit_atrativo filters phone_e164 before merge: `sanitized_fields = {k:v ... if k != "phone_e164"}` cms.py:688, then flag_modified cms.py:694. |
| T-08-06 | Spoofing | mitigate | CLOSED | `require_bearer` on workers.py:33 (`/workers`) and :88 (`/failures`). Tests test_workers_endpoints.py:90-99 assert 401. |
| T-08-07 | DoS (self) | mitigate | CLOSED | inspect(timeout=1.0) workers.py:47; entire block wrapped in try/except workers.py:46-52; None→{} coercion → broker_reachable=False, 200 not 500. Tests test_workers_endpoints.py:107-137 (broker down) + 140-181 (redis llen fail). |
| T-08-08 | Information Disclosure | mitigate | CLOSED | PoisonQuarantine.payload NOT serialized in /failures items (workers.py:131-139 emits only id/task_name/error_message[:500]/quarantined_at). Tests test_workers_endpoints.py:285-335 assert `payload` key never present. |
| T-08-09 | EoP (SSRF) | accept | CLOSED | See Accepted Risks log. Broker URL sourced from env (BRAVE_DB_REDIS_URL deps.py:202 / celery_app), never user-supplied; inspect targets the configured broker only. |
| T-08-10 | Information Disclosure | mitigate | CLOSED | JourneyStepper reads `row.after_state?.sub_state` as a string only (JourneyStepper.tsx:158); audit rows render fmtTs(created_at) + actor (JourneyStepper.tsx:293-298) — no phone/contact fields, no raw JSON dump of after_state. |
| T-08-11 | Tampering | mitigate | CLOSED | `@theme inline` block (globals.css:11-47) intact with all --color-* + status bindings. Token-swap commit 26ffa9b changed only :root/.dark color *values*, not the @theme mapping (verified via diff). |
| T-08-12 | Tampering | mitigate | CLOSED | DestinoActions mutations go through BFF apiFetch (operator Bearer attached) destinos-api.ts:92-108; backend gate is require_steward_or_bearer (cms.py:254/310/361). |
| T-08-13 | Information Disclosure | accept | CLOSED | See Accepted Risks log. Destinos normalized carries geographic/name data only — no phone_e164/PII (08-04-SUMMARY Threat Flags). |
| T-08-14 | Information Disclosure | mitigate | CLOSED | Backend `_safe_normalized`/`_safe_contacts` enforce masking (cms.py:58-104); FE type AtrativoListItem.contacts_summary declares phone_masked only (atrativos-api.ts:33-37, :50). |
| T-08-15 | Tampering | mitigate | CLOSED | Backend expected_state 409 + advance_sub_state(lock=True) FSM revalidate (cms.py:606-620); FE passes expected_state from detail.sub_state (08-05-SUMMARY). |
| T-08-16 | Information Disclosure | accept | CLOSED | See Accepted Risks log. WorkerBoard hostname/task metadata is internal ops data behind require_bearer (workers.py:33). |
| T-08-17 | DoS | accept | CLOSED | See Accepted Risks log. Poll interval with refetchOnWindowFocus disabled; backend <2s (inspect timeout=1.0 workers.py:47). |
| T-08-18 | EoP | accept | CLOSED | See Accepted Risks log. /processo + /workers + /failures are read-only; no worker-control API exists in workers.py. |
| T-08-19 | Information Disclosure (test) | mitigate | CLOSED | Assertion present: test_workers_endpoints.py:315-335 (`payload` not in items). |
| T-08-20 | Information Disclosure (test) | mitigate | CLOSED | Assertion present: test_cms_endpoints.py:397-447 (phone_e164 absent, phone_masked present, raw E.164 absent). |
| T-08-21 | Spoofing (test) | mitigate | CLOSED | Fail-closed 401 tests on all new endpoints: cms reads/mutations test_cms_endpoints.py:159-180, 341-365; workers test_workers_endpoints.py:90-99. |
| T-08-SC | Tampering (supply chain) | accept | CLOSED | See Accepted Risks log. No pyproject.toml / package.json changes in phase 08 (git log: last dep change was phase 07/04). |

---

## Accepted Risks Log

| Threat ID | Risk | Justification | Verified |
|-----------|------|---------------|----------|
| T-08-09 | SSRF via celery inspect broker URL | Broker URL is read from server-side config/env (BRAVE_DB_REDIS_URL, deps.py:202) and the celery app broker, never from request input. No user-controlled value reaches inspect(). | Code path confirmed: get_workers takes no URL param; inspect targets configured broker only (workers.py:44-52). |
| T-08-13 | Destino normalized payload disclosure | Destinos are geographic/territorial entities (name, UF, IBGE, coordinates). No phone_e164 or owner PII is stored in destino normalized — PII only exists on attraction contacts. | Confirmed: destino detail returns rio.normalized raw (cms.py:236) with no contacts/PII; 08-04-SUMMARY Threat Flags. |
| T-08-16 | WorkerBoard hostname/task metadata exposure | Hostnames and queue/task counts are internal ops observability data, exposed only to authenticated operators behind require_bearer. | require_bearer on /workers (workers.py:33). |
| T-08-17 | WorkerBoard polling load | Read-only poll; refetchOnWindowFocus disabled; backend response bounded by inspect timeout=1.0 + Redis socket_connect_timeout=1 (deps.py:203). Self-DoS bounded. | workers.py:47, deps.py:203. |
| T-08-18 | /processo implies worker control | Observability surface is strictly read-only (GET /workers, GET /failures). No start/stop/kill worker endpoints exist. | workers.py contains only two GET handlers; no control verbs. |
| T-08-SC | Supply-chain (new npm/pip packages) | No dependency manifests changed during phase 08. | git log -- pyproject.toml dashboard/package.json shows no phase-08 commits. |

---

## Unregistered Flags

None. All `## Threat Flags` sections across 08-01..08-07 SUMMARY.md either declare
"None — no new trust boundaries" or map explicitly to existing threat IDs
(T-08-10/11 in 08-03, T-08-13 in 08-04, T-08-14/15 in 08-05). No new attack
surface appeared during implementation without a threat mapping.

---

## Notes on Code Review CR-01 (cross-check)

The phase 08 code review (08-REVIEW.md) raised one BLOCKER (CR-01): `_safe_normalized`
originally masked only `phone_e164` and shipped the rest of the contacts dict
(including owner `email` and `ig_handle`) to the dashboard. This audit confirms the
FIX (commit a369ea0) — not merely the phone mask:

- `_safe_contacts` (cms.py:58-82) is now an explicit allow-list: only `website`
  (passthrough) and `phone_masked` (from mask_phone) are emitted; `email` and
  `ig_handle` are dropped by deny-by-default.
- Applied to BOTH entry points: list `contacts_summary` (cms.py:512) and detail
  `normalized` via `_safe_normalized` (cms.py:570).
- Regression test test_atrativo_owner_email_never_leaked (test_cms_endpoints.py:451-494)
  asserts owner email and ig_handle are absent in list AND detail, and that the
  non-PII website is still surfaced.

T-08-04 is therefore CLOSED with the CR-01 leak verified resolved at all atrativo
response paths.

The remaining CR-01..07 review findings (WR-01..07 race/observability-accuracy
issues, IN-01..04 UI/lint nits) are correctness/quality issues, not declared phase-08
threats, and are tracked by the code review — out of scope for this threat-disposition
audit. Note the WR fixes (WR-01 commit-before-dispatch cms.py:288-302, WR-02/03
total/by_task cms→workers.py:113-125, WR-04 live beat_schedule workers.py:75-84,
WR-05 409 on descarte of promoted Mar cms.py:330-339, WR-07 narrowed reprocess
fallback cms.py:386-409) are already present in the audited code.
