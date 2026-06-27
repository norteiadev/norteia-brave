---
phase: 17-painel-brave-redesign-light-theme-single-shell-painel-kanban
verified: 2026-06-27T13:45:00Z
status: passed
score: 6/6 must-have clusters verified
overrides_applied: 0
re_verification:
  previous_status: none
  note: initial verification
gaps: []
---

# Phase 17: Painel Brave redesign — light single-shell + Painel Kanban (slice 1) — Verification Report

**Phase Goal:** At a NEW route `/painel` ALONGSIDE the existing 10 dark routes (non-breaking): a light-theme shell (232px sidebar + topbar + view-switcher) hosting a real-data Painel Kanban (2 metric cards, type filter, UF scope, 5 stage columns of draggable record cards). Topbar motor switch + TA pill wired to engine-api. Drag/retry fire ONLY real existing mutations; unmapped drops revert+toast. Existing routes + suites stay green. Light theme scoped. No new backend endpoints. Tokens from CONTEXT, not scattered hex.

**Verified:** 2026-06-27
**Status:** passed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| # | Truth (goal-backward) | Status | Evidence |
|---|-----------------------|--------|----------|
| 1 | Shell + route exist & render at NEW `/painel` (light shell, 232px sidebar, 6 nav items / 2 groups, topbar, view-switcher; non-painel = "Em breve") | ✓ VERIFIED | `app/painel/page.tsx` wraps subtree in `.painel-light`, local `useState` view-switcher, renders `<PainelView>` for painel else `<EmBreve>`. `PainelSidebar.tsx` is `w-[232px]`. `nav.ts` defines 6 items in NAV_GROUPS (Processamento×4 / Operação×2). Topbar `useQuery(fetchEngineStatus)` + `useQuery(fetchTASessionStatus → /api/v1/tripadvisor/session/status)`. New route, no existing route touched. |
| 2 | Real-data Kanban: metrics from usePainelMetrics (envelope total + engine counts.nascente), board from usePainelBoard (destinos+atrativos), 5 columns, RecordCard fields incl. score band via StageBadge, retry on descarte, no invented fields | ✓ VERIFIED | `painel-data.ts`: `usePainelMetrics` reads `data.total` envelope (server count, `limit:1` count queries) + `engine.data.counts.nascente`; `usePainelBoard` fetches both lists → `toPainelCards` (PII-free allow-list, no phone_e164). `COLUMN_DEFS` = 5 columns. `RecordCard.tsx` renders `<StageBadge score={card.score}/>`, chip, name, UF mono chip, município, source (hidden when null), duplicado flag, and ⚠ falha + ↺ Reprocessar on descarte. Both envelope fields confirmed present in `destinos-api.ts`/`engine-api.ts`. |
| 3 | Drag/retry honesty: closed allow-list firing ONLY real mutations; unmapped → toast + revert + NO fetch; reprocess+atrativo throws | ✓ VERIFIED | `painel-actions.ts`: `mapDrop` switch returns concrete action only for mar/descarte/dlq(destino); nascente/in_progress/same-column/atrativo-dlq → `null`. `mapRetry` → destino only else null. `usePainelMutations.drop/retry`: null mapping `toast.error(UNAVAILABLE)` + `return` BEFORE `mutation.mutate` (no fetch); `onError` → `onRevert()` + toast. `runAction` throws on reprocess+atrativo. Real fns `promoteDestino/descarteDestino/reprocessDestino` (destinos-api), `descartarAtrativo` (atrativos-api), `promoteAtrativo` (mar-ready-api) all confirmed to exist. |
| 4 | Non-regression: .painel-light append-only; no existing component/route/layout modified; existing suites green; full suite 34 files / 236 tests | ✓ VERIFIED | `git diff da9778b..HEAD -- dashboard/` (excl painel) = ONLY `app/globals.css`. Diff of globals.css = **0 removed lines** (`:root`/`.dark`/`@theme` untouched, append-only `.painel-light` block). `bun run test` → **34 files passed / 236 tests passed**. |
| 5 | Scope discipline: deferred views/edit-drawer/source-modal/theme-toggle NOT built; source read-only; no new backend endpoint | ✓ VERIFIED | Non-painel views render `<EmBreve>` placeholder only (sidebar entries for duplicados/mapeamento/etc are SVG icon defs, not impls). No `ThemeToggle/useTheme/setTheme/Drawer/SourceModal` in painel tree. Source button has no onClick (read-only). `git diff` shows no `app/api/**` or backend `.py` changes. |
| 6 | Tokens: painel components reference scoped `.painel-light` CSS vars, not scattered hardcoded hex | ✓ VERIFIED | `grep` for `#rrggbb` across `components/painel/`, `app/painel/`, `lib/painel-*.ts` (excl var() + tests) = **0 matches**. All literal hex live once in the `.painel-light` block in globals.css; components use `var(--painel-*)`/`var(--status-*)`/`var(--card)`. |

**Score:** 6/6 must-have clusters verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `dashboard/app/painel/page.tsx` | Client SPA shell + view-switcher under `.painel-light` | ✓ VERIFIED | 69 lines; `painel-light` wrapper + `useState<PainelViewKey>` switcher |
| `dashboard/components/painel/PainelTopbar.tsx` | Topbar wired to engine-api (motor switch, TA pill, source) | ✓ VERIFIED | 208 lines; useQuery×2 + useMutation start/stop |
| `dashboard/components/painel/PainelSidebar.tsx` | 232px sidebar, 6 nav items / 2 groups, footer | ✓ VERIFIED | `w-[232px]`, NAV_GROUPS rendered |
| `dashboard/lib/painel-data.ts` | PainelCard model + pure selectors + usePainelBoard/usePainelMetrics | ✓ VERIFIED | 298 lines; envelope-total metrics, PII allow-list |
| `dashboard/components/painel/PainelMetrics.tsx` | 2 metric cards (EntityMetric props) | ✓ VERIFIED | total/sincronizados/falhas/progresso% |
| `dashboard/components/painel/PainelFilters.tsx` | type segmented control + UF multi-select | ✓ VERIFIED | imports BR_UFS |
| `dashboard/components/painel/PainelBoard.tsx` | 5-column horizontal-scroll board, drag handlers | ✓ VERIFIED | buildColumns, nascenteCount prop, drop targets |
| `dashboard/components/painel/RecordCard.tsx` | draggable card reusing StageBadge bands | ✓ VERIFIED | StageBadge + retry button on descarte |
| `dashboard/lib/painel-actions.ts` | closed allow-list drop/retry → real mutation + hook | ✓ VERIFIED | mapDrop/mapRetry/runAction/usePainelMutations |
| `dashboard/components/painel/PainelView.tsx` | wired container (replaces 17-01 stub) | ✓ VERIFIED | composes metrics+filters+board, optimistic+revert |
| `dashboard/app/globals.css` | append-only `.painel-light` token block | ✓ VERIFIED | 0 removed lines vs base da9778b |

### Key Link Verification

| From | To | Status | Details |
|------|-----|--------|---------|
| PainelTopbar | engine-api fetchEngineStatus/startEngine/stopEngine/fetchTASessionStatus | ✓ WIRED | useQuery + useMutation present |
| painel-data usePainelBoard | destinos-api/atrativos-api fetch lists | ✓ WIRED | both useQuery + toPainelCards |
| painel-data usePainelMetrics | list envelope totals + engine counts.nascente | ✓ WIRED | `.data.total` + `counts.nascente` |
| painel-actions | promote/descarte/reprocess + descartarAtrativo + promoteAtrativo | ✓ WIRED | all 5 imports resolve to existing exports |
| PainelView | PainelBoard/usePainelBoard/usePainelMetrics + drop/retry | ✓ WIRED | composes + dispatches via usePainelMutations |
| RecordCard | cms/StageBadge | ✓ WIRED | renders score band |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Full dashboard suite green | `bun run test` | 34 files / 236 tests passed | ✓ PASS |
| painel-actions allow-list honesty | painel-actions.test.ts | 17 tests passed (null for nascente/in_progress/same-col/atrativo-dlq/atrativo-retry) | ✓ PASS |
| painel-data selectors/hooks | painel-data.test.ts | 17 tests passed | ✓ PASS |
| globals.css append-only | `git diff da9778b..HEAD -- globals.css \| grep '^-' \| wc -l` | 0 removed lines | ✓ PASS |
| no scattered hex | grep `#rrggbb` in painel tree | 0 matches | ✓ PASS |
| no new backend endpoint | `git diff` app/api + *.py | 0 changes | ✓ PASS |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| UI-PAINEL-1 | 17-01..05 | Synthetic id tracking the whole slice-1 scope (no REQUIREMENTS.md entry — noted in plan frontmatter) | ✓ SATISFIED | All 6 truth clusters above |

### Anti-Patterns Found

None blocking. Notable (intentional per 17-CONTEXT, not gaps):
- `card.source` is always `null` this slice (no list field today) → source label hidden by design. CONTEXT explicitly allows.
- "Falhas" metric uses `routing=descarte` only (CONTEXT: "Use routing=descarte for slice 1").
- Nascente column is count-only (no draggable cards) — rio-backed lists don't surface nascente-only records; count comes from engine counts (17-02 design note).
- No `TODO/FIXME/XXX/HACK/PLACEHOLDER` debt markers found in painel source files.

### Human Verification Required

None required for goal sign-off. The phase output contract specifies `status: passed|failed` and every must-have is code-verifiable + suite-backed. (Optional, non-blocking: a human may eyeball visual fidelity of `/painel` against `design/Painel-Brave.dc.html`, but pixel fidelity is out of the functional goal's scope.)

### Gaps Summary

No gaps. All six goal-backward truth clusters are verified against shipped code on `main`: the `/painel` light shell + Kanban renders real data, the drag/retry mapping is a closed allow-list over real existing mutations (unmapped → toast + revert + no fetch, reprocess+atrativo throws), the `.painel-light` theme block is provably append-only (0 removed lines, dark `:root`/`.dark`/`@theme` untouched), the only non-painel file changed is globals.css, deferred features are not built, the source trigger is read-only, no backend endpoint was added, painel components carry zero scattered hex, and the full dashboard suite passes 34 files / 236 tests.

---

_Verified: 2026-06-27T13:45:00Z_
_Verifier: Claude (gsd-verifier)_

## VERIFICATION PASSED

All 6 goal-backward truth clusters verified in code; `.painel-light` is append-only, drag/retry fires only real existing mutations with honest revert+toast, and the full dashboard suite passes 34 files / 236 tests with no regression.
