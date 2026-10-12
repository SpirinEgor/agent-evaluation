# Default target
.PHONY: all
all: help

## Clean up generated files and virtual environment
.PHONY: clean
clean:
	rm -rf .venv
	rm -rf __pycache__
	rm -rf *.egg-info
	rm -rf .pytest_cache
	rm -rf dist
	rm -rf build

## Run core tests (requires: uv sync --extra dev)
.PHONY: test
test:
	uv run pytest tests/ --ignore=tests/test_voice --ignore=tests/test_streaming --ignore=tests/test_gym --ignore=tests/test_domains/test_banking_knowledge

## Run voice and streaming tests (requires: uv sync --extra dev --extra voice)
.PHONY: test-voice
test-voice:
	uv run pytest tests/test_voice tests/test_streaming -m "not full_duplex_integration"

## Run knowledge/banking tests (requires: uv sync --extra dev --extra knowledge)
.PHONY: test-knowledge
test-knowledge:
	uv run pytest tests/test_domains/test_banking_knowledge

## Run gymnasium tests (requires: uv sync --extra dev --extra gym)
.PHONY: test-gym
test-gym:
	uv run pytest tests/test_gym

## Run all tests (requires: uv sync --all-extras)
.PHONY: test-all
test-all:
	uv run pytest tests/

## Start the Environment CLI for interacting with domain environments
.PHONY: env-cli
env-cli:
	uv run python -m tau2.environment.utils.interface_agent

## Lint code with ruff
.PHONY: lint
lint:
	uv run ruff check .

## Format code with ruff
.PHONY: format
format:
	uv run ruff format .

## Lint and fix issues automatically
.PHONY: lint-fix
lint-fix:
	uv run ruff check --fix .

## Run both linting and formatting
.PHONY: check-all
check-all: lint format

## Generate leaderboard submission JSON schema from Pydantic models
.PHONY: generate-schema
generate-schema:
	uv run python -m tau2.scripts.leaderboard.generate_schema

## Check that leaderboard submission JSON schema is up-to-date
.PHONY: check-schema
check-schema:
	uv run python -m tau2.scripts.leaderboard.generate_schema --check

## Install pre-commit hooks
.PHONY: setup-hooks
setup-hooks:
	uv run pre-commit install

## Display online help for commonly used targets in this Makefile
.PHONY: help
help:
	@awk '/^[a-zA-Z_\/\.0-9-]+:/ {        \
		nb = sub( /^## /, "", helpMsg );  \
		if (nb)                           \
			print  $$1 "\t" helpMsg;      \
	}                                     \
	{ helpMsg = $$0 }' $(MAKEFILE_LIST) | \
	column -ts $$'\t' |                   \
	expand -t 1 |                         \
	grep --color '^[^ ]*'

# Banking release tests (offline; requires uv sync --extra dev --extra platform).
.PHONY: test-banking-basic test-banking-package test-banking-tools

test-banking-basic:
	uv run pytest tests/test_domains/test_banking_ru/test_whole_full_catalog_cases.py -q

test-banking-package:
	uv run pytest tests/test_domains/test_banking_ru/test_release_package.py tests/test_platform/test_package.py -q

test-banking-tools:
	uv run pytest tests/test_domains/test_banking_ru/test_tool_contract_catalog.py -q
