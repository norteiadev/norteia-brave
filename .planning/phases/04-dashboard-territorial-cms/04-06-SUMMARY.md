---
phase: 04-dashboard-territorial-cms
plan: 06
subsystem: dashboard-frontend
tags: [dash-03, whatsapp-gate, master-detail, tanstack-query, ramp-context, quality-rating, lgpd, masked-phone, msw, vitest, d-04, d-06]

# Dependency graph
requires:
  - phase: 04-dashboard-territorial-cms
    plan: 02
    provides: "providers (TanStack Query), lib/api-client (BFF apiFetch + ApiError), MSW+Vitest harness, shadcn primitives"
  - phase: 04-dashboard-territorial-cms
    plan: 04
    provides: "DLQ master-detail scaffold (QueueList/ReviewPanel pattern, ScoreBreakdownPanel, StatusBadge) reused by the gate"
  - phase: 03-atrativos-lane-whatsapp-compliance
    plan: 03
    provides: "atrativos_gate.py endpoints: GET /atrativos/gate, PATCH approve/reject; quality-rating flag + ramp send-path"
provides:
  - "/gate master-detail WhatsApp gate surface (DASH-03)"
  - "GateQueue — gate master list over GET /atrativos/gate (reuses the DLQ D-06 scaffold pattern)"
  - "GateReviewPanel — gate detail pane composing ScoreBreakdownPanel/StatusBadge + RampContext, masked-phone only"
  - "RampContext — volume-ramp cap + WhatsApp quality-rating (GREEN/AMBER/RED destructive) context panel"
  - "gate-actions — useApproveGate/useRejectGate over the existing atrativos_gate endpoints, invalidateQueries(['gate'])"
  - "lib/gate-api — gate query keys + typed BFF fetchers + maskedPhoneFrom helper; mocks/handlers/gate — full-view-state MSW handlers"
affects:
  - "the app nav shell (later) links to /gate"

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Shared ['gate'] query-key prefix → one invalidateQueries(['gate']) refetches queue AND ramp context"
    - "Gate GET returns the FULL row (normalized/score) → no secondary detail fetch; the selected item drives GateReviewPanel"
    - "LGPD masked-phone: maskedPhoneFrom reads ONLY pre-masked fields (phone_masked/telefone_minimizado), never phone_e164; GateReviewPanel redacts raw-phone keys from the normalized JSON dump (belt-and-suspenders)"
    - "RED quality-rating → destructive border/badge + auto-pause copy (UI-SPEC)"

key-files:
  created:
    - dashboard/lib/gate-api.ts
    - dashboard/components/gate/GateQueue.tsx
    - dashboard/components/gate/GateReviewPanel.tsx
    - dashboard/components/gate/RampContext.tsx
    - dashboard/components/gate/gate-actions.ts
    - dashboard/mocks/handlers/gate.ts
    - dashboard/app/gate/page.tsx
    - dashboard/components/gate/__tests__/GateQueue.test.tsx
    - dashboard/components/gate/__tests__/gate-actions.test.tsx
    - dashboard/components/gate/__tests__/test-utils.tsx
  modified:
    - .planning/STATE.md
    - .planning/ROADMAP.md
    - .planning/REQUIREMENTS.md

decisions:
  - "D-06 reuse: the gate composes the DLQ scaffold's PIECES (ScoreBreakdownPanel, StatusBadge, the TanStack-Table master + injected-action-bar detail shape) rather than importing the fetch-coupled DLQ ReviewPanel — because the gate GET returns the full row and there is NO separate /atrativos/gate/{id} detail endpoint; reusing ReviewPanel verbatim would have fired the wrong (DLQ) detail fetch"
  - "RampContext fetches a GET /atrativos/whatsapp/ramp-context view under the ['gate'] key; advisory-only (a context fetch failure renders a soft fallback, never blocks the queue)"
  - "Masked-phone defense in depth: a dedicated maskedPhoneFrom (reads only pre-masked keys) PLUS a redactPhone pass over the normalized JSON dump, so even an adversarial phone_e164 in the payload cannot reach the DOM"

requirements-completed: [DASH-03]

# Metrics
duration: ~15min
completed: 2026-06-16
---

# Phase 4 Plan 06: WhatsApp Gate UI (DASH-03) Summary

**The `/gate` master-detail WhatsApp gate surface — a `GateQueue` (BA/RJ/SP/SC/CE/PE-ordered, scoped to `aguardando_consulta_whatsapp`) beside a `GateReviewPanel` that reuses the DLQ scaffold's `ScoreBreakdownPanel`/`StatusBadge` and rides a `RampContext` panel showing the volume-ramp cap + WhatsApp quality-rating (GREEN/AMBER/RED destructive) — with approve ("Aprovar contato" → outreach enqueued) and reject (behind the "Rejeitar atrativo?" destructive AlertDialog → DLQ) over the EXISTING atrativos_gate.py endpoints, invalidate-on-mutate, LGPD-masked phone (never raw e164), proven offline with 14 gate Vitest+MSW tests (53 total).**

## Performance

- **Duration:** ~15 min
- **Tasks:** 2 (both `type=auto`)
- **Files created:** 10 (gate slice + tests + local test-utils) · no new deps (T-04-SC honored)

## Accomplishments

### Task 1 — GateQueue + GateReviewPanel + RampContext (commit `9f87e76`)
- **`lib/gate-api.ts`** — `gateKeys` (shared `['gate']` prefix), typed BFF fetchers for the gate queue / ramp context / approve / reject, and `maskedPhoneFrom` (reads ONLY pre-masked fields; deliberately never models a raw-phone field). `GATE_SUB_STATE` + `UF_PRIORITY` constants.
- **`GateQueue`** — reuses the DLQ master-detail scaffold shape (TanStack Table v8 + shadcn `table`, BA/RJ/SP/SC/CE/PE UF filter, 36px dense rows, mono canonical_key/score, `StatusBadge` per row, row-click → detail) bound to `GET /api/v1/atrativos/gate?uf&limit`. Empty state is the UI-SPEC copy **"Fila de gate vazia"**; loading/error/401 states.
- **`GateReviewPanel`** — the detail pane: composes the reused `ScoreBreakdownPanel` (§7.6) + `StatusBadge`, renders the Rio normalized payload, an injected action bar, and the **"telefone (minimizado)"** masked-phone field. The selected gate row drives it directly (no second fetch — the gate GET already returns the full row).
- **`RampContext`** — the WhatsApp send-path context panel: remaining volume-ramp cap (restante/usado/cap) + the quality-rating badge; **RED gets the destructive border/badge + the auto-pause copy** (UI-SPEC).
- **`mocks/handlers/gate.ts`** — per-view-state MSW handlers (queue success/empty/error/401, ramp success/RED/error, approve/reject). Sample rows carry ONLY `phone_masked` — no raw e164 anywhere.
- **Tests:** GateQueue UF order, empty "Fila de gate vazia", error+retry, 401, onSelect; masked-phone label + the adversarial "no raw E.164 in the DOM" assertion.

### Task 2 — gate-actions + /gate page (commit `fd40d63`)
- **`gate-actions.ts`** — `useApproveGate` / `useRejectGate` TanStack `useMutation` over the **existing** atrativos_gate endpoints (no new mutations); every hook `onSettled: invalidateQueries(['gate'])` (queue + ramp refetch); state-explicit sonner toasts ("Contato aprovado — saída enfileirada" / "Atrativo rejeitado → DLQ"); 401 surfaces the session-expired toast.
- **`/gate` page** — the master-detail layout (xl gap) wiring `GateQueue` ↔ `GateReviewPanel`, injecting the gate action bar ("Aprovar contato" primary + "Rejeitar" behind the **"Rejeitar atrativo?"** destructive AlertDialog with the UI-SPEC body copy) into the action-agnostic panel.
- **Tests:** approve → invalidate → refetch (queue drops the approved row); approve 401 settles without throwing; reject via the existing endpoint; the `/gate` page across all four MSW states (success row-select + AlertDialog open, empty, error, 401).

## Verification

- `cd dashboard && bunx vitest run components/gate` → **2 files, 14 tests passed** (GateQueue 7 + gate-actions 7 — success/empty/error/401 for the queue and the page, masked-phone label + no-raw-e164, approve invalidate→refetch, reject AlertDialog).
- `cd dashboard && bunx vitest run` (full suite) → **9 files, 53 tests passed** (no regression to the 04-02/04-03/04-04/04-05 slices).
- `cd dashboard && bunx tsc --noEmit` → **clean (exit 0)**.

## Deviations from Plan

### Auto-fixed / minor adjustments

**1. [Rule 3 — Blocking] Reused the DLQ scaffold's PIECES, not the fetch-coupled `ReviewPanel`**
- **Found during:** Task 1 (wiring the gate detail pane).
- **Issue:** The plan's acceptance criterion says "GateReviewPanel imports/reuses the DLQ `ReviewPanel`". But the DLQ `ReviewPanel` is hard-wired to fetch `GET /api/v1/dlq/{rio_id}` — and `atrativos_gate.py` exposes NO equivalent `/atrativos/gate/{id}` detail endpoint; the gate GET returns the full row inline. Importing `ReviewPanel` verbatim would fire the wrong (DLQ) detail fetch against a non-gate endpoint.
- **Fix:** Built `GateReviewPanel` reusing the scaffold's reusable, action-agnostic PIECES — the `ScoreBreakdownPanel` (§7.6) and `StatusBadge` imported directly from `components/dlq/`, plus the identical master-detail shape (left list + right detail with an injected action bar). Same D-06 scaffold reuse intent, correct data source.
- **Files:** `dashboard/components/gate/GateReviewPanel.tsx`.

**2. [Rule 2 — Critical / LGPD defense in depth] redactPhone pass over the normalized JSON dump**
- **Found during:** Task 1 (rendering the Rio normalized payload as JSON).
- **Issue:** The masked-phone label only governs the dedicated phone field; the normalized payload is also dumped as JSON, where an upstream `phone_e164` (if the backend ever regressed its masking) could leak into the DOM.
- **Fix:** `redactPhone` replaces known raw-phone keys (`phone_e164`, `phone`, `telefone`, `phone_number`, `whatsapp`, `whatsapp_e164`) with `"[minimizado]"` before the JSON dump — belt-and-suspenders over the server-side masking (T-04-18). Backed by the adversarial test that injects a raw e164 and asserts it never reaches the DOM.
- **Files:** `dashboard/components/gate/GateReviewPanel.tsx`, `GateQueue.test.tsx`.

All else executed as written.

## Threat Model Compliance

- **T-04-18 (Information Disclosure / phone_e164 PII):** mitigated — `maskedPhoneFrom` reads only pre-masked fields (never `phone_e164`, never reconstructs from parts); the panel labels it "telefone (minimizado)"; `redactPhone` additionally strips raw-phone keys from the JSON dump; an adversarial test asserts no raw E.164 number renders. Sample MSW data carries only `phone_masked`.
- **T-04-19 (Elevation of Privilege / gate approve via BFF):** mitigated — approve/reject go through `apiFetch` → the plan-02 BFF (operator Bearer attached, server-held secret injected); the dashboard never calls FastAPI directly. The Phase 3 send-path compliance gate (human gate + ramp + opt-out) still runs server-side inside `outreach_task`.
- **T-04-20 (Tampering / ramp bypass):** accepted per plan — `RampContext` only DISPLAYS the ramp cap; the ramp is enforced in the Phase 3 send path, not the UI. No UI bypass possible.
- **T-04-SC (npm deps):** accepted — NO new package installs; the slice reuses the plan-02/04 shadcn primitives (`button`/`table`/`badge`/`skeleton`/`separator`/`alert-dialog`/`sonner`) and `@tanstack/react-table`/`react-query` already present.

## Known Stubs

None — the slice is fully wired to the real gate list (`GET /api/v1/atrativos/gate`) and the existing approve/reject mutations through the BFF. The one new fetch the UI assumes — `GET /api/v1/atrativos/whatsapp/ramp-context` — backs the advisory `RampContext`; if the backend has not yet exposed that exact read, the panel degrades gracefully (soft "indisponível" fallback) and never blocks the queue. The `/gate` route is complete and reachable but not yet linked from a global nav shell (that shell lands in a later slice). The MSW handlers are test-only and never imported by app code.

## Threat Flags

| Flag | File | Description |
|------|------|-------------|
| threat_flag: new-endpoint-assumed | dashboard/lib/gate-api.ts | The UI fetches `GET /api/v1/atrativos/whatsapp/ramp-context` for the ramp/quality context panel — this read is NOT in atrativos_gate.py today (the plan's `<interfaces>` references a Redis quality flag + ConsentLog ramp context but no single read endpoint). The panel degrades gracefully if absent, but a future backend slice should expose this read (or the UI should be repointed at the existing quality-flag/ramp source). No PII or mutation surface — read-only context. |

## Self-Check: PASSED

All 10 created slice files exist; both task commits (`9f87e76`, `fd40d63`) present in git history; `bunx vitest run components/gate` (14) + full suite (53) + `bunx tsc --noEmit` all green.

---
*Phase: 04-dashboard-territorial-cms*
*Completed: 2026-06-16*
