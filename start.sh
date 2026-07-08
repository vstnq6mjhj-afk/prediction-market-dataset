#!/usr/bin/env bash
set -e

streamlit run dashboard/app.py --server.port $PORT --server.address 0.0.0.0