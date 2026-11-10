.PHONY: pipeline/up pipeline/down pipeline/migrate pipeline/run pipeline/test pipeline/test-unit install lint fmt

# Offline test env: test DB + Redis db 15, never real externals.
TEST_ENV = env -u RUN_REAL_EXTERNALS \
	BRAVE_DB_URL=postgresql+psycopg://brave:brave@localhost:5432/norteia_brave_test \
	BRAVE_DB_REDIS_URL=redis://localhost:6379/15

# Start the whole docker-compose stack: postgres (pgvector), redis, migrate (one-shot:
# alembic upgrade + seed reference data + seed config), api (:8000), worker, beat and
# dashboard (:3000)
pipeline/up:
	docker compose up -d

# Stop and remove containers
pipeline/down:
	docker compose down

# Apply Alembic migrations to the local database
pipeline/migrate:
	.venv/bin/alembic upgrade head

# Run the fixture pipeline CLI (exercises Nascente → Rio → score → route → Mar push)
pipeline/run:
	.venv/bin/python -m brave.cli run-fixture

# Run full test suite against the test DB (requires postgres + redis running)
pipeline/test:
	$(TEST_ENV) .venv/bin/pytest -q

# Run only unit tests (test env exported; no real externals)
pipeline/test-unit:
	$(TEST_ENV) .venv/bin/pytest tests/unit -q

# Sync the venv from uv.lock, with dev dependencies
install:
	uv sync --extra dev

# Lint and format check
lint:
	.venv/bin/ruff check brave/ tests/
	.venv/bin/ruff format --check brave/ tests/

# Apply formatting fixes
fmt:
	.venv/bin/ruff format brave/ tests/
	.venv/bin/ruff check --fix brave/ tests/
