# Phase 7: Real Places Hardening + Targeted Atrativos Discovery + Mtur Refresh - Discussion Log

> **Audit trail only.** Decisions live in CONTEXT.md.

**Date:** 2026-06-17
**Phase:** 7-Real Places Hardening + Targeted Atrativos Discovery + Mtur Refresh
**Mode:** operator-directed (findings from a real load-test attempt; user chose "Phase 7 via GSD" over a quick patch)
**Origin:** Attempting the user's load test (10 destinos × 10 atrativos, real data, fresh Mtur) surfaced a chain of real-data gaps the offline suite never exercised.

---

## Real-data gaps found (live dogfooding)
| # | Gap | Evidence | Decision |
|---|-----|----------|----------|
| 1 | `google-maps-places` SDK not installed / not in pyproject | `ModuleNotFoundError: No module named 'google'` on RealPlacesClient | Installed + declared (`uv add google-maps-places>=0.9.0`) |
| 2 | `RealPlacesClient` omits `X-Goog-FieldMask` | Live call → `400 INVALID_ARGUMENT: FieldMask is a required parameter` | D-01: add field mask metadata to text_search + place_details |
| 3 | `text_search` returns no município; `_resolve_parent_destino("")` → `contains("")` mislinks every atrativo to one arbitrary Mar parent | code read (places.py returns only id/name/address/types/location; discovery_agent.py fallback) | D-02: map addressComponents→município + name→IBGE via Mtur table; quarantine on unresolved |
| 4 | `DiscoveryAgent.produce(uf)` = UF-wide sweep (~40 places, capital-biased) — can't give 10/destino | code read | D-03: targeted per-município discovery |
| 5 | Mtur seed = 16-row hand sample, not current Mapa do Turismo | `data/mtur/municipios_mtur_2024.csv` (16 rows) | D-04: refresh to current official categorization |
| 6 | DB polluted from prior runs (341 rio / 121 mar) | live count | D-05: reset operator-run; harness reports absolute run counts |

## Path decision
| Option | Selected |
|--------|----------|
| Phase 7 via GSD — hardening + targeted discovery + Mtur refresh + harness, with tests (recommended) | ✓ |
| Quick patch just to demo (no GSD, no tests) | |
| Destinos only for now | |
**User选 Phase 7 via GSD.**

## Claude's Discretion
field-mask string contents, município normalization, targeted-discovery surface (DiscoveryAgent method vs sweep wrapper), harness CLI args, commit granularity.

## Deferred
Apify signal · norteia-api push · WhatsApp outreach · national Mtur fan-out · full IBGE municipality table.
