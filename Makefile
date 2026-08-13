.DEFAULT_GOAL := help
.PHONY: help venv install lint format typecheck check-layers test test-unit test-integration \
        test-e2e quality up down reset logs migrate seed train clean

PYTHON_VERSION := 3.11
UV := uv

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# --- Environment -----------------------------------------------------------

venv: ## Create the Python 3.11 virtualenv (ADR-0007)
	$(UV) venv --python $(PYTHON_VERSION)

install: ## Install the project with dev dependencies and pre-commit hooks
	$(UV) pip install -e ".[dev]"
	$(UV) run pre-commit install

install-all: ## Install every optional dependency group (slow — pulls torch)
	$(UV) pip install -e ".[dev,storage,imaging,versioning,ml,api,auth,worker,monitoring,test-infra]"

# --- Quality ---------------------------------------------------------------

lint: ## Ruff lint
	$(UV) run ruff check src tests pipelines

format: ## Format with Black and fix import order
	$(UV) run ruff check --select I --fix src tests pipelines
	$(UV) run black src tests pipelines

format-check: ## Verify formatting without writing
	$(UV) run black --check src tests pipelines

typecheck: ## Mypy strict
	$(UV) run mypy

check-layers: ## Enforce Clean Architecture boundaries (ADR-0001)
	$(UV) run lint-imports

test: ## Run unit tests (fast, no coverage gate — see test-cov)
	$(UV) run pytest -m unit

test-integration: ## Run integration tests (requires Docker)
	$(UV) run pytest -m integration

test-cov: ## Run unit + integration tests with the 80% coverage gate (requires Docker)
	$(UV) run pytest -m "unit or integration" --cov=factoryai --cov-report=term-missing --cov-report=xml

test-e2e: ## Run end-to-end tests against the compose stack
	$(UV) run pytest -m e2e

quality: lint format-check typecheck check-layers test ## Everything CI runs

# --- Local stack (Phase 2 onwards) -----------------------------------------

# --env-file is required, not cosmetic: Compose derives its default project directory
# from the first -f file's own directory (deploy/compose), not the caller's working
# directory, so host-port overrides in the repo-root .env (README's `cp .env.example .env`
# step) are silently ignored without it.
COMPOSE := docker compose --env-file .env -f deploy/compose/docker-compose.yml

up: ## Start the local platform
	$(COMPOSE) up -d --build

down: ## Stop the local platform, keep volumes
	$(COMPOSE) down

reset: ## Stop and DESTROY local volumes
	$(COMPOSE) down -v

logs: ## Tail all service logs
	$(COMPOSE) logs -f

migrate: ## Apply database migrations
	$(UV) run alembic -c database/alembic.ini upgrade head

migration: ## Create a new migration; usage: make migration m="add x column"
	$(UV) run alembic -c database/alembic.ini revision --autogenerate -m "$(m)"

seed: ## Download, validate and ingest the MVTec bottle dataset
	$(UV) run python scripts/seed_dataset.py --category bottle

train: ## Run the training pipeline with the default config
	$(UV) run factoryai train --config configs/bottle/patchcore.yaml

# --- Housekeeping ----------------------------------------------------------

clean: ## Remove caches and build artifacts
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage coverage.xml dist build
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
