# Deferred Items — Phase 17.1 (out-of-scope discoveries during 17.1-05)

Pre-existing `bunx tsc --noEmit` errors in files NOT touched by 17.1-05 (the
dashboard CI gate is Vitest + ESLint, not `tsc --noEmit`). Logged, not fixed —
out of scope for the Varreduras frontend slice:

- `components/engine/__tests__/EngineControl.test.tsx:18` — TS2322 EngineStatus
  `depth` optional-vs-nullable mismatch in a test fixture.
- `mocks/handlers/mar-ready.ts:64` — TS18048 `params.id` possibly undefined.
