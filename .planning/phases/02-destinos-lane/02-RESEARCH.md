# Phase 2: Destinos Lane — Research

**Researched:** 2026-06-12
**Domain:** Destinos lane producers (Mtur seed, NotebookLM, DesmembramentoAgent), DLQ validate endpoint, batch-by-state promotion to Mar
**Confidence:** HIGH on Phase 1 code seams (verified in repo), HIGH on score math (computed deterministically), MEDIUM on Mtur dataset shape (official portal inaccessible; official naming change confirmed), HIGH on instructor/Mode.Tools pattern

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** MturClient supplies municipalities from a bundled, versioned static seed dataset (CSV/Parquet under a repo `data/` dir, content-hashed for supersession), not a live REST API. Map Mtur categoria → Oferta Principal / Complementar / Apoio. Fully offline-testable.
- **D-02:** NotebookLMIngest ingests all available reports at origem=80; overlap with Mtur resolved by Rio dedup (corroboração boost), not explicit pre-filter.
- **D-03:** DesmembramentoAgent fans out one LLM call per Oferta Principal município using `instructor` + `Mode.Tools` against a `DesmembramentoResult` Pydantic schema. Each valid destino → Nascente origem=40, flagged "LLM-generated, pending validation". Behind `LLMClientProtocol`.
- **D-04:** Each producer populates `*_value` fields in its Nascente payload. The Rio normalizer already reads them from `process_nascente_record`; no change to core.
- **D-05:** Use `simulation.py` harness to confirm records land in DLQ band (51–84.9), not descarte — calibrate on first state before national fan-out.
- **D-06:** The origem=40 firewall is a scoring consequence verified by unit test, not a special-case branch.
- **D-07:** New `PATCH /api/v1/dlq/{rio_id}/validate` sets `normalized.validacao_humana_value = 100`, calls `reprocess_record`, promotes via `promote_to_mar` if routing becomes "mar", and pushes.
- **D-08:** Batch-by-state is a thin endpoint over the same per-record validate. BA/RJ/SP/SC/CE/PE first.
- **D-09:** Mar→`destinations` push fires via idempotent Celery `push_destination_task`, mirroring the existing reprocess "dispatch-or-fallback" pattern.
- **D-10:** Carry IBGE municipality code in `RioRecord.municipio_id`; norteia-api owns the canonical municipality table and resolves IBGE→`municipality_id` on push.

### Claude's Discretion
- Exact `data/` seed file format/location, the `DesmembramentoResult` schema field set, the quarantine destination for malformed LLM output, Celery queue/task names, FastAPI request/response models, and test-fixture layout.

### Deferred Ideas (OUT OF SCOPE)
- Dashboard DLQ batch-review UI (Phase 4).
- Atrativos producers (Phase 3).
- Live Mtur API fetch.
- Auto-tuning of §7.6 weights (TUNE-01).
- OTA price cross-check / freshness-decay cron.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| DEST-01 | MturSeedIngest ingests categorized Mtur municipalities (Oferta Principal/Complementar/Apoio) → Nascente (`source=mtur`, origem=100), linked to `municipality_id` | §Mtur seed dataset format, `store_raw` call pattern, `source_ref` format |
| DEST-02 | NotebookLMIngest ingests structured reports → Nascente (`source=notebooklm`, origem=80) | `NotebookLMClientProtocol.fetch_report` seam, payload shape, `store_raw` call |
| DEST-03 | DesmembramentoAgent (§7.4) uses DeepSeek to list real destinos → Nascente (origem=40, flagged), with mandatory Pydantic+`instructor` 2nd-layer validator | `DesmembramentoResult` schema, `LLMClientProtocol.extract` seam, quarantine pattern |
| DEST-04 | Destinos flow through Rio + §7.6 score and land in DLQ by default; origem=40 firewall confirmed | Score math confirms firewall; DLQ reason written by `route_by_score` |
| DEST-05 | Steward validates destinos DLQ batch-by-state → validação humana=100 → Mar → push to `destinations` | `validate` endpoint design, `reprocess_record` + `promote_to_mar` + `push_destination_task` |
| TEST-02 | Score engine and DesmembramentoAgent unit tests covering Mar/DLQ/descarte boundary cases, all offline | Fake fixtures layout, score boundary parametrize patterns |
</phase_requirements>

---

## Summary

Phase 2 is a **brownfield layer** that fills in the client implementations (MturClient, NotebookLMClient, LLMClient for Desmembramento), wires them as `LaneProtocol.produce(uf)` implementations under `brave/lanes/destinos/`, and adds one new DLQ endpoint (`validate` + `validate-batch`) to the already-existing `dlq.py` router. The Phase 1 core (Nascente/Rio/Mar/score/routing) is untouched.

**The most critical research finding is a calibration risk:** With default §7.6 weights and thresholds at cold start (no corroboração, no validação humana), DesmembramentoAgent records (origem=40) are mathematically guaranteed to hit `descarte` (not DLQ) unless `corroboracao_value` > 0. The simulation harness must be run before national fan-out and `atualidade_value` and `completude_value` mappings must be calibrated to ensure Mtur/NotebookLM records land in DLQ (not descarte). Separately, after human validation (validacao_humana=100), Mtur records need `corroboracao_value >= 50` to cross the Mar threshold of 85 — meaning the D-02 corroboration-boost from NotebookLM/Mtur overlap is load-bearing for Mar promotion, not just a nice-to-have.

**Primary recommendation:** Implement the three producers + validate endpoint in that order, gate with simulation-harness calibration on a BA sample before national fan-out, and treat corroboração-value population from dedup-merges as a required pipeline step (not Phase 3 work).

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Mtur seed ingest | Lane (brave/lanes/destinos/) | Core Nascente (store_raw) | Producer writes payload; core stores it |
| NotebookLM report ingest | Lane (brave/lanes/destinos/) | Core Nascente (store_raw) | Same producer pattern |
| DesmembramentoAgent LLM extraction | Lane (brave/lanes/destinos/) | Client boundary (LLMClientProtocol) | All LLM calls behind client seam |
| §7.6 scoring + routing | Core (Rio) | — | Untouched from Phase 1 |
| DLQ validate (single + batch) | API router (dlq.py) | Core Rio (reprocess_record) | Extends existing router; no core change |
| Mar promotion after validation | Core Mar (promote_to_mar) | — | Already idempotent; no change needed |
| Celery push_destination_task | Tasks layer (brave/tasks/) | Client boundary (NorteiaApiClientProtocol) | Mirrors existing push_mar pattern |
| IBGE code threading | Lane payload construction | Core (RioRecord.municipio_id) | Producers set municipio_id in payload; rio reads it via `payload.get("municipio_id")` |
| Fake clients for offline tests | tests/fakes/ | — | New FakeMturClient, FakeNotebookLMClient added alongside existing fakes |

---

## Standard Stack

No new packages required for this phase. The entire standard stack is inherited from Phase 1.

### Core (already installed)
| Library | Version | Purpose | Status |
|---------|---------|---------|--------|
| `instructor` | 1.15.1 [VERIFIED: pip show] | Structured LLM output with Mode.Tools | Already installed |
| `openai` | 2.41.x | OpenRouter client (OpenAI-compatible) | Already installed |
| `pydantic` | 2.13.x | `DesmembramentoResult` schema + 2nd-layer validation | Already installed |
| `fastapi` | 0.136.x | New DLQ validate/batch endpoints | Already installed |
| `celery` | 5.6.x | `push_destination_task` | Already installed |
| `sqlalchemy` | 2.0.x | Session for validate endpoint | Already installed |

### No New Packages Required
This phase adds zero new dependencies. All client implementations and lane code reuse already-installed libraries. The `FakeMturClient` and `FakeNotebookLMClient` are pure Python classes with no new imports.

**Installation:** None required.

---

## Package Legitimacy Audit

> No new packages are installed in Phase 2. This section is intentionally minimal.

| Package | Status |
|---------|--------|
| All packages from Phase 1 | Previously audited and installed |

**Packages removed due to slopcheck [SLOP] verdict:** none
**Packages flagged as suspicious [SUS]:** none

---

## Architecture Patterns

### System Architecture Diagram

```
                    UF sweep (Celery beat)
                           │
               ┌───────────┼───────────┐
               │           │           │
         MturSeedIngest  NotebookLM  Desmembramento
          (origem=100)  (origem=80)  Agent (origem=40)
               │           │           │
               │  fetch_municipalities  │
               │  (static CSV parse)    │  extract(prompt, DesmembramentoResult)
               │                        │  → instructor + Mode.Tools
               │                        │  → 2nd-layer validate-or-quarantine
               │           │           │
               └───────────┴───────────┘
                           │ store_raw(session, source, source_ref,
                           │          entity_type, uf, payload)
                           │  payload includes: *_value criterion fields
                           │                    municipio_id (IBGE code)
                           ▼
                    nascente_records
                           │
              process_nascente_record(session, nascente, config)
              (existing Rio pipeline — unchanged)
                           │
                    ┌──────┴──────┐
                    │             │
               score < 51     51 ≤ score < 85
               (descarte)         (DLQ)
                                  │
              Steward: PATCH /api/v1/dlq/{rio_id}/validate
                       OR POST /api/v1/dlq/validate-batch?uf=BA
                                  │
              set validacao_humana_value=100 in rio.normalized
              → reprocess_record(session, rio_id, config)
                                  │
                           score ≥ 85?
                         ┌────────┴────────┐
                        yes               no
                         │              stays DLQ
              promote_to_mar(session, rio)
                         │
              push_destination_task.delay(rio_id)
                         │
              NorteiaApiClientProtocol.push_destination(payload)
              (Pact-frozen shape, idempotent by source_ref)
```

### Recommended Project Structure (additions only)

```
brave/
├── lanes/
│   ├── base.py              # LaneProtocol (exists — unchanged)
│   └── destinos/            # NEW
│       ├── __init__.py
│       ├── mtur.py          # MturSeedIngest implements LaneProtocol
│       ├── notebooklm.py    # NotebookLMIngest implements LaneProtocol
│       └── desmembramento.py# DesmembramentoAgent (LLM fan-out)
├── clients/
│   ├── base.py              # Protocols (exists — unchanged)
│   ├── mtur.py              # NEW: MturClient (reads bundled CSV)
│   └── notebooklm.py        # NEW: NotebookLMClient (stub or httpx)
├── api/
│   └── routers/
│       └── dlq.py           # EXTEND: add validate + validate-batch endpoints
└── tasks/
    └── pipeline.py          # EXTEND: add push_destination_task
data/
└── mtur/
    └── municipios_mtur_2024.csv  # NEW: bundled seed file (content-hashed)
tests/
├── fakes/
│   ├── fake_mtur.py         # NEW
│   └── fake_notebooklm.py   # NEW
├── unit/
│   ├── test_score_engine.py  # EXISTS — extend with producer-specific cases
│   └── test_desmembramento.py# NEW: offline tests with FakeLLMClient
└── integration/
    └── test_destinos_lane.py # NEW: end-to-end lane test (offline, DB+Redis)
```

### Pattern 1: Producer populating `*_value` criterion fields in Nascente payload

**What:** Each producer sets all five `*_value` fields in the Nascente payload dict. `process_nascente_record` in `routing.py` reads them from `payload.get("origem_value", 0.0)` etc. into `normalized`. No change to core.

**When to use:** All three producers (Mtur, NotebookLM, DesmembramentoAgent).

**Example — MturSeedIngest:**
```python
# Source: brave/core/rio/routing.py lines 138-149 (existing read path)
# Producers populate these fields:
payload = {
    "name": municipio_name,
    "municipio_id": ibge_code,      # IBGE 7-digit code as string
    "uf": uf,
    "categoria": categoria,          # "Oferta Principal" / "Complementar" / "Apoio"
    # §7.6 criterion values — producers set these:
    "origem_value": 100.0,           # Mtur = 100
    "completude_value": _completude_from_fields(record),
    "corroboracao_value": 0.0,       # 0 at single-source ingest; boosted by dedup
    "atualidade_value": _atualidade_from_publish_date(dataset_version),
    "validacao_humana_value": 0.0,   # 0 until steward validates
    # Additional destino metadata:
    "source_note": "LLM-generated, pending validation",  # for origem=40 only
}
```

### Pattern 2: `source_ref` format for destino producers

**What:** The `source_ref` must be unique per source. The existing `push_mar` task derives `source` from `source_ref.split(":", 1)[0]`. Consistent format critical for idempotency and the Pact contract.

```python
# Mtur
source_ref = f"mtur:{uf}:{ibge_code}"          # e.g. "mtur:BA:2903201"
# NotebookLM  
source_ref = f"notebooklm:{uf}:{ibge_code}"    # e.g. "notebooklm:BA:2903201"
# DesmembramentoAgent
source_ref = f"desm:{uf}:{ibge_code}:{slug}"   # e.g. "desm:BA:2903201:trancoso"
```

### Pattern 3: DesmembramentoAgent with `instructor` + Mode.Tools

**What:** `LLMClientProtocol.extract(prompt, DesmembramentoResult, mode="tools")` is the seam. The real `LLMClient` wraps the instructor/OpenAI client; the fake returns a pre-configured `DesmembramentoResult`. The 2nd-layer validate-or-quarantine is: if `extract` raises a Pydantic validation error (instructor exhaust retries), catch it and call `quarantine_poison` (the existing `PoisonQuarantine` table, NOT the §7.6 DLQ).

```python
# Source: python.useinstructor.com/integrations/openrouter/ (CITED)
# In the real LLMClient implementation:
import instructor
from openai import AsyncOpenAI

client = instructor.from_openai(
    AsyncOpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=llm_config.openrouter_api_key,
    ),
    mode=instructor.Mode.TOOLS,
)
# With data_collection deny in extra_body:
result = await client.chat.completions.create(
    model=llm_config.deepseek_primary_slug,
    response_model=DesmembramentoResult,
    extra_body={
        "provider": {
            "data_collection": "deny",
            "require_parameters": True,
        }
    },
    messages=[{"role": "user", "content": prompt}],
)
```

### Pattern 4: DesmembramentoResult Pydantic schema

**What:** The result of one DesmembramentoAgent call for one Oferta Principal município. Validated by instructor before returning. Fails → quarantine_poison.

```python
from pydantic import BaseModel, Field
from typing import Literal

class DestinoItem(BaseModel):
    nome: str = Field(..., description="Nome turístico do destino (ex: 'Trancoso')")
    tipo: Literal["distrito", "praia", "vila", "localidade", "ilha", "outros"] = Field(
        ..., description="Tipo geográfico do destino"
    )
    posicionamento: str = Field(
        ..., description="Breve posicionamento turístico (ex: 'Vila histórica a 80km de Porto Seguro')"
    )

class DesmembramentoResult(BaseModel):
    municipio_ibge: str = Field(..., description="Código IBGE do município (7 dígitos)")
    municipio_nome: str = Field(..., description="Nome oficial do município")
    destinos: list[DestinoItem] = Field(
        default_factory=list,
        description="Lista de destinos turísticos dentro do município"
    )
```

### Pattern 5: DLQ validate endpoint (extension of existing dlq.py)

**What:** New `PATCH /api/v1/dlq/{rio_id}/validate` — mirrors the existing `reprocess` endpoint shape exactly. Dispatch-or-sync-fallback pattern preserved.

```python
# Source: brave/api/routers/dlq.py lines 57-89 (existing reprocess pattern — VERIFIED)
@router.patch("/api/v1/dlq/{rio_id}/validate", status_code=202)
def validate_dlq_record(
    rio_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> dict:
    """Steward validates a DLQ record (sets validacao_humana=100 → re-score → Mar + push)."""
    rio = db.get(RioRecord, rio_id)
    if rio is None:
        raise HTTPException(status_code=404, detail="RioRecord not found")

    before_state = {"routing": rio.routing, "score": float(rio.score or 0)}

    # Mutate the normalized dict in-place (SQLAlchemy JSON column — needs flag_modified)
    normalized = dict(rio.normalized or {})
    normalized["validacao_humana_value"] = 100.0
    rio.normalized = normalized
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(rio, "normalized")
    db.flush()

    # Re-score (reprocess_record resets routing → in_progress → re-routes)
    config = ScoreConfig()
    reprocess_record(db, rio_id, config)

    # If now routed to mar, dispatch push
    db.refresh(rio)
    if rio.routing == "mar":
        try:
            from brave.tasks.pipeline import push_destination_task
            push_destination_task.delay(str(rio_id))
        except Exception:
            # Sync fallback (no broker in tests/dev)
            from brave.core.mar.service import promote_to_mar
            promote_to_mar(db, rio)

    write_audit(
        session=db,
        action="dlq_validated",
        entity_type=rio.entity_type,
        record_id=rio.id,
        before_state=before_state,
        after_state={"routing": rio.routing, "score": float(rio.score or 0)},
        actor="steward",
    )
    return {"status": "accepted", "rio_id": str(rio_id), "routing": rio.routing}
```

**IMPORTANT:** The `rio.normalized` field is a SQLAlchemy JSON column. Mutating a dict in-place (e.g. `rio.normalized["key"] = value`) does NOT mark the column dirty in SQLAlchemy — you must reassign `rio.normalized = new_dict` AND call `flag_modified(rio, "normalized")`.

### Pattern 6: Batch-by-state validate endpoint

```python
@router.post("/api/v1/dlq/validate-batch", status_code=202)
def validate_batch(
    uf: str = Query(..., description="Two-letter UF code (e.g. 'BA')"),
    entity_type: str = Query("destination"),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> dict:
    """Validate all DLQ records for a UF in one steward action."""
    rows = db.scalars(
        select(RioRecord).where(
            RioRecord.routing == "dlq",
            RioRecord.uf == uf,
            RioRecord.entity_type == entity_type,
        ).limit(limit)
    ).all()

    validated = 0
    for rio in rows:
        # Inline the single-record logic (or call validate_dlq_record internally)
        # ... same pattern as single validate
        validated += 1

    return {"status": "accepted", "uf": uf, "validated": validated}
```

### Anti-Patterns to Avoid

- **Modifying `rio.normalized` in-place without `flag_modified`:** SQLAlchemy will not detect JSON column mutations. Always reassign + `flag_modified`.
- **Calling `promote_to_mar` directly in the validate endpoint without checking `routing == "mar"` first:** `promote_to_mar` asserts nothing; it will create a Mar record regardless of score. Always check `rio.routing == "mar"` after reprocess.
- **Adding a special-case "firewall" branch for origem=40:** D-06 requires the firewall to be a scoring consequence, not a code branch. The score math proves it — no branch needed.
- **Calling `process_nascente_record` again on validate:** `reprocess_record` is the correct call — it re-scores an EXISTING RioRecord without creating a duplicate.
- **Importing from `brave/lanes/` in `brave/core/`:** D-18 boundary violation. Lanes import core; never the reverse.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| LLM structured output with retry | Custom JSON parse + retry loop | `instructor` + `Mode.Tools` + Pydantic | Already in stack; handles retries with validation-error feedback to model |
| Quarantine for LLM failures | New `llm_quarantine` table | Existing `PoisonQuarantine` table + `quarantine_poison()` | Already built in Phase 1; the table is for exactly this — permanent task failures |
| Re-scoring after mutation | Re-call `process_nascente_record` | `reprocess_record(session, rio_id, config)` | `process_nascente_record` is idempotent — it returns the existing record unchanged; `reprocess_record` is the correct path for re-scoring |
| Audit logging | Custom logging | `write_audit(session, action, ...)` | Already built with structlog correlation |
| Score computation | Inline weight math | `compute_score(inp, config)` + `route_by_score()` | Pure function; just mutate `normalized` and call `reprocess_record` |
| IBGE municipality table | Local `municipalities` table | Pass IBGE code in payload; norteia-api resolves | D-10: norteia-api owns canonical municipality table |

**Key insight:** This phase's "don't hand-roll" discipline is about reusing the Phase 1 services exactly as designed, not about external libraries.

---

## §7.6 Score Calibration Analysis

### D-06: Origem=40 Firewall — VERIFIED

With weights origen=30, completude=20, corroboração=20, atualidade=15, validação humana=15:

Maximum score for origem=40 with validação_humana=0 (no matter what other values):
```
40*30/100 + 100*20/100 + 100*20/100 + 100*15/100 + 0*15/100
= 12 + 20 + 20 + 15 + 0 = 67.0
```

**67.0 < 85.0 — firewall is mathematically guaranteed.** [VERIFIED: computed in session]

### Critical Calibration Risk: DLQ vs Descarte Boundary

The D-05 directive to run simulation.py is load-bearing. **Math shows serious descarte risk:**

**DesmembramentoAgent (origem=40):**
- Without corroboração AND without human validation: max score = 47.0 — ALWAYS DESCARTE
- Formula: `40*0.3 + 100*0.2 + 0*0.2 + 100*0.15 + 0*0.15 = 12 + 20 + 0 + 15 + 0 = 47`
- Implication: **DesmembramentoAgent records will hit descarte (not DLQ) unless completude_value or corroboracao_value is non-zero.** This is the Phase 2 DLQ-landfill/black-hole risk.
- Mitigation: assign `completude_value` based on field coverage AND set `atualidade_value` meaningfully (e.g. from the dataset publish date). With `completude=100, atualidade=70, corroboracao=0`: score = 12+20+0+10.5+0 = 42.5 — still descarte. Even with `completude=100, atualidade=100, corroboracao=0`: score = 47.0 — still descarte.
- **Conclusion:** Desmembramento cold-start records will land in descarte without either (a) a corroboration signal from dedup-merge with a Mtur/NotebookLM record, or (b) recalibrating the DLQ threshold_dlq downward (e.g., from 51 to 40) for the initial sweep. This is a **planning decision** to resolve before implementation.

**Mtur (origem=100):**
- Minimum for DLQ: `30 + completude*0.2 + atualidade*0.15 >= 51` ⟹ `completude*0.2 + atualidade*0.15 >= 21`
- Safe landing zone: `completude=70, atualidade=50` → score = 51.5 → DLQ ✓
- Descarte risk: `completude=70, atualidade=30` → score = 48.5 → DESCARTE
- **atualidade_value mapping is the critical calibration variable for Mtur.** Map to a value ≥ 50 for recently-published datasets (e.g., 2024/2025 edition) to ensure DLQ landing.

**NotebookLM (origem=80):**
- `completude=100, atualidade=50` → score = 51.5 → DLQ (minimum safe combo)
- `completude=80, atualidade=70` → score = 50.5 → DESCARTE (!) 
- `completude=100, atualidade=40` → score = 50.0 → DESCARTE (exactly at threshold)
- NotebookLM needs very high completude to reliably land in DLQ.

**After human validation (validação_humana=100): Can records reach Mar (≥85)?**
- Mtur + validação=100, no corroboração: max = `30+20+0+15+15 = 80` → DLQ, not Mar
- Mtur + validação=100 + corroboração=50: `30+20+10+10.5+15 = 85.5` → Mar ✓ (atualidade=70)
- **Corroboração ≥ 50 is required for Mtur records to reach Mar after validation.** The D-02 dedup corroboration boost (when NotebookLM merges with Mtur) is not cosmetic — it is the mechanism that enables Mar promotion.
- Desmembramento + validação=100 + corroboração=0: max = 62.0 → DLQ (never Mar without corroboration)
- Desmembramento + validação=100 + corroboração=100: max = 82.0 → DLQ (still not Mar at 85 threshold)
- **Desmembramento records can never reach Mar at threshold=85**, even with full human validation and full corroboration. They can only be promoted if the threshold is lowered or weights are re-calibrated.

**[ASSUMED] Recommendation for the planner (open question):** Consider lowering `threshold_dlq` to 40 for the initial pass (to avoid descarte black-hole for Desmembramento), and document that `threshold_mar` may need lowering to 70 for the first state sweep before accumulating corroboration. These are calibration decisions that simulation.py should validate with real BA data.

### Simulation Harness Usage

`simulation.py` provides `simulate_distribution(config, samples)` and `generate_cold_start_samples(n, origem_value)`. Add a Wave 0 calibration script:

```python
from brave.core.score.simulation import simulate_distribution, generate_cold_start_samples
from brave.config.settings import ScoreConfig

config = ScoreConfig()  # Default weights
for orig, name in [(100, "mtur"), (80, "notebooklm"), (40, "desmembramento")]:
    samples = generate_cold_start_samples(1000, origem_value=orig)
    dist = simulate_distribution(config, samples)
    print(f"{name}: mar={dist['mar_pct']}% dlq={dist['dlq_pct']}% descarte={dist['descarte_pct']}%")
```

---

## Mtur Dataset

### Official Structure [MEDIUM confidence — portal inaccessible; sourced from official gov.br announcement]

The "Mapa do Turismo Brasileiro" is published by the Ministério do Turismo as open data. As of March 6, 2025, the categorization names were updated:

| Old Category | New Name | Brave mapping |
|-------------|----------|---------------|
| A, B | Municípios turísticos | `Oferta Principal` |
| C, D | Municípios com oferta turística complementar | `Complementar` |
| E | Municípios de apoio ao turismo | `Apoio` |

[CITED: agenciagov.ebc.com.br/noticias/202503/atencao-gestores-mapa-do-turismo-tem-nova-nomenclatura-para-a-categorizacao-dos-municipios]

**Historical dataset (2019):** 2,694 municipalities across 333 tourism regions. [CITED: dados.gov.br/dataset/categorizacao]

**Expected CSV columns [ASSUMED based on domain knowledge + official portal descriptions]:**
- `co_municipio` or `codigo_ibge` — 7-digit IBGE municipality code
- `no_municipio` or `nome_municipio` — municipality name
- `sg_uf` or `uf` — two-letter state code
- `categoria` or `ds_categoria` — category field (A–E in old format, or new nomenclature)
- `no_regiao_turistica` — tourism region name

**The IBGE 7-digit code is the canonical municipality identifier for Brazil.** Cross-reference with IBGE's `kelvins/municipios-brasileiros` CSV or the TSE open data portal for authoritative code lists. [CITED: github.com/kelvins/municipios-brasileiros]

**Recommended seed file approach:**
1. Download the latest "Categorização dos Municípios Turísticos" CSV from `dados.gov.br/dados/conjuntos-dados/categorizacao` or `dados.turismo.gov.br/dataset/mapa-do-turismo-brasileiro`.
2. Place at `data/mtur/municipios_mtur_YYYY.csv` where YYYY is the dataset edition year.
3. Include a SHA-256 hash file alongside it (`municipios_mtur_YYYY.csv.sha256`) for content-verification.
4. `MturClient.fetch_municipalities(uf)` reads and filters the CSV in-process — no network call.

**MturClient implementation (concrete):**
```python
import csv
import pathlib
from typing import Any

DATA_PATH = pathlib.Path(__file__).parent.parent.parent / "data" / "mtur"

def _load_csv(year: str = "2024") -> list[dict]:
    candidates = sorted(DATA_PATH.glob("municipios_mtur_*.csv"), reverse=True)
    if not candidates:
        raise FileNotFoundError("No Mtur seed CSV found in data/mtur/")
    path = candidates[0]
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))

def _map_categoria(raw: str) -> str:
    """Map raw categoria field to Brave canonical value."""
    raw_clean = raw.strip().upper()
    # Old A/B → Oferta Principal; old C/D → Complementar; old E → Apoio
    # New nomenclature: detect by substring
    if raw_clean in ("A", "B") or "TURÍSTICOS" in raw_clean or "TURISTICOS" in raw_clean:
        return "Oferta Principal"
    elif raw_clean in ("C", "D") or "COMPLEMENTAR" in raw_clean:
        return "Complementar"
    elif raw_clean in ("E",) or "APOIO" in raw_clean:
        return "Apoio"
    return "Apoio"  # safe default

class MturClient:
    async def fetch_municipalities(self, uf: str) -> list[dict[str, Any]]:
        rows = _load_csv()
        result = []
        for row in rows:
            row_uf = (row.get("sg_uf") or row.get("uf") or "").strip().upper()
            if row_uf != uf.upper():
                continue
            ibge = (row.get("co_municipio") or row.get("codigo_ibge") or "").strip()
            name = (row.get("no_municipio") or row.get("nome_municipio") or "").strip()
            categoria_raw = (row.get("categoria") or row.get("ds_categoria") or "").strip()
            result.append({
                "ibge_code": ibge,
                "name": name,
                "categoria": _map_categoria(categoria_raw),
                "uf": uf,
            })
        return result
```

**[RISK: Column name uncertainty]** The exact column names in the current CSV are unverified. The implementation must handle at least two common column name patterns and fail with a clear error if neither matches. A Wave 0 task should download the real CSV, inspect columns, and finalize the parser before coding `MturClient.fetch_municipalities`.

---

## DesmembramentoAgent Design

### DesmembramentoResult Schema (recommended)

```python
# brave/lanes/destinos/schemas.py
from pydantic import BaseModel, Field
from typing import Literal

class DestinoItem(BaseModel):
    nome: str = Field(..., min_length=2, description="Nome turístico do destino")
    tipo: Literal["distrito", "praia", "vila", "localidade", "ilha", "balneario", "outros"]
    posicionamento: str = Field(
        ..., min_length=5,
        description="Breve posicionamento turístico"
    )

class DesmembramentoResult(BaseModel):
    municipio_ibge: str = Field(..., pattern=r"^\d{7}$")
    municipio_nome: str
    destinos: list[DestinoItem] = Field(default_factory=list)
```

### Prompt Structure for DeepSeek

The PITFALLS.md (Pitfall 4) recommends **grounding the prompt** with known official localities — feed the Mtur record's município name and ask DeepSeek to classify/position real sub-divisions, not invent them.

```python
DESMEMBRAMENTO_PROMPT = """
Você é um especialista em turismo brasileiro. Liste os destinos turísticos 
(distritos, praias, vilas, localidades) dentro do município de {municipio_nome} ({uf}), 
que é categorizado como Oferta Principal no Mapa do Turismo Brasileiro.

Inclua apenas destinos que existem de fato (com nome turístico reconhecido).
Não invente destinos. Se não houver sub-destinos relevantes, retorne uma lista vazia.

Município: {municipio_nome}
UF: {uf}
Código IBGE: {ibge_code}
"""
```

### Validate-or-Quarantine Pattern

Malformed LLM output (instructor exhausts retries) → `quarantine_poison` (the existing `PoisonQuarantine` table), NOT the §7.6 DLQ.

```python
# In DesmembramentoAgent.produce(uf):
try:
    result: DesmembramentoResult = await llm_client.extract(
        prompt=prompt,
        schema=DesmembramentoResult,
        mode="tools",
    )
except Exception as exc:  # instructor.exceptions.InstructorRetryException or ValidationError
    # Quarantine the failure — not the §7.6 DLQ
    quarantine_poison(
        session=session,
        nascente_id=None,
        task_name="brave.desmembramento",
        error=str(exc),
        payload={"municipio_ibge": ibge_code, "municipio_nome": municipio_nome},
    )
    continue  # Skip this município, continue fan-out
```

---

## IBGE Municipality Linkage (D-10)

### How producers populate `municipio_id`

`RioRecord.municipio_id` is a `String(64)` field. `process_nascente_record` reads it from `payload.get("municipio_id")` (line 159 in routing.py). Producers must include `municipio_id` in the Nascente payload.

**Standard:** Use the 7-digit IBGE code as the string value.

```python
# In MturSeedIngest.produce(uf):
for mun in await mtur_client.fetch_municipalities(uf):
    payload = {
        "name": mun["name"],
        "municipio_id": mun["ibge_code"],  # 7-digit IBGE code
        "uf": uf,
        # ...
    }
    store_raw(session, source="mtur", source_ref=f"mtur:{uf}:{mun['ibge_code']}", ...)
```

### Pact contract shape for `municipio_id`

Inspecting `tests/contract/test_pact_norteia_api.py` (lines 51-68), the frozen Pact `DESTINATION_PAYLOAD` does NOT include `municipio_id` as a top-level field. The `canonical` dict only has `{"name": ..., "uf": ..., "municipio": ...}`.

**RISK (CNTR-01 adjacent):** The frozen Pact contract does not carry `municipio_id` (IBGE code) in the push payload. norteia-api resolves IBGE→`municipality_id` from what? If the Pact `canonical.municipio` is only a name string, the API cannot resolve IBGE codes without an additional field.

**Resolution options:**
1. Add `municipio_ibge_code` to the `canonical` dict in the push payload → **requires a Pact contract update** (breaking change to the frozen contract).
2. Pass IBGE code in `canonical.municipio` as a structured value `{"nome": ..., "ibge_code": ...}`.
3. Accept that IBGE resolution is norteia-api's responsibility from the municipality name (fragile).

**Recommendation:** Treat this as a **Phase 2 RISK** requiring a Pact contract addendum. Add `ibge_code` to `canonical` dict and update the Pact interaction. The contract is still owned by this repo (consumer side); update the Pact fixture and document the change for the Laravel team. This is the correct time to do it before the first real push.

---

## Common Pitfalls

### Pitfall 1: DesmembramentoAgent records land in `descarte` (not DLQ)

**What goes wrong:** With default weights and `atualidade_value` near 0, a Desmembramento record (origem=40, corroboração=0, validação=0) has maximum score ≤ 47.0 — always `descarte`. The planner's success criterion says "land in DLQ by default" — but the math contradicts this for LLM-generated records.

**Why it happens:** The §7.6 weights were designed for steady-state with corroboration; at cold start with no second source, the 40% origem weight drags everything below 51.

**How to avoid:** EITHER (a) lower `threshold_dlq` to 40 for the Phase 2 sweep (calibrated via simulation.py), OR (b) assign a meaningful corroboração_value when a Desmembramento record overlaps a Mtur/NotebookLM record from the same município (i.e., the parent município's Mtur record corroborates the destinos inside it). Option (b) requires the DesmembramentoAgent to set `corroboracao_value > 0` when creating a destino inside an Oferta Principal município that already exists in Nascente.

**Warning signs:** simulation.py shows 100% descarte_pct for origem=40 samples; zero Desmembramento records appear in the DLQ.

### Pitfall 2: Mar threshold unreachable after human validation (without corroboração)

**What goes wrong:** Even after a steward validates (validação_humana=100), a Mtur record without corroboração scores maximum 80.0 — below the Mar threshold of 85. The steward action appears to work but the record never promotes to Mar.

**Why it happens:** `validação humana * 15/100 = 15 points`, but with `origem=100, completude=100, corroboração=0, atualidade=100`: `30+20+0+15+15 = 80`. The 5-point gap requires corroboração ≥ 25 (minimum).

**How to avoid:** Ensure corroboração boost fires when dedup merges a Mtur + NotebookLM record. The dedup code in `routing.py` finds duplicates but does NOT currently boost `corroboracao_value`. This boost must be explicitly implemented in the lane code (update `normalized["corroboracao_value"]` on the surviving record) OR lower `threshold_mar` to 70 for Phase 2.

**Warning signs:** Steward validates many records; `post_validate` routing shows "dlq" (not "mar"); zero Mar records after batch validation.

### Pitfall 3: `flag_modified` omission on JSON column mutation

**What goes wrong:** `rio.normalized["validacao_humana_value"] = 100.0` silently does nothing in SQLAlchemy because JSON column mutations are not tracked automatically.

**How to avoid:** Always use `rio.normalized = {**rio.normalized, "validacao_humana_value": 100.0}` (reassign) + `from sqlalchemy.orm.attributes import flag_modified; flag_modified(rio, "normalized")`. Test with an integration test that reads back the value after commit.

### Pitfall 4: Calling `process_nascente_record` instead of `reprocess_record` in the validate endpoint

**What goes wrong:** `process_nascente_record` checks for an existing `RioRecord.canonical_key` match and returns the existing record unchanged — it does NOT re-score. Only `reprocess_record` resets routing and re-scores.

**How to avoid:** The validate endpoint calls `reprocess_record(session, rio_id, config)` — not `process_nascente_record`.

### Pitfall 5: Embedding stub in Phase 1 dedup

**What goes wrong:** `compute_embedding` returns `[0.0] * 1536` for all records. The HNSW vector search on zero vectors will never find fuzzy duplicates. Mtur + NotebookLM records for the same município will NOT be deduped via vector similarity — they will produce duplicate RioRecords unless exact `content_hash` matches.

**Why it matters for corroboração:** If Mtur and NotebookLM records for the same municipality don't dedup-merge, corroboração_value is never boosted. This worsens the Mar threshold issue (Pitfall 2).

**How to avoid:** For Phase 2, use an exact-match dedup strategy: when a NotebookLM record's `municipio_id` matches an existing Mtur record's `municipio_id`, explicitly boost `corroboracao_value` in the surviving record's `normalized` dict. This can be done in the NotebookLM producer before or after calling `store_raw`, or as a post-ingest step. Document that real embedding-based dedup requires a real embedding model (deferred to a future phase or when LLM cost is acceptable).

---

## Code Examples

### Fake clients for offline testing

```python
# tests/fakes/fake_mtur.py
from typing import Any
from brave.clients.base import MturClientProtocol

class FakeMturClient:
    """Fake Mtur client returning configurable municipality fixtures."""

    def __init__(self, fixtures: list[dict] | None = None) -> None:
        self._fixtures = fixtures or [
            {
                "ibge_code": "2927408",
                "name": "Porto Seguro",
                "categoria": "Oferta Principal",
                "uf": "BA",
            },
        ]
        self.calls: list[str] = []

    async def fetch_municipalities(self, uf: str) -> list[dict[str, Any]]:
        self.calls.append(uf)
        return [m for m in self._fixtures if m.get("uf") == uf]

def _check_protocol_compliance() -> None:
    _client: MturClientProtocol = FakeMturClient()  # noqa: F841
```

### Score boundary test cases for TEST-02

```python
# tests/unit/test_score_engine.py — extend with Phase 2 producer boundary cases
@pytest.mark.parametrize("origem,completude,corroboracao,atualidade,validacao_humana,expected_routing", [
    # D-06 firewall: origem=40 + validacao=0 → never Mar
    (40, 100, 100, 100, 0, "dlq"),     # max without human = 67.0
    # Mtur cold-start safe zone
    (100, 70, 0, 50, 0, "dlq"),        # 51.5
    # Mtur cold-start descarte risk
    (100, 70, 0, 30, 0, "descarte"),   # 48.5
    # NotebookLM minimum DLQ landing
    (80, 100, 0, 50, 0, "dlq"),        # 51.5
    # After validation, Mtur + corroboration → Mar
    (100, 100, 50, 70, 100, "mar"),    # 85.5
    # After validation, Mtur no corroboration → DLQ (not Mar)
    (100, 100, 0, 100, 100, "dlq"),    # 80.0
    # Desmembramento post-validate, good completude/atualidade → DLQ
    (40, 100, 0, 70, 100, "dlq"),      # 57.5
])
def test_producer_score_boundaries(origem, completude, corroboracao, atualidade, validacao_humana, expected_routing):
    config = ScoreConfig()
    inp = ScoreInput(
        origem_value=origem, completude_value=completude,
        corroboracao_value=corroboracao, atualidade_value=atualidade,
        validacao_humana_value=validacao_humana,
    )
    result = compute_score(inp, config)
    assert result.routing == expected_routing
```

### DesmembramentoAgent offline test

```python
# tests/unit/test_desmembramento.py
import pytest
from tests.fakes.fake_llm import FakeLLMClient
from brave.lanes.destinos.schemas import DesmembramentoResult, DestinoItem

def test_desmembramento_agent_happy_path(db_session, score_config):
    """DesmembramentoAgent writes destinos to Nascente with origem=40."""
    fake_result = DesmembramentoResult(
        municipio_ibge="2927408",
        municipio_nome="Porto Seguro",
        destinos=[DestinoItem(nome="Trancoso", tipo="vila", posicionamento="Vila histórica")],
    )
    fake_llm = FakeLLMClient(fixture_result=fake_result)
    # ... instantiate agent, call produce("BA"), assert store_raw called with origem=40

def test_desmembramento_agent_malformed_llm_output_quarantined(db_session):
    """FakeLLMClient raises → malformed output routed to PoisonQuarantine, not §7.6 DLQ."""
    fake_llm = FakeLLMClient(raise_on_call=ValueError("bad schema"))
    # ... assert PoisonQuarantine row created, no NascenteRecord created
```

---

## Pact Contract Gap (RISK-01)

**Finding:** The frozen Pact contract `DESTINATION_PAYLOAD` in `tests/contract/test_pact_norteia_api.py` (lines 51-68) has:
```python
"canonical": {
    "name": "Trancoso",
    "uf": "BA",
    "municipio": "Porto Seguro",
}
```

This carries municipality as a **name string only**, not an IBGE code. Per D-10, norteia-api must resolve IBGE code → `municipality_id`. The current Pact does not provide the IBGE code.

**Risk:** norteia-api cannot reliably link a canonical destination to a municipality record without the IBGE code. Municipality name disambiguation (multiple "Santa Cruz" in Brazil) requires the code.

**Recommended action for the planner:** Create a Wave 0 task: "Update Pact contract to add `ibge_code` to `canonical` dict and re-run Pact test". This is a breaking contract change that must be coordinated with the Laravel team (Trilha 5), but it is cheap now and expensive later. Add as the first task of Phase 2 Wave 1.

---

## Embedding Stub Gap (RISK-02)

**Finding:** `compute_embedding` in `brave/core/rio/dedup.py` returns `[0.0] * 1536` (Phase 1 stub). The docstring says "Real embeddings via LLMClient in Phase 2."

**Risk:** Without real embeddings, vector dedup does not work, and the corroboração boost (critical for Mar promotion) cannot be driven by pgvector similarity. This does not break Phase 2 — it means explicit IBGE-code matching must drive the corroboration boost instead of vector similarity.

**Recommended action:** Document that Phase 2 uses IBGE-code exact matching for corroboração. Real embedding generation deferred unless justified by dedup miss rate on Phase 2 data.

---

## Open Questions (RESOLVED)

1. **DesmembramentoAgent DLQ landing with default thresholds**
   - What we know: with default `threshold_dlq=51`, origen=40 records always hit descarte unless corroboration or threshold is adjusted.
   - What's unclear: should the planner lower `threshold_dlq` to ~40 for Phase 2, or implement explicit corroboration injection?
   - Recommendation: Lower `threshold_dlq` to 40 in `LLMConfig`/`ScoreConfig` for the Desmembramento sweep, then restore after the first state's human validation confirms the distribution. This is exactly what D-05 is for — use simulation.py first.
   - **RESOLVED:** `threshold_dlq` is lowered to 40 (plan 02-02, ScoreConfig default). Desmembramento cold-start records with origine=40 land in DLQ (score ~42-47 > 40), not descarte. Post-calibration origin=40 records remain in DLQ pending human validation.

2. **Mtur CSV column names**
   - What we know: the dataset is published as open data; 2025 nomenclature change confirmed; expected columns are IBGE code + name + UF + categoria.
   - What's unclear: exact column names in the current downloadable file.
   - Recommendation: Wave 0 task — download the file, inspect columns, finalize the parser. Implement with flexible column detection as fallback.
   - **RESOLVED:** Parser is column-flexible — `MturClient._load_csv` tries both pre-2025 column names (`co_municipio`, `no_municipio`, `sg_uf`, `categoria`) and March-2025 nomenclature variants using `row.get(name_a) or row.get(name_b)` fallback chains. Keys off IBGE code + UF + categoria; category mapping handles both old (A–E) and new names (plan 02-03).

3. **Corroboração boost mechanism**
   - What we know: D-02 says overlap is resolved by Rio dedup; the Pact corroboration boost is expected to be triggered by dedup.
   - What's unclear: where exactly does the corroboração_value get updated when a NotebookLM record merges with a Mtur record?
   - Recommendation: The dedup code (`find_duplicate`) returns the duplicate record but does not currently update `corroboracao_value`. The lane code must explicitly: after `store_raw`, if an existing RioRecord is found via dedup, UPDATE the surviving record's `normalized["corroboracao_value"]` += boost and call `reprocess_record`. This is Phase 2 lane code, not core change.
   - **RESOLVED:** Corroboration boost is implemented as an IBGE-exact-match lookup in `NotebookLMIngest.produce` (plan 02-07) — after `store_raw`, the lane queries for an existing RioRecord with matching `municipio_id` + `uf` + `routing in ("dlq","mar")` and boosts `corroboracao_value` += 50 (capped at 100) via `flag_modified` + `reprocess_record`. No pgvector involved (RISK-02 mitigation; embedding stub remains). Not in pgvector dedup code — implemented directly in lane code per D-18 boundary.

4. **NotebookLM client implementation**
   - What we know: `NotebookLMClientProtocol.fetch_report(municipio)` is the seam; no real NotebookLM HTTP client spec exists in the codebase.
   - What's unclear: is NotebookLM accessed via an API, or are reports pre-downloaded as local files?
   - Recommendation: Implement as a local-file-backed client (similar to Mtur) for Phase 2 — reports are structured JSON files under `data/notebooklm/`. This keeps the offline-test mandate intact. A future task can add real HTTP access.
   - **RESOLVED:** Implemented as a local-file reader — `NotebookLMClient.fetch_report` reads structured JSON files under `data/notebooklm/{uf}/{ibge}.json` (plan 02-03). No HTTP client needed for Phase 2. Preserves the offline-test mandate. Real HTTP access deferred to a future phase.

---

## Environment Availability

> This phase adds no new external tools. All dependencies were installed in Phase 1.

| Dependency | Required By | Available | Version | Notes |
|------------|------------|-----------|---------|-------|
| Python 3.12 | Runtime | ✓ | 3.12.x (venv) | |
| PostgreSQL | Integration tests | ✓ (docker-compose) | 16/17 | |
| Redis | Celery tests | ✓ (docker-compose) | 7.x | |
| instructor | DesmembramentoAgent | ✓ | 1.15.1 [VERIFIED: pip show] | |
| OpenRouter API key | Real LLM calls (opt-in only) | Opt-in | — | CI keyless; flag: `run_real_externals=True` |

**Missing dependencies with no fallback:** None.

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Mtur CSV columns are `co_municipio`/`no_municipio`/`sg_uf`/`categoria` (or close variants) | Mtur Dataset | MturClient parser fails silently; need column-detection fallback |
| A2 | Lowering `threshold_dlq` to ~40 for Desmembramento sweep is acceptable per D-05 calibration intent | Score Calibration | DesmembramentoAgent records all hit descarte; DLQ never populated by LLM destinos |
| A3 | NotebookLM "client" is a local-file reader, not a live HTTP client, for Phase 2 | Open Questions | Incorrect if NotebookLM has a real API endpoint that Phase 2 is expected to call |
| A4 | Pact contract update (add `ibge_code` to canonical dict) will be approved by the team | Pact Contract Gap | IBGE resolution in norteia-api breaks silently; municipality linkage wrong |
| A5 | Corroboração boost should be applied in lane code (not core) when IBGE dedup match found | Corroboração mechanism | Low — D-18 boundary supports this; lane code updating normalized is correct |

---

## Sources

### Primary (HIGH confidence)
- `brave/clients/base.py` — 8 client Protocol definitions (verified in session)
- `brave/core/nascente/service.py` — `store_raw` signature and supersession pattern (verified)
- `brave/core/rio/routing.py` — `process_nascente_record`, `reprocess_record`, `route_by_score` (verified)
- `brave/core/mar/service.py` — `promote_to_mar` idempotency pattern (verified)
- `brave/api/routers/dlq.py` — existing reprocess/descarte endpoint shape (verified)
- `brave/core/score/engine.py`, `schemas.py`, `simulation.py` — pure §7.6 engine (verified)
- `brave/core/models.py` — RioRecord.municipio_id field (String(64), verified)
- `brave/tasks/pipeline.py` — push_mar task + dispatch-or-fallback pattern (verified)
- `tests/contract/test_pact_norteia_api.py` — frozen Pact DESTINATION_PAYLOAD shape (verified)
- Score math Python computation — all producer score distributions (computed in session, deterministic)
- `instructor` 1.15.1 (pip show, installed in project venv)

### Secondary (MEDIUM confidence)
- [python.useinstructor.com/integrations/openrouter/](https://python.useinstructor.com/integrations/openrouter/) — Mode.Tools is default for DeepSeek; `from_openai` with OpenRouter base_url pattern
- [python.useinstructor.com/integrations/deepseek/](https://python.useinstructor.com/integrations/deepseek/) — `Mode.Tools` confirmed default for DeepSeek
- [agenciagov.ebc.com.br/noticias/202503/...](https://agenciagov.ebc.com.br/noticias/202503/atencao-gestores-mapa-do-turismo-tem-nova-nomenclatura-para-a-categorizacao-dos-municipios) — Mtur 2025 nomenclature change (A/B→Turísticos, C/D→Complementar, E→Apoio), effective March 6 2025
- [dados.gov.br/dataset/categorizacao](https://dados.gov.br/dados/conjuntos-dados/categorizacao) — official Categorização dataset (portal JavaScript-rendered, content not extractable)

### Tertiary (LOW confidence — [ASSUMED])
- Mtur CSV column names (`co_municipio`, `no_municipio`, `sg_uf`, `categoria`) — inferred from domain convention; unverified against current download
- Recommendation to lower `threshold_dlq` to 40 for Desmembramento — derived from score math; calibration thresholds are tunable by design (D-05)

---

## Metadata

**Confidence breakdown:**
- Phase 1 code seams: HIGH — all files read and verified in session
- Score math / calibration: HIGH — computed deterministically from engine.py weights
- Standard stack: HIGH — all packages already installed; no new deps
- Mtur dataset shape: MEDIUM — official naming change verified; column names assumed
- Pitfalls: HIGH — derived directly from reading the actual code + score formula

**Research date:** 2026-06-12
**Valid until:** 2026-07-12 (stable — score math is deterministic; Mtur dataset is periodic/versioned)
