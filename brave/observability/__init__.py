"""Observability package — LLM tracking, USD cost guard, and audit logging.

Components:
  - llm_tracker   — writes llm_generations rows + calls cost guard pre-dispatch
  - cost_guard    — Redis-based enforcing daily USD ceiling (OBS-02)
  - audit         — writes audit_log rows; structlog JSON integration

Phase 1: module stubs (filled in Plan 1-02/1-03).
"""
