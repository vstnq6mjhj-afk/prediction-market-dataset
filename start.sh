#!/usr/bin/env bash
set -e

streamlit run dashboard/app.py --server.address 0.0.0.0 --server.port $PORT