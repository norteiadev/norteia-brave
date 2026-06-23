---
phase: 10-engine-stage-depth-selector-cost-gated-collection
plan: 04
subsystem: dashboard / cms
tags: [stage-badge, nascente, cms, presentational, css-var-tokens]
requires:
  - dashboard/components/cms/StageBadge.tsx (existing prop-driven badge)
  - dashboard/components/ui/badge.tsx (Badge primitive)
provides:
  - StageBadge `nascente?: boolean` variant — visual primitive for a Nascente-only record (no Rio row yet)
affects:
  - any CMS surface that renders StageBadge can now show the free-layer "Nascente" stage
tech-stack:
  added: []
  patterns:
    - prop-driven StageBadge variant guarded by its own prop
    - CSS-var token styling (--color-primary), zero hex literals
key-files:
  created: []
  modified:
    - dashboard/components/cms/StageBadge.tsx
    - dashboard/components/cms/__tests__/StageBadge.test.tsx
decisions:
  - "ENG-06: nascente is a self-contained prop-driven badge variant — stage stays implicit by table membership (D-01 table-per-layer), the badge is purely the visual. No backend/schema/endpoint change."
  - "Reused existing --color-primary token (the same family as in_progress/discovered chips) for the Nascente chip — no new token defined."
  - "Placed the nascente chip first in the render sequence so a nascente-only record reads stage-first."
metrics:
  duration: ~6min
  completed: 2026-06-23
requirements: [ENG-06, ENG-07]
---

# Phase 10 Plan 04: Engine Stage-Depth Selector — StageBadge "nascente" variant Summary

Prop-driven `nascente` StageBadge variant renders a stage-first PT-BR "Nascente" chip (via the `--color-primary` CSS-var token) for records parked at the free Nascente layer, completing the cost-checkpoint UX with no backend/schema change.

## What Was Built

- **`StageBadge` `nascente?: boolean` prop** — when truthy, renders a `variant="outline"` Badge labeled **"Nascente"**, styled `border-transparent bg-[var(--color-primary)]/15 text-[var(--color-primary)]` (same `font-mono text-[12px] font-semibold` shape as the other chips). Placed at the **start** of the returned `<span>` so a nascente-only record reads stage-first, and composes with any other prop.
- All existing variants (`routing`/`subState`/`score`/`source`/`validationPending`) are byte-for-byte unchanged; `ROUTING_CLASS`/`SUB_STATE_CLASS`/`scoreClass` untouched.
- **Vitest (+2 cases):** asserts `<StageBadge nascente />` shows "Nascente" + the chip class references a CSS var and contains no hex; `<StageBadge />` and `<StageBadge nascente={false} />` render no "Nascente" text; and a stage-first ordering case (`nascente` chip leads when composed with `routing="dlq"`).

## Verification

- `cd dashboard && bun run test -- StageBadge` → **12/12 pass** (10 existing + 2 new).
- `cd dashboard && bun run test` (full offline suite) → **142/142 pass** (was 140 before this plan; +2 nascente). No MSW/network needed — pure presentational.
- `grep -c 'nascente' dashboard/components/cms/StageBadge.tsx` → ≥1 (prop + branch present).
- `grep -E "#[0-9a-fA-F]{3,6}" dashboard/components/cms/StageBadge.tsx | grep -c .` → **0** (no hardcoded hex introduced).

## TDD Gate Compliance

RED → GREEN observed in this session:
- RED: added the 2 nascente Vitest cases first; ran the suite — both failed (`getByText("Nascente")` not found; stage-first ordering count 1≠2) while the 10 existing cases passed.
- GREEN: added the `nascente` prop + guarded Badge block; re-ran — 12/12 pass.
- Both RED tests and GREEN implementation landed in a single atomic `feat(10-04)` commit (`bb92264`) per the sequential-executor protocol (code+tests committed together).

## Deviations from Plan

None — plan executed exactly as written. No new npm packages, no backend/schema/endpoint change (scope fence honored). Threat T-10-SC (npm install gate) did not trigger.

## Self-Check: PASSED

- FOUND: dashboard/components/cms/StageBadge.tsx (nascente prop + branch)
- FOUND: dashboard/components/cms/__tests__/StageBadge.test.tsx (2 new cases)
- FOUND commit: bb92264 (feat(10-04): add nascente variant to StageBadge)
