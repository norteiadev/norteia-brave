.PHONY: pipeline/up pipeline/down pipeline/migrate pipeline/run pipeline/test pipeline/test-unit

# Boot Postgres (pgvector) and Redis for local dev and offline test suite
pipeline/up:
	docker compose up -d

# Stop and remove containers
pipeline/down:
	docker compose down

# Apply Alembic migrations to the local database
pipeline/migrate:
	alembic upgrade head

# Run the fixture pipeline CLI (exercises Nascente → Rio → score → route → Mar push)
pipeline/run:
	python -m brave.cli run-fixture

# Run full test suite (requires docker-compose services running)
pipeline/test:
	pytest -q

# Run only unit tests (no external services required)
pipeline/test-unit:
	pytest tests/unit -q

# Install the project in editable mode with dev dependencies
install:
	uv pip install -e ".[dev]"

# Lint and format check
lint:
	ruff check brave/ tests/
	ruff format --check brave/ tests/

# Apply formatting fixes
fmt:
	ruff format brave/ tests/
	ruff check --fix brave/ tests/
