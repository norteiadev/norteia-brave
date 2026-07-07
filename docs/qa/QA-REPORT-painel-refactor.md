# QA Report — Painel Brave (post spec-driven refactor)

Offline stack (RUN_REAL_EXTERNALS=false): API :8000 + worker + dashboard :3000.
DB reset + seeded via mTur CSV → 2921 destinos, all routing=dlq, 0 Mar, 0 atrativos.
Report-only. No code changed.

## Verified WORKING ✓
- `/` → `/painel` redirect; unauthenticated `/painel` bounces to `/login`; login with operator token works.
- All 10 sidebar views render with 0 console errors: Kanban, Duplicados, Mapeamento, Varreduras, DLQ/Revisão, Conversas WhatsApp, Custo & LLM, Monitor & Funis, Logs, Configuração.
- Motor tri-state (Ligar/Pausar/Desligar) → POST /engine/mode 200, mode + editing_unlocked flip correctly.
- Edit-lock (Phase C): card mutation returns HTTP 423 when motor LIGADO ("Edição travada… Pause o motor"); PASSES through (409 edge-validation) when PAUSADO. Correct.
- Config PATCH validation (Phase D): weight-sum≠100 → 422; threshold>100 → 422; unknown source → 422 ("must be one of default/tripadvisor"); valid → 200.
- GET /config redacts secrets (llm/twilio keys → "***"). LGPD/secret hygiene good.
- DLQ/Revisão: per-row Validar/Descartar; Gate WhatsApp section correctly empty ("Nenhum atrativo aguardando consulta").
- Monitor & Funis: accurate aggregates (Nascente/DLQ 2.921) + funil por camada.
- reset-brave-db re-seeded config_settings defaults (9 rows).

## FINDINGS

### F1 — RESOLVED: INTENDED / BY-DESIGN (user decision)
mTur-alone destinos stay in DLQ by design; publishing to Mar requires multi-source
corroboração (TripAdvisor, once TA-destino corroboration is wired). Not a bug; no code
change. Cold-start note: a mTur-only base publishes 0 destinos to Mar until a corroboração
source exists — expected. Original analysis below.

### F1 (analysis): mTur destinos cannot reach Mar
Every seeded mTur destino scores exactly **60.50** (origem 30 + completude 20 + atualidade ~10.5; corroboração 0, validação 0) → all 2921 land in DLQ, 0 in Mar. Human "Validar" injects validação=100 → ~75.5, still < 80 → stays DLQ. So under the new scoring + removal of the corroboração sources (Desmembramento/NotebookLM, Phase E), a mTur-only destino has **no path to Mar** (max ~75.5). Cold-start needs a corroboração source (TripAdvisor corroboration, or a manual override / steward promote path). Expected consequence of the refactor, but blocks "carga inicial" destinos from publishing. DECISION NEEDED.

### F2 — LOW (UX): same record appears in Nascente AND its routed column
Nascente column = raw /nascente count (2921); each record ALSO appears in its routed column (DLQ). Records double-appear. Counts differ by surface: Kanban DLQ 500 (list limit), DLQ/Revisão fila 50 (list limit), Monitor DLQ 2.921 (aggregate). Consistent (paginated lists vs aggregates) but the differing numbers can confuse an operator. Code shows this is pre-existing painel behavior, not refactor-caused.

### F3 — LOW (UX): "Possível duplicado" badge vs empty Duplicados queue
All DLQ cards show a "Possível duplicado" badge, but Duplicados view = "Nenhum duplicado pendente / Todos os pares resolvidos." Badge is a heuristic flag; the dedup queue is pairs-vs-Mar and Mar is empty → 0 pairs. Explainable, but the mismatch reads as contradictory.

### F4 — LOW (UX): dual motor indicators
Topbar shows "Motor parado" (runtime state) AND "Motor · Pausado/Ligado" (operator mode) at once. Orthogonal by design (Phase C), but two "motor" labels can confuse which is authoritative.

### F5 — LOW / INFO: one transient 502
A single 502 Bad Gateway appeared once in the console during navigation; not reproduced (all subsequent /api/* requests 200). Likely a Next dev-server / polling race. Watch for recurrence.

### F6 — VERY LOW (visual): two nav items highlighted at once
After JS-navigating to Logs, Duplicados + Logs both looked active in the sidebar. Low confidence — likely a lingering :hover, not a real active-state bug.

## NOT TESTED
- DLQ→WhatsApp multi-select batch move + eligibility 422 + branch (LLM number-discovery vs outreach): no atrativos in the seed (mTur seeds destinos only). Needs an atrativos/Places or TripAdvisor sweep to exercise. Backend endpoint verified green in unit/integration tests (Phase F).
- Kanban drag-and-drop edit-lock in the browser (verified at API level instead — 423).
