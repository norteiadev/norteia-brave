---
phase: 03-atrativos-lane-whatsapp-compliance
plan: "01"
subsystem: atrativos-scaffold
tags:
  - consent-log
  - lgpd
  - whatsapp
  - pydantic-schemas
  - alembic-migration
  - fake-clients
  - tdd
dependency_graph:
  requires:
    - Phase 1 (brave/core/models.py AuditLog, Base, Mapped patterns)
    - Phase 1 (brave/config/settings.py StewardConfig pattern)
    - Phase 1 (brave/clients/base.py ApifyClientProtocol, WhatsAppClientProtocol)
    - Phase 2 (alembic/versions/0003 — down_revision chain)
  provides:
    - ConsentLog SQLAlchemy model (consent_log table)
    - Alembic migration 0004_consent_log
    - WhatsAppConfig + RampConfig settings (BRAVE_WA_ / BRAVE_WA_RAMP_ prefixes)
    - AppConfig.whatsapp + AppConfig.ramp fields
    - AtrativoResult, ContactResult, SignalResult, ConversationExtractionResult schemas
    - NullWhatsAppClient (production stub)
    - FakeApifyClient, FakeWhatsAppClient (test fakes)
    - SIGNAL_FIXTURE_OPEN, SIGNAL_FIXTURE_CLOSED constants
  affects:
    - brave/core/models.py (ConsentLog appended after AuditLog)
    - brave/config/settings.py (WhatsAppConfig, RampConfig, AppConfig extensions)
    - tests/fakes/fake_places.py (signal fixture constants added)
tech_stack:
  added: []
  patterns:
    - AuditLog Mapped[T] = mapped_column style copied for ConsentLog
    - StewardConfig BaseSettings + SettingsConfigDict pattern for WhatsAppConfig/RampConfig
    - null_norteia_api.py structural duck-typing pattern for NullWhatsAppClient
    - fake_places.py _check_protocol_compliance pattern for FakeApifyClient/FakeWhatsAppClient
    - DestinoItem/DesmembramentoResult Field pattern for all atrativos schemas
key_files:
  created:
    - brave/lanes/atrativos/__init__.py
    - brave/lanes/atrativos/schemas.py
    - brave/clients/null_whatsapp.py
    - tests/fakes/fake_apify.py
    - tests/fakes/fake_whatsapp.py
    - alembic/versions/0004_consent_log.py
    - tests/unit/test_atrativos_schemas.py
  modified:
    - brave/core/models.py
    - brave/config/settings.py
    - tests/fakes/fake_places.py
decisions:
  - "ConsentLog is a separate table from audit_log (different query pattern: real-time suppression lookup vs. historical trail)"
  - "No Field(alias=...) on WhatsAppConfig/RampConfig fields (CR-02 hard rule)"
  - "NullWhatsAppClient lives in brave/clients/ not tests/ (production code must never import from test tree)"
  - "Migration uses standard B-tree index, not CONCURRENTLY (inside Alembic transaction — Phase 2 lesson)"
  - "Alembic revision 0004 chains from 0003 (down_revision='0003')"
  - "AtrativoResult.tipo has 11 Literal values per CONTEXT.md D-05 requirements"
  - "ConversationExtractionResult.confidence default=0.0 (not None) for reliable default scoring"
metrics:
  duration: "~25 minutes"
  completed_date: "2026-06-15"
  tasks_completed: 2
  files_changed: 10
---

# Phase 03 Plan 01: Package Gate + Scaffold Summary

**One-liner:** ConsentLog model+migration (0004), WhatsApp/Ramp pydantic-settings config classes (BRAVE_WA_* no-alias), AtrativoResult/ContactResult/SignalResult/ConversationExtractionResult Pydantic v2 schemas with instructor Mode.Tools Field descriptions, NullWhatsAppClient production stub, FakeApifyClient/FakeWhatsAppClient test fakes, and SIGNAL_FIXTURE constants — all data contracts for Phase 3 in place.

## Tasks Executed

| Task | Type | Status | Commit |
|------|------|--------|--------|
| Task 1: Verify langgraph-checkpoint-postgres | checkpoint:human-verify | PRE-APPROVED (orchestrator verified PyPI publisher=langchain-ai, version=3.1.0) | N/A |
| Task 2: ConsentLog + migration + schemas + config + fakes | auto (TDD) | Complete | 3043f18 |

## TDD Gate Compliance

| Gate | Commit | Status |
|------|--------|--------|
| RED (failing tests) | ebf39e9 | PASS — 24 tests collected, all fail on missing modules |
| GREEN (implementation) | 3043f18 | PASS — all 24 tests pass, no regressions in existing suite |
| REFACTOR | N/A | Skipped — code is clean as-written |

## What Was Built

### ConsentLog SQLAlchemy Model (brave/core/models.py)

Appended after AuditLog following the exact `Mapped[T] = mapped_column(...)` style. Columns: `id` (UUID PK), `phone_e164` (String(32) NOT NULL indexed), `rio_id` (UUID FK → rio_records.id), `legal_basis`, `norteia_identified` (Boolean), `opted_out` (Boolean default=False), `opted_out_at` (DateTime nullable), `opted_out_keyword` (String nullable), `first_contact_at` / `last_contact_at` / `created_at` (DateTime server_default=now()), `purpose`. FK constraint enforces no orphaned consent rows (T-03-01-01).

### Alembic Migration 0004 (alembic/versions/0004_consent_log.py)

`down_revision="0003"`. `upgrade()` creates consent_log table + `ix_consent_log_phone_e164` B-tree index. `downgrade()` drops index then table. No CONCURRENTLY (inside Alembic transaction — Phase 2 lesson).

### WhatsAppConfig + RampConfig (brave/config/settings.py)

`WhatsAppConfig` (env_prefix="BRAVE_WA_"): twilio_account_sid, twilio_auth_token, from_number, messaging_service_sid, approved_templates. `RampConfig` (env_prefix="BRAVE_WA_RAMP_"): daily_cap=50, quality_pause_threshold="RED". Both use `populate_by_name=True`, zero `Field(alias=...)` calls anywhere (CR-02). AppConfig extended with `whatsapp: WhatsAppConfig` and `ramp: RampConfig` fields.

### Atrativos Lane Package (brave/lanes/atrativos/)

`__init__.py` is an empty module (mirrors brave/lanes/destinos/). `schemas.py` contains 4 Pydantic v2 models, every Field has `description=` for instructor Mode.Tools compliance:
- `AtrativoResult`: nome (min_length=2), tipo (11-value Literal), posicionamento (min_length=10), municipio_nome, municipio_ibge (pattern `^\d{7}$`), uf (min/max_length=2), place_id, origem_value=60.0, completude_value=0.0
- `ContactResult`: all optional (phone_e164, website, ig_handle, email)
- `SignalResult`: business_status (CLOSED_* = hard descarte per D-05), weekday_text, atualidade_value, reviews_recent_count
- `ConversationExtractionResult`: existe/funcionando/horarios/valor (all optional), confidence (float 0.0-1.0, default=0.0)

### NullWhatsAppClient (brave/clients/null_whatsapp.py)

Production-safe offline stub. Structural duck typing (no Protocol import). Records calls in `sent_messages: list[dict]`, returns `{"message_sid": uuid4, "status": "queued"}`. Docstring explicitly states test code should use `FakeWhatsAppClient` instead.

### Fake Clients (tests/fakes/)

- `fake_apify.py`: `FakeApifyClient(fixture_data, raise_on_call)` with `scrape_ig_calls` tracking; `_check_protocol_compliance()` at module bottom
- `fake_whatsapp.py`: `FakeWhatsAppClient(should_fail)` with `sent_messages` tracking, raises `RuntimeError` when `should_fail=True`; `_check_protocol_compliance()` at module bottom
- `fake_places.py` extended: `SIGNAL_FIXTURE_OPEN` (place_id=ChIJtest001, business_status=OPERATIONAL, weekday_text list, recent review) and `SIGNAL_FIXTURE_CLOSED` (place_id=ChIJtest002, business_status=CLOSED_PERMANENTLY, empty lists)

## Task 1 Checkpoint — Package Legitimacy Gate

**Result:** PRE-APPROVED by orchestrator before spawning this agent.

| Check | Result |
|-------|--------|
| PyPI existence | langgraph-checkpoint-postgres 3.1.0 exists |
| Publisher | langchain-ai (official LangChain organization) |
| Source repo | github.com/langchain-ai/langgraph (confirmed >10k stars) |

No code in this plan depends on `langgraph-checkpoint-postgres` — the package gate verified the dependency before any code uses it. Plans 03-02 and onwards will import from `langgraph.checkpoint.postgres.aio`.

## Verification Results

```
uv run --extra dev python -c "from brave.core.models import ConsentLog; ..." → imports ok
ConsentLog.__tablename__ == "consent_log"  ✓
AppConfig().ramp.daily_cap == 50  ✓
FakeApifyClient().scrape_ig_calls == []  ✓
pytest tests/unit/test_atrativos_schemas.py → 24/24 passed  ✓
grep -c "alias=" brave/config/settings.py → 1 (comment only, no actual alias= code)  ✓
brave/clients/null_whatsapp.py does not import from brave.clients.base  ✓
SIGNAL_FIXTURE_CLOSED["business_status"] == "CLOSED_PERMANENTLY"  ✓
```

Note on alias check: The single `alias=` hit in settings.py is inside the module docstring comment that documents the prohibition (`CR-02: No Field(alias=...) on any field...`). There are zero actual `Field(alias=...)` calls in any config class.

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None in this plan — all schemas, configs, and clients are complete (not placeholder-dependent). NullWhatsAppClient is an intentional production stub, not a missing-data placeholder. The `langgraph-checkpoint-postgres` dependency (verified) is not imported here; importing it in the WhatsApp agent is Plan 03-02 scope.

## Threat Flags

None — no new network endpoints, auth paths, or schema changes beyond what the plan's threat model (`T-03-01-01`, `T-03-01-02`, `T-03-01-SC`) covers.

## Self-Check: PASSED

All created files exist on disk. All commits confirmed in git log.

| Item | Status |
|------|--------|
| brave/lanes/atrativos/__init__.py | FOUND |
| brave/lanes/atrativos/schemas.py | FOUND |
| brave/clients/null_whatsapp.py | FOUND |
| tests/fakes/fake_apify.py | FOUND |
| tests/fakes/fake_whatsapp.py | FOUND |
| alembic/versions/0004_consent_log.py | FOUND |
| tests/unit/test_atrativos_schemas.py | FOUND |
| Commit ebf39e9 (RED phase) | FOUND |
| Commit 3043f18 (GREEN phase) | FOUND |
