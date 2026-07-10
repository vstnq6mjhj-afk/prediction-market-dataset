#!/usr/bin/env bash
set -e

python - <<'PY' &
from run_dataset_scheduler import main
main()
PY

uvicorn api.main:app --host 0.0.0.0 --port $PORT