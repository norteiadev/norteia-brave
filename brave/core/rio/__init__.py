"""Rio layer — deduplication, normalization, labeling, and routing.

Exports:
  find_duplicate        — two-stage dedup (exact hash + pgvector territorial-key blocked)
  compute_embedding     — Phase 1 stub embedding (zero vector)
  normalize_name        — string normalization helpers
  label_entity          — taxonomy labeling (Phase 1 stub)
  route_by_score        — apply reliability score and set RioRecord.routing
  process_nascente_record — full Rio pipeline for a NascenteRecord
  reprocess_record      — re-run routing for an existing RioRecord
"""
