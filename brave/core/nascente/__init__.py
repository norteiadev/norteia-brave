"""Nascente layer — immutable raw payload store.

Exports:
  store_raw    — ingest a raw payload; idempotent, supersession on update
  get_nascente — retrieve a NascenteRecord by ID
"""

from brave.core.nascente.service import get_nascente, store_raw

__all__ = ["store_raw", "get_nascente"]
