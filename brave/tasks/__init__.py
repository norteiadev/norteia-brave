"""Tasks package — Celery application, beat schedule, and pipeline tasks.

Components:
  - celery_app     — Celery() instance with redbeat config; queue definitions
  - beat_schedule  — RedBeatScheduler entries (sweep_uf per UF)
  - pipeline       — process_nascente, push_mar, reprocess_record tasks

Phase 1: module stubs (Celery app wiring filled in Plan 1-02/1-03).
"""
