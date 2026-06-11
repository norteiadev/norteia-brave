"""Clients package — the single testability seam for all external systems (D-18).

All outbound I/O (LLM, NorteiaApi, Places, OTA, Apify, WhatsApp, Mtur, NotebookLM)
flows through typed Protocol interfaces defined in clients/base.py.

Production code accepts Protocol types.
Tests inject fake implementations from tests/fakes/.
Real implementations live in this package (Phase 1: LLM stub + NorteiaApiClient).

No test should bypass this boundary — pytest-socket enforces it in CI.
"""
