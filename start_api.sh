#!/usr/bin/env bash
set -Eeuo pipefail

export DB_PATH="${DB_PATH:-/var/data/warehouse.duckdb}"
export SEMANTICS_DB_PATH="${SEMANTICS_DB_PATH:-/var/data/market_semantics.duckdb}"
export KALSHI_NORMALIZED_DB_PATH="${KALSHI_NORMALIZED_DB_PATH:-/var/data/kalshi_normalized.duckdb}"
export REFRESH_STATUS_DB_PATH="${REFRESH_STATUS_DB_PATH:-/var/data/refresh_status.sqlite3}"
export DUCKDB_CONNECT_ATTEMPTS="${DUCKDB_CONNECT_ATTEMPTS:-30}"
export DUCKDB_CONNECT_RETRY_SECONDS="${DUCKDB_CONNECT_RETRY_SECONDS:-0.25}"
export DATASET_REFRESH_SECONDS="${DATASET_REFRESH_SECONDS:-300}"
export SEMANTIC_REFRESH_SECONDS="${SEMANTIC_REFRESH_SECONDS:-21600}"
export RUN_SEMANTICS_ON_START="${RUN_SEMANTICS_ON_START:-false}"
export DISCOVERY_REFRESH_SECONDS="${DISCOVERY_REFRESH_SECONDS:-21600}"
export RUN_DISCOVERY_ON_START="${RUN_DISCOVERY_ON_START:-false}"
export DISCOVERY_SNAPSHOT_DIR="${DISCOVERY_SNAPSHOT_DIR:-/var/data/discovery_snapshots}"
export CONNECTOR_DIAGNOSTICS_DIR="${CONNECTOR_DIAGNOSTICS_DIR:-/var/data}"

mkdir -p /var/data

scheduler_supervisor() {
  set +e

  while true; do
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) | Starting dataset scheduler."
    python -u run_dataset_scheduler.py
    status=$?

    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) | Dataset scheduler exited with status ${status}; restarting in 30 seconds."
    sleep 30
  done
}

scheduler_supervisor &
SCHEDULER_SUPERVISOR_PID=$!

cleanup() {
  kill "${SCHEDULER_SUPERVISOR_PID}" 2>/dev/null || true
  wait "${SCHEDULER_SUPERVISOR_PID}" 2>/dev/null || true
}

trap cleanup EXIT TERM INT

# Keep the public API as the service-critical foreground process.
# A collector or scheduler failure is logged and retried, but cannot take
# the website offline.
uvicorn api.main:app \
  --host 0.0.0.0 \
  --port "${PORT}"
