from pathlib import Path
import sys

from utils.bootstrap import bootstrap_project

ROOT = bootstrap_project()

import pandas as pd

from utils.config_loader import get_path, ensure_directories
from utils.logging_utils import log, log_success, log_warning, log_error
from utils.file_utils import list_csv_files, load_csv, save_csv


# ============================================================
# STAGE 40 — DATA QUALITY FILTER
# ============================================================

ensure_directories()

RAW_DIR = get_path("snapshots")
NORMALIZED_DIR = get_path("normalized")
REPORT_DIR = get_path("reports")

LOG_FILE = "stage40_quality_filter.log"


def validate_dataframe(df: pd.DataFrame):
    issues = []

    original_count = len(df)

    df = df.dropna(how="all")

    before_dupes = len(df)

    if "market_id" in df.columns and "platform" in df.columns:
        df = df.drop_duplicates(subset=["market_id", "platform"])
    else:
        df = df.drop_duplicates()

    duplicates_removed = before_dupes - len(df)

    if duplicates_removed > 0:
        issues.append(f"duplicates_removed:{duplicates_removed}")

    if "title" in df.columns:
        before_titles = len(df)
        df = df[df["title"].astype(str).str.strip() != ""]
        empty_titles_removed = before_titles - len(df)

        if empty_titles_removed > 0:
            issues.append(f"empty_titles_removed:{empty_titles_removed}")

    numeric_columns = [
        "volume",
        "liquidity",
        "yes_price",
        "no_price",
    ]

    for col in numeric_columns:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    if "yes_price" in df.columns:
        before_yes = len(df)

        df = df[
            df["yes_price"].isna()
            | (
                (df["yes_price"] >= 0.0)
                & (df["yes_price"] <= 1.0)
            )
        ]

        invalid_yes_removed = before_yes - len(df)

        if invalid_yes_removed > 0:
            issues.append(f"invalid_yes_price_removed:{invalid_yes_removed}")

    if "no_price" in df.columns:
        before_no = len(df)

        df = df[
            df["no_price"].isna()
            | (
                (df["no_price"] >= 0.0)
                & (df["no_price"] <= 1.0)
            )
        ]

        invalid_no_removed = before_no - len(df)

        if invalid_no_removed > 0:
            issues.append(f"invalid_no_price_removed:{invalid_no_removed}")

    final_count = len(df)

    issues.append(f"rows_before:{original_count}")
    issues.append(f"rows_after:{final_count}")

    return df, issues


def run():
    log("Stage 40 quality filter started.", log_file=LOG_FILE)

    csv_files = list_csv_files(RAW_DIR)

    if not csv_files:
        log_warning("No raw CSV files found.", log_file=LOG_FILE)
        return

    report_rows = []

    for file in csv_files:
        try:
            log(f"Processing {file.name}", log_file=LOG_FILE)

            df = load_csv(file)

            clean_df, issues = validate_dataframe(df)

            output_file = NORMALIZED_DIR / file.name
            save_csv(clean_df, output_file)

            report_rows.append(
                {
                    "file": file.name,
                    "input_rows": len(df),
                    "clean_rows": len(clean_df),
                    "issues": "; ".join(issues),
                }
            )

            log_success(
                f"Saved cleaned file -> {output_file.name}",
                log_file=LOG_FILE,
            )

        except Exception as e:
            log_error(
                f"Error processing {file.name}: {e}",
                log_file=LOG_FILE,
            )

    report_df = pd.DataFrame(report_rows)

    report_path = REPORT_DIR / "stage40_quality_report.csv"
    save_csv(report_df, report_path)

    log_success("Stage 40 quality filter complete.", log_file=LOG_FILE)


if __name__ == "__main__":
    run()