"""Test suite for norteia-brave.

100% offline by default — docker-compose provides Postgres+Redis.
Real external API calls are opt-in via RUN_REAL_EXTERNALS=1 env var.
pytest-socket blocks outbound network calls in CI (hard failure).
"""
