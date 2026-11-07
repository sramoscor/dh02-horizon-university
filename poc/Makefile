# DH-02 · Evaluación 3 — comandos reproducibles (T-ENV-01). Requisito: Docker Engine + Compose v2.
COMPOSE ?= docker compose
SOURCE_COMMIT := $(shell git rev-parse HEAD 2>/dev/null || echo unknown)
SOURCE_DIRTY := $(shell test -z "$$(git status --porcelain 2>/dev/null)" && echo false || echo true)
TEST_RUN = $(COMPOSE) --profile test run --rm -e SOURCE_COMMIT=$(SOURCE_COMMIT) -e SOURCE_DIRTY=$(SOURCE_DIRTY) \
	-e ENV_SMOKE_RESULT -e ENV_SMOKE_LOG test

.PHONY: help env up down clean seed demo-reset test test-fast evidence env-check lint arch logs ps lock

help:
	@echo "make up | down | clean | seed | demo-reset | test | test-fast | evidence | env-check | lint | arch | logs | ps"

env:
	@test -f .env || cp .env.example .env

up: env ## compose + migraciones + seed (el servicio migrate aplica Alembic y siembra)
	$(COMPOSE) up -d --build --wait

down:
	$(COMPOSE) down

clean: ## borra contenedores y volúmenes (arranque desde cero)
	$(COMPOSE) --profile test down -v --remove-orphans

seed: ## idempotente
	$(COMPOSE) run --rm migrate python -m backend.bootstrap.cli seed

demo-reset: ## estado inicial de la demo (BD + simuladores)
	$(COMPOSE) run --rm migrate python -m backend.bootstrap.cli demo-reset
	$(COMPOSE) exec -T mocks python -c "import urllib.request as u; u.urlopen(u.Request('http://localhost:8090/_control/reset', method='POST'))"

test: env
	$(TEST_RUN) pytest

test-fast: env ## sin la carrera de 100 ensayos
	$(TEST_RUN) pytest -m "not slow"

evidence: env ## ejecuta la suite y escribe poc/evidence/<test_id>.yaml + SUMMARY.md
	$(TEST_RUN) python -m tests.evidence_runner

env-check: ## T-ENV-01: arranque desde cero + humo + evidencia completa
	./scripts/t_env_01.sh

lint: env
	$(TEST_RUN) ruff check .

arch: env ## T-ARCH-01 (import-linter)
	$(TEST_RUN) lint-imports

logs:
	$(COMPOSE) logs -f --tail=100

ps:
	$(COMPOSE) ps

lock: ## regenera las restricciones transitivas dentro de la imagen
	$(COMPOSE) run --rm --no-deps migrate pip freeze
