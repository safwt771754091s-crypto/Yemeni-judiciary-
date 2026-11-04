.PHONY: help install dev run serve offline deploy seed sql test lint fmt docker-build docker-up clean

PY ?= .venv/bin/python
PIP ?= .venv/bin/pip

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

install: ## Create venv and install all dependencies
	python3 -m venv .venv && $(PIP) install --upgrade pip && $(PIP) install -r requirements-dev.txt

dev: ## Run the API with autoreload (foreground: never returns)
	$(PY) -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

run: ## Run the API (production-style)
	$(PY) -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2

serve: ## Bring up Docker + pgvector + corpus + API on PORT (default 12000)
	PORT=$(PORT) ./scripts/serve_public.sh

offline: ## Same, but with no outbound calls at all (local model + hash embeddings)
	OFFLINE=1 PORT=$(PORT) ./scripts/serve_public.sh

deploy: ## Native (no-Docker) deploy: Postgres + schema + corpus + API on PORT
	PORT=$(PORT) ./scripts/deploy_native.sh

seed: ## Ingest the bundled Yemeni legal corpus
	$(PY) scripts/seed_corpus.py

sql: ## Apply the pgvector schema to Supabase
	$(PY) scripts/apply_sql.py

test: ## Run the test-suite
	$(PY) -m pytest -q

lint: ## Ruff lint
	$(PY) -m ruff check app scripts tests

fmt: ## Ruff format
	$(PY) -m ruff format app scripts tests

docker-build: ## Build the API image
	docker compose build

docker-up: ## Start API (+ local pgvector)
	docker compose up -d

clean: ## Remove caches
	find . -type d -name __pycache__ -prune -exec rm -rf {} + ; rm -rf .pytest_cache .ruff_cache
