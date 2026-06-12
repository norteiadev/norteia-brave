---
phase: 02
slug: destinos-lane
status: verified
threats_open: 0
asvs_level: 1
created: 2026-06-12
---

# Phase 02 — Destinos Lane — Security

> Per-phase security contract: threat register, accepted risks, and audit trail.
> Audited against the 9 plan-time `<threat_model>` blocks (34 STRIDE rows) plus the
> 5 critical code-review findings (`02-REVIEW.md`). ASVS L1, block_on=high.

---

## Trust Boundaries

| Boundary | Description | Data Crossing |
|----------|-------------|---------------|
| Steward → DLQ mutating endpoints | `PATCH /dlq/{id}/reprocess\|validate\|descarte`, `POST /dlq/validate-batch` set validação humana=100, re-score into Mar, and dispatch the push to norteia-api | Steward intent; promotes records to canonical Mar |
| Brave → norteia-api (`push_destination`) | Outbound idempotent push of canonical destinos over httpx/TLS, Bearer service token | Public territorial data (no PII); token from env, never logged |
| DesmembramentoAgent → DeepSeek (OpenRouter) | LLM extraction of destinos from município names | Public geographic names; `provider.data_collection="deny"` |
| Lane producers → Nascente | Mtur seed CSV / NotebookLM JSON / LLM output → raw store | Public open data; LLM output gated by 2nd-layer Pydantic validator → quarantine |
| Webhook → Brave (Phase 1) | Error-report reopen (X-Webhook-Secret, hmac) | source_ref string |

---

## Threat Register

34 plan-time threats verified + 3 code-review-sourced findings adjudicated. All `*-SC`
supply-chain rows are CLOSED (no new packages added in any plan). Full STRIDE rows live in
the per-plan `<threat_model>` blocks; this table records dispositions and final status.

| Threat ID | Category | Component | Disposition | Mitigation | Status |
|-----------|----------|-----------|-------------|------------|--------|
| T-02-06-01 | Elevation of Privilege | DLQ mutating endpoints had no authz | mitigate | **FIXED** — `require_steward` (X-Steward-Secret, hmac.compare_digest, fail-closed) on all 4 mutating routes; `test_validate_requires_steward_secret` proves 401 + no mutation. Supersedes the plan-time `accept`. | closed |
| CR-02 | Information Disclosure | `LLMConfig` alias shadowed prefixed API-key env var | mitigate | **FIXED** — removed `alias=` from `openrouter_api_key` / `anthropic_api_key`; keys resolve only from `BRAVE_LLM_*`. Live-reproduced before/after. | closed |
| CR-03 | Repudiation / Integrity | bare `except Exception` in DLQ dispatch → silent sync Mar promotion outside Celery on broker error | mitigate (deferred) | Open warning (medium, < block_on=high). Narrow the except to broker-specific errors so real failures surface instead of silently sync-promoting. Tracked for Phase 3. | open (non-blocking) |
| CR-05 | Tampering | Desmembramento slug leaves accents/apostrophes in `canonical_key` | mitigate (deferred) | Non-security idempotency/URL-safety nit. Normalize (strip accents, drop apostrophes) when slugging. Tracked for Phase 3. | open (non-blocking) |
| T-02-06-02/03 | Tampering / DoS | validate-batch wildcard / unbounded scan | mitigate | `uf=Query(...)` required, `limit` capped 1–1000 at FastAPI layer | closed |
| T-02-06-04 | Tampering | flag_modified omission loses JSON mutation | mitigate | `flag_modified` + dict reassign; DB round-trip test asserts persistence | closed |
| T-02-06-05 | Repudiation | steward action without audit | mitigate | `write_audit(actor="steward")` mandatory in both endpoints | closed |
| T-02-08-01 | Tampering | LLM prompt injection via município names | mitigate | Trusted bundled Mtur data + `DesmembramentoResult` Pydantic 2nd-layer validator; failure → `quarantine_poison`, never Nascente | closed |
| T-02-08-02 | Information Disclosure | DeepSeek sees município names | accept | Public geographic data; `provider_data_collection="deny"` asserted in tests | closed |
| T-02-08-03 | Elevation of Privilege | origem=40 firewall bypass | mitigate | Pure scoring consequence (max 67 < 85 unaided); proven by `test_producer_score_boundaries` | closed |
| T-02-08-04 | DoS | unbounded LLM fan-out | mitigate | Bounded by Oferta Principal count/UF; `usd_daily_budget` cost guard (real client) | closed |
| T-02-04-01 | Information Disclosure | push payload to norteia-api | mitigate | Public data only; Bearer token from env, never logged; httpx TLS | closed |
| T-02-04-02 | DoS | push retry loop | mitigate | `max_retries=3` with backoff | closed |
| T-02-01/03/05/07/09-* | Tampering / Info Disclosure / DoS | Pact JSON, seed CSV/JSON, producer payloads, corroboração boost, test DB writes | mitigate/accept | Public no-PII data; deterministic scoring; test-session isolation. See accepted-risks log. | closed |
| T-02-*-SC | Tampering (supply chain) | npm/pip/cargo installs | mitigate | No new packages in any Phase 2 plan | closed |

*Status: open · closed* · *Disposition: mitigate · accept · transfer*

---

## Accepted Risks Log

| Risk ID | Threat Ref | Rationale | Accepted By | Date |
|---------|------------|-----------|-------------|------|
| AR-02-01 | T-02-01-02, T-02-04-01 | IBGE codes + territorial fields in push payloads are public Brazilian open data — no PII, no secrets. | Leandro Freire | 2026-06-12 |
| AR-02-02 | T-02-03-01, T-02-05-01 | Mtur seed CSV is bundled public data; SHA-256 integrity check deferred to the real-dataset download path (documented stub `*.csv.sha256`). | Leandro Freire | 2026-06-12 |
| AR-02-03 | T-02-08-02 | Data sent to DeepSeek is public geographic names; `provider.data_collection="deny"` enforced + asserted. | Leandro Freire | 2026-06-12 |
| AR-02-04 | T-02-09-01/02 | Integration tests write directly to an isolated test DB session; production pipeline unaffected. | Leandro Freire | 2026-06-12 |
| AR-02-05 | CR-03, CR-05 | Two non-blocking code-quality findings (broker-error masking; slug accents). Below block_on=high; scheduled for Phase 3 hardening, not accepted permanently. | Leandro Freire | 2026-06-12 |

---

## Security Audit Trail

| Audit Date | Threats Total | Closed | Open | Run By |
|------------|---------------|--------|------|--------|
| 2026-06-12 | 37 | 35 | 2 (non-blocking warnings) | gsd-security-auditor + remediation |

Two BLOCKERs found and **fixed** during this audit: T-02-06-01 (steward auth added) and
CR-02 (API-key env shadowing removed). Two non-blocking warnings (CR-03, CR-05) remain
open and are tracked for Phase 3. `threats_open: 0` for block_on=high.

---

## Sign-Off

- [x] All threats have a disposition (mitigate / accept / transfer)
- [x] Accepted risks documented in Accepted Risks Log
- [x] `threats_open: 0` confirmed (no open threat at or above block_on=high)
- [x] `status: verified` set in frontmatter

**Approval:** verified 2026-06-12
