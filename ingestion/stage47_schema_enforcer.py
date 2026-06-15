from pathlib import Path
from datetime import datetime, timezone
import sys
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from utils.file_utils import save_csv
from utils.logging_utils import log_success, log_error


SNAPSHOT_DIR = ROOT / "data" / "snapshots"
VALIDATION_DIR = ROOT / "data" / "validation"


REQUIRED_COLUMNS = [
    "platform",
    "market_id",
    "title",
    "yes_price",
    "no_price",
]


def latest_snapshot():
    files = sorted(SNAPSHOT_DIR.glob("markets_*.csv"))
    return files[-1] if files else None


def main():
    try:
        print("Stage 47 schema enforcer started.")

        snapshot = latest_snapshot()

        if snapshot is None:
            raise ValueError("No snapshot found.")

        df = pd.read_csv(snapshot)

        missing_columns = [
            col for col in REQUIRED_COLUMNS
            if col not in df.columns
        ]

        null_counts = {
            col: int(df[col].isnull().sum())
            for col in REQUIRED_COLUMNS
            if col in df.columns
        }

        duplicate_market_ids = (
            int(df["market_id"].duplicated().sum())
            if "market_id" in df.columns
            else -1
        )

        schema_valid = (
            len(missing_columns) == 0
            and duplicate_market_ids == 0
        )

        report = pd.DataFrame([
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "snapshot_file": snapshot.name,
                "missing_columns": ",".join(missing_columns),
                "duplicate_market_ids": duplicate_market_ids,
                "schema_valid": schema_valid,
                **null_counts
            }
        ])

        output_dir = VALIDATION_DIR
        output_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")

        output_path = (
            output_dir /
            f"schema_validation_{timestamp}.csv"
        )

        save_csv(report, output_path)

        log_success(
            f"Saved schema validation -> {output_path.name}"
        )

        print(report)
        print("Stage 47 schema enforcer complete.")

    except Exception as e:
        log_error(f"Stage 47 failed: {e}")
        raise


if __name__ == "__main__":
    main()
