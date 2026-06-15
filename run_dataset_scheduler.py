from pathlib import Path
import subprocess
import sys
import time
import os

ROOT = Path(__file__).parent

PIPELINE = [
    "ingestion.stage39_live_market_data",
    "ingestion.stage41_snapshot_validator",
    "ingestion.stage47_schema_enforcer",
]

env = os.environ.copy()
env["PYTHONPATH"] = str(ROOT)

while True:
    print("=" * 60)
    print("Prediction Market Dataset Cycle")

    for stage in PIPELINE:
        print(f"Running {stage}")
        result = subprocess.run(
            [sys.executable, "-m", stage],
            cwd=ROOT,
            env=env
        )

        if result.returncode != 0:
            print(f"FAILED: {stage}")
            break

    print("Cycle complete")
    print("Sleeping 300 seconds")
    time.sleep(300)