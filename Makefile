# TRIAD make targets (PRD section 36.4). Everything runs in containers (ADR-0001), so these
# work the same on Linux, macOS and Windows with WSL2.
COMPOSE := docker compose -f docker/compose.yaml
DEV     := $(COMPOSE) --profile tools run --rm -T dev

.DEFAULT_GOAL := help
.PHONY: help toolchains build up down ps logs shell migrate seed smoke test test-unit \
        test-integration test-agent test-security test-acceptance lint scala-test scala-package \
        demo replay clean-volumes versions

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

toolchains: ## Download + verify the Spark tarball the image needs (once)
	bash scripts/fetch_toolchains.sh

build: ## Build the TRIAD toolchain image
	$(COMPOSE) build

up: ## Start the core stack (postgres, mlflow, api, orchestrator, frontend)
	$(COMPOSE) up -d postgres mlflow api orchestrator frontend

up-spark: ## Start the Spark standalone cluster as well
	$(COMPOSE) --profile spark up -d spark-master spark-worker

down: ## Stop everything (volumes are kept)
	$(COMPOSE) --profile spark --profile tools down

ps: ## Show service status
	$(COMPOSE) ps

logs: ## Tail logs of the core services
	$(COMPOSE) logs -f --tail=100 api orchestrator

shell: ## Interactive shell in the dev container
	$(COMPOSE) --profile tools run --rm dev bash

migrate: ## Apply database migrations
	$(DEV) alembic -c backend/db/alembic.ini upgrade head

seed: ## Seed demo users, policy and the demo dataset
	$(DEV) python scripts/seed_demo.py

versions: ## Print the resolved version manifest from inside the image
	$(DEV) python scripts/report_versions.py

smoke: ## Phase-1 compatibility smoke test (hard gate)
	$(DEV) pytest tests/smoke -v

test: ## Full Python test suite
	$(DEV) pytest tests -v

test-unit:
	$(DEV) pytest tests/unit -v

test-integration:
	$(DEV) pytest tests/integration -v

test-agent:
	$(DEV) pytest tests/agent -v

test-security:
	$(DEV) pytest tests/security -v

test-acceptance:
	$(DEV) pytest tests/e2e -v

lint: ## ruff + bandit + secret scan
	$(DEV) bash -lc "ruff check . && ruff format --check . && bandit -q -r backend agents python_ml triad_core -x '*/tests/*' && detect-secrets scan --baseline .secrets.baseline"

scala-test: ## Compile and test the Scala jobs
	$(DEV) bash -lc "cd scala_spark && sbt -batch test"

scala-package: ## Build the thin JAR used by spark-submit
	$(DEV) bash -lc "cd scala_spark && sbt -batch jobs/package"

demo: ## Run the scripted demonstration end to end
	$(DEV) python scripts/run_demo.py

replay: ## Replay the last completed run with no LLM calls (ATC-18)
	$(DEV) python scripts/replay_run.py --latest

clean-volumes: ## DESTRUCTIVE: remove TRIAD volumes (asks first)
	@echo "This deletes triad-pgdata and triad-data. Ctrl-C to abort."; read -r _
	$(COMPOSE) --profile spark --profile tools down -v
