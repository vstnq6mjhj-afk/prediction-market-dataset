#!/usr/bin/env bash
set -Eeuo pipefail

export DB_PATH="${DB_PATH:-/var/data/warehouse.duckdb}"
export SEMANTICS_DB_PATH="${SEMANTICS_DB_PATH:-/var/data/market_semantics.duckdb}"
export KALSHI_NORMALIZED_DB_PATH="${KALSHI_NORMALIZED_DB_PATH:-/var/data/kalshi_normalized.duckdb}"
export DATASET_REFRESH_SECONDS="${DATASET_REFRESH_SECONDS:-300}"
export SEMANTIC_REFRESH_SECONDS="${SEMANTIC_REFRESH_SECONDS:-3600}"
export RUN_SEMANTICS_ON_START="${RUN_SEMANTICS_ON_START:-true}"

mkdir -p /var/data

python -u run_dataset_scheduler.py &
SCHEDULER_PID=$!

uvicorn api.main:app \
  --host 0.0.0.0 \
  --port "${PORT}" &
API_PID=$!

cleanup() {
  kill "${SCHEDULER_PID}" 2>/dev/null || true
  kill "${API_PID}" 2>/dev/null || true
  wait "${SCHEDULER_PID}" 2>/dev/null || true
  wait "${API_PID}" 2>/dev/null || true
}

trap cleanup EXIT TERM INT

while true; do
  if ! kill -0 "${API_PID}" 2>/dev/null; then
    wait "${API_PID}" || API_STATUS=$?
    echo "API process exited unexpectedly."
    exit "${API_STATUS:-1}"
  fi

  if ! kill -0 "${SCHEDULER_PID}" 2>/dev/null; then
    wait "${SCHEDULER_PID}" || SCHEDULER_STATUS=$?
    echo "Dataset scheduler exited unexpectedly."
    exit "${SCHEDULER_STATUS:-1}"
  fi

  sleep 5
done
