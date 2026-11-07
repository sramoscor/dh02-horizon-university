#!/usr/bin/env bash
# T-ENV-01 — arranque desde cero con datos semilla (borra volúmenes), humo contra los contenedores reales y
# registro de evidencia. Uso: make env-check   (requiere Docker Engine + Compose v2; sin rutas locales).
set -uo pipefail
cd "$(dirname "$0")/.."
mkdir -p evidence/raw
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
LOG="evidence/raw/t-env-01-${STAMP}.log"
RESULT=FAILED
{
  echo "== T-ENV-01 ${STAMP} · commit $(git rev-parse HEAD 2>/dev/null || echo unknown)"
  echo "== docker: $(docker --version) · $(docker compose version)"
  echo "== 1. make clean (borra contenedores y volúmenes)" && make clean &&
  echo "== 2. make up (build + migraciones + seed + health checks)" && time make up &&
  echo "== 3. estado de los servicios" && docker compose ps &&
  echo "== 4. seed idempotente" && make seed &&
  echo "== 5. humo extremo a extremo" && docker compose --profile test run --rm test python -m tests.smoke
} 2>&1 | tee "$LOG"
[ "${PIPESTATUS[0]}" -eq 0 ] && RESULT=PASSED
echo "== resultado del arranque desde cero: ${RESULT}" | tee -a "$LOG"
ENV_SMOKE_RESULT=$RESULT ENV_SMOKE_LOG=$LOG make evidence
