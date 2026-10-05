SHELL := /bin/bash

UV_CACHE_DIR ?= /tmp/researchflow-uv-cache
export UV_CACHE_DIR

HOSTED_ENV_FILE ?= $(HOME)/.config/researchflow/hosted-development.env
UNAME_S ?= $(shell uname -s)

ifeq ($(UNAME_S),Darwin)
DOCKER_HOST ?= unix://$(HOME)/.colima/default/docker.sock
export DOCKER_HOST
endif

.PHONY: sync format format-check lint typecheck test-unit test-integration test quality \
	container-start container-stop container-status db-start db-stop db-reset db-migrate \
	db-heads test-stack-prepare test-stack-guard test-stack-start test-stack-migrate \
	test-stack-status test-stack-stop test-stack-destroy guard-local-test guard-hosted \
	hosted-db-current hosted-db-dry-run hosted-db-migrate hosted-smoke

TEST_STACK_WORKDIR := $(CURDIR)/.researchflow-test-stack
TEST_STACK_PROJECT_ID := researchflow-test
TEST_STACK_DATABASE_URL := postgresql+psycopg://postgres:postgres@127.0.0.1:55322/postgres
TEST_STACK_API_URL := http://127.0.0.1:55321

sync:
	uv sync --frozen --all-groups

format:
	uv run ruff format .

format-check:
	uv run ruff format --check .

lint:
	uv run ruff check .

typecheck:
	uv run mypy

test-unit:
	uv run pytest -m unit tests/unit

guard-local-test:
	@PERSISTENCE_ENVIRONMENT=local \
		TEST_DATABASE_URL=$(TEST_STACK_DATABASE_URL) SUPABASE_URL=$(TEST_STACK_API_URL) \
		uv run python -m researchflow.infrastructure.database.commands local-test

guard-hosted:
	@test -r "$(HOSTED_ENV_FILE)" || (echo "Hosted profile is missing" >&2; exit 2)
	@set -a; source "$(HOSTED_ENV_FILE)"; set +a; \
		uv run python -m researchflow.infrastructure.database.commands hosted-development

container-start:
ifeq ($(UNAME_S),Darwin)
	@colima status >/dev/null 2>&1 || colima start --cpus 4 --memory 8 --disk 60
else ifeq ($(UNAME_S),Linux)
	@docker info >/dev/null 2>&1 || (echo "Docker is not reachable; start Docker before running ResearchFlow." >&2; exit 2)
else
	@echo "Unsupported container runtime platform: $(UNAME_S)" >&2; exit 2
endif

container-stop:
	colima stop

container-status:
	colima status
	docker context show
	docker compose version

db-start: container-start
	supabase start

db-stop:
	supabase stop

test-stack-prepare:
	uv run python -m researchflow.infrastructure.test_supabase_stack prepare

test-stack-guard:
	uv run python -m researchflow.infrastructure.test_supabase_stack guard

test-stack-start: container-start test-stack-prepare
	supabase --workdir $(TEST_STACK_WORKDIR) start

test-stack-migrate: test-stack-guard
	uv run python -m researchflow.infrastructure.test_supabase_stack migrate

test-stack-status: test-stack-guard
	supabase --workdir $(TEST_STACK_WORKDIR) status

test-stack-stop: test-stack-guard
	supabase --workdir $(TEST_STACK_WORKDIR) stop --project-id $(TEST_STACK_PROJECT_ID)

test-stack-destroy: test-stack-guard
	supabase --workdir $(TEST_STACK_WORKDIR) stop --project-id $(TEST_STACK_PROJECT_ID) --no-backup

db-reset: test-stack-destroy test-stack-start test-stack-migrate

db-migrate:
	uv run alembic upgrade head

db-heads:
	uv run alembic heads

test-integration: test-stack-guard guard-local-test
	uv run pytest -m integration tests/integration --cov-fail-under=0

hosted-db-current: guard-hosted
	@set -a; source "$(HOSTED_ENV_FILE)"; set +a; uv run alembic current

hosted-db-dry-run: guard-hosted
	@set -a; source "$(HOSTED_ENV_FILE)"; set +a; uv run alembic upgrade head --sql

hosted-db-migrate: guard-hosted
	@set -a; source "$(HOSTED_ENV_FILE)"; set +a; uv run alembic upgrade head

hosted-smoke: guard-hosted
	@set -a; source "$(HOSTED_ENV_FILE)"; set +a; \
		uv run python -m researchflow.infrastructure.supabase_health

test: test-stack-guard guard-local-test
	uv run pytest

quality: sync format-check lint typecheck test
