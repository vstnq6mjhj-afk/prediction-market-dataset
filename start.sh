#!/usr/bin/env bash
set -e

python - <<'PY' &
from run_dataset_scheduler import main
main()
PY

streamlit run dashboard/app.py --server.address 0.0.0.0 --server.port $PORT
