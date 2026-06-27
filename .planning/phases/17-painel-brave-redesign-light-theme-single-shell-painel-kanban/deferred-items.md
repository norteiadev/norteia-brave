# Deferred items — phase 17

Out-of-scope discoveries logged during execution (not fixed; not caused by this plan's changes).

## Pre-existing typecheck errors (predate 17-01)

`bun run typecheck` reports two errors in files NOT touched by plan 17-01:

1. `components/engine/__tests__/EngineControl.test.tsx:18` — TS2322: `buildStatus`
   helper omits `depth`, so the literal isn't assignable to `EngineStatus`
   (`depth` is `EngineDepth | null`, not optional). Pre-existing test helper gap.
2. `mocks/handlers/mar-ready.ts:64` — TS18048: `params.id` possibly `undefined`.

Both are in unrelated files; the new `components/painel/*` and `app/painel/page.tsx`
type-check cleanly. The full Vitest suite (27 files / 176 tests) is green. Fix under
a dedicated typing-cleanup task, not this UI slice.
