from pathlib import Path
from datetime import datetime, timezone
import sys
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from utils.file_utils import save_csv
from utils.logging_utils import log_success, log_error


REQUIRED_COLUMNS = [
    "platform",
    "market_id",
    "title",
    "yes_price",
    "no_price",
    "volume",
    "liquidity",
]


def latest_snapshot():
    files = sorted((ROOT / "data" / "snapshots").glob("markets_*.csv"))
    return files[-1] if files else None


def main():
    try:
        print("Stage 41 snapshot validator started.")

        snapshot_file = latest_snapshot()

        if snapshot_file is None:
            raise ValueError("No market snapshot found.")

        df = pd.read_csv(snapshot_file)

        validation = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "snapshot_file": snapshot_file.name,
            "row_count": len(df),
            "is_empty": df.empty,
            "missing_required_columns": [],
            "null_market_ids": 0,
            "duplicate_market_ids": 0,
            "invalid_yes_prices": 0,
            "invalid_no_prices": 0,
            "valid_snapshot": True,
        }

        missing_cols = [
            col for col in REQUIRED_COLUMNS
            if col not in df.columns
        ]

        validation["missing_required_columns"] = ",".join(missing_cols)

        if missing_cols:
            validation["valid_snapshot"] = False

        if "market_id" in df.columns:
            validation["null_market_ids"] = int(df["market_id"].isna().sum())
            validation["duplicate_market_ids"] = int(
                df.duplicated(subset=["platform", "market_id"]).sum()
                if "platform" in df.columns
                else df.duplicated(subset=["market_id"]).sum()
            )

        if "yes_price" in df.columns:
            yes_price = pd.to_numeric(df["yes_price"], errors="coerce")
            validation["invalid_yes_prices"] = int(
                ((yes_price < 0) | (yes_price > 1)).sum()
            )

        if "no_price" in df.columns:
            no_price = pd.to_numeric(df["no_price"], errors="coerce")
            validation["invalid_no_prices"] = int(
                ((no_price < 0) | (no_price > 1)).sum()
            )

        if (
            validation["is_empty"]
            or validation["null_market_ids"] > 0
            or validation["duplicate_market_ids"] > 0
            or validation["invalid_yes_prices"] > 0
            or validation["invalid_no_prices"] > 0
        ):
            validation["valid_snapshot"] = False

        report = pd.DataFrame([validation])

        output_dir = ROOT / "data" / "validation"
        output_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
        output_path = output_dir / f"snapshot_validation_{timestamp}.csv"

        save_csv(report, output_path)

        log_success(f"Saved snapshot validation -> {output_path.name}")

        print(report)
        print("Stage 41 snapshot validator complete.")

    except Exception as e:
        log_error(f"Stage 41 failed: {e}")
        raise


if __name__ == "__main__":
    main()
