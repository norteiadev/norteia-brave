---
phase: 08-ops-cms-destinos-atrativos-crud-process-observability-cores
plan: "03"
subsystem: dashboard-frontend
tags: [css-tokens, component, badge, journey, navigation, tailwind-v4]
dependency_graph:
  requires: []
  provides:
    - dashboard/app/globals.css (Norteia brand tokens)
    - dashboard/components/cms/StageBadge.tsx
    - dashboard/components/cms/JourneyStepper.tsx
    - dashboard/app/page.tsx (nav links)
  affects:
    - dashboard/components/cms/DestinoList.tsx (08-04 imports StageBadge)
    - dashboard/components/cms/AtrativoList.tsx (08-05 imports StageBadge)
    - dashboard/app/processo/page.tsx (08-06 imports JourneyStepper compact)
tech_stack:
  added: []
  patterns:
    - "CSS custom property token override in :root/.dark blocks"
    - "oklch() color format for Tailwind v4 opacity modifier compatibility"
    - "StageBadge multi-prop badge composition with CSS var tokens only"
    - "JourneyStepper AuditLog-derived step completion inference"
key_files:
  created:
    - dashboard/components/cms/StageBadge.tsx
    - dashboard/components/cms/JourneyStepper.tsx
  modified:
    - dashboard/app/globals.css
    - dashboard/app/page.tsx
decisions:
  - "Used oklch() format for all token values (not hsl()) to ensure Tailwind v4 opacity modifier compatibility (bg-primary/50 etc.) per Pitfall 6"
  - "Destino JourneyStepper steps 1-2 (Nascente+Rio) inferred from record existence — no AuditLog rows for pipeline processing steps (RESEARCH Q3 confirmed)"
  - "StageBadge uses only CSS var references; no hardcoded hex anywhere in the component"
  - "page.tsx SURFACES array append-only (6 existing + 3 new = 9 total)"
metrics:
  duration: "4 minutes"
  completed: "2026-06-19T01:23:14Z"
  tasks: 2
  files: 4
---

# Phase 08 Plan 03: Frontend Visual Foundation — Tokens, StageBadge, JourneyStepper Summary

Swapped Norteia brand color tokens into globals.css (navy primary + terracota accent + off-white background in oklch), created StageBadge and JourneyStepper primitives consumed by all subsequent CMS plans, and added /destinos, /atrativos, /processo nav links.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Token swap (globals.css) + StageBadge component | 26ffa9b | dashboard/app/globals.css, dashboard/components/cms/StageBadge.tsx |
| 2 | JourneyStepper component + page.tsx nav links | a57ce4f | dashboard/components/cms/JourneyStepper.tsx, dashboard/app/page.tsx |

## What Was Built

### globals.css token swap (D-01)
- `--primary`: `oklch(0.23 0.10 253)` (navy #082B5B)
- `--accent`: `oklch(0.48 0.12 30)` (terracota #B14A36)
- `--background`: `oklch(0.98 0.01 90)` (off-white)
- `--primary-foreground`, `--accent-foreground`: off-white for text on colored backgrounds
- Dark block: `--primary: oklch(0.28 0.10 253)`, `--accent: oklch(0.52 0.12 30)`
- New tokens: `--status-in-progress: var(--primary)`, `--status-success: var(--status-mar)`, `--status-warning: var(--status-dlq)`
- `@theme inline` block (lines 11-47) untouched

### StageBadge.tsx (D-02)
- Props: `routing`, `subState`, `score`, `source`, `validationPending`
- Routing states (4): mar/dlq/descarte/in_progress
- Sub_state FSM (5): discovered/contacts_found/signals_gathered/aguardando_consulta_whatsapp/whatsapp_in_progress (navy gradient)
- Score band: ≥85 green, 40–84.9 amber, <40 red
- Source labels: mtur/notebooklm/desmembramento/places_discovery
- Validation pending flag chip
- Zero hardcoded hex values — only CSS var references

### JourneyStepper.tsx (D-06)
- `entityType: "destination" | "attraction"` prop
- Destino 4-step journey: Nascente → Rio/Score → DLQ → Mar
  - Steps 1-2 inferred from record existence (no AuditLog for pipeline processing)
  - Step 3 completion from dlq_validated/dlq_rejected/dlq_reprocessed actions
- Atrativo 7-step journey: discovered → contacts_found → signals_gathered → score → gate → outreach → Mar/DLQ
  - Step completion from atrativo_discovered, sub_state_advanced, whatsapp_gate_approved/rejected
- Each step: circle indicator (completed=filled/green, current=ring/navy, pending=muted)
- Completed steps with AuditLog rows show actor + timestamp
- `compact` prop: horizontal abbreviated step bar (circles only, for /processo)

### page.tsx nav (D-07 scope)
- SURFACES array extended from 6 to 9 entries
- Added: /destinos, /atrativos, /processo

## Verification

All plan verification checks passed:
- `grep "oklch(0.23 0.10 253)" globals.css` → line 57
- `grep "oklch(0.48 0.12 30)" globals.css` → line 63
- `grep "@theme inline" globals.css` → line 11 (unchanged)
- `grep "status-in-progress" globals.css` → line 72
- No hardcoded hex in StageBadge.tsx (count=0)
- `/destinos`, `/atrativos`, `/processo` in page.tsx SURFACES
- `bun run build` → exit 0

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None. StageBadge and JourneyStepper are fully implemented primitives — their rendering
depends on props from caller context, not on empty data. All mapping tables are complete.

## Threat Flags

T-08-10 mitigated: JourneyStepper reads `after_state.sub_state` (routing metadata only)
from AuditLog rows — no phone_e164 or contact data is surfaced in the component.
after_state is accessed only as `row.after_state?.sub_state` (string extraction), not
rendered as raw JSON.

T-08-11 mitigated: @theme inline block (lines 11-47) is unchanged (verified via build
and direct grep).

No new threat surface beyond what the plan's threat model covers.

## Self-Check: PASSED

Files exist:
- dashboard/app/globals.css ✓
- dashboard/components/cms/StageBadge.tsx ✓
- dashboard/components/cms/JourneyStepper.tsx ✓
- dashboard/app/page.tsx ✓

Commits exist:
- 26ffa9b (feat(08-03): token swap globals.css + StageBadge component) ✓
- a57ce4f (feat(08-03): JourneyStepper component + page.tsx nav links) ✓
