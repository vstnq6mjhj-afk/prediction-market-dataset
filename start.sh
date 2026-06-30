#!/usr/bin/env bash

python run_dataset_scheduler.py &

streamlit run dashboard/app.py --server.address 0.0.0.0 --server.port $PORT