from pathlib import Path
import sys
import pandas as pd

from utils.bootstrap import bootstrap_project

ROOT = bootstrap_project()

from utils.config_loader import get_path, ensure_directories
from utils.logging_utils import log, log_success, log_warning, log_error
from utils.file_utils import list_csv_files, load_csv, save_csv
from utils.time_utils import utc_now_iso


# ============================================================
# STAGE 23 — DATA QUALITY REPAIR
# ============================================================

ensure_directories()

DATA_DIR = get_path("data")
SNAPSHOT_DIR = get_path("snapshots")
NORMALIZED_DIR = get_path("normalized")
REPORT_DIR = get_path("reports")

LOG_FILE = "stage23_data_quality_repair.log"


REQUIRED_MARKET_COLUMNS = [
    "platform",
    "market_id",
    "title",
    "category",
    "status",
    "volume",
    "liquidity",
    "yes_price",
    "no_price",
]


def clean_text(value):
    if pd.isna(value):
        return ""

    return str(value).strip()


def coerce_numeric(df, columns):
    for col in columns:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df


def repair_market_dataframe(df: pd.DataFrame):
    issues = []

    original_rows = len(df)

    df = df.copy()

    df = df.dropna(how="all")

    if len(df) < original_rows:
        issues.append(f"empty_rows_removed:{original_rows - len(df)}")

    for col in REQUIRED_MARKET_COLUMNS:
        if col not in df.columns:
            df[col] = ""
            issues.append(f"missing_column_added:{col}")

    text_columns = [
        "platform",
        "market_id",
        "title",
        "category",
        "status",
    ]

    for col in text_columns:
        if col in df.columns:
            df[col] = df[col].apply(clean_text)

    numeric_columns = [
        "volume",
        "liquidity",
        "yes_price",
        "no_price",
    ]

    df = coerce_numeric(df, numeric_columns)

    if "yes_price" in df.columns:
        before = len(df)
        df = df[
            df["yes_price"].isna()
            | (
                (df["yes_price"] >= 0.0)
                & (df["yes_price"] <= 1.0)
            )
        ]
        removed = before - len(df)

        if removed > 0:
            issues.append(f"invalid_yes_price_removed:{removed}")

    if "no_price" in df.columns:
        before = len(df)
        df = df[
            df["no_price"].isna()
            | (
                (df["no_price"] >= 0.0)
                & (df["no_price"] <= 1.0)
            )
        ]
        removed = before - len(df)

        if removed > 0:
            issues.append(f"invalid_no_price_removed:{removed}")

    if "market_id" in df.columns and "platform" in df.columns:
        before = len(df)
        df = df.drop_duplicates(subset=["platform", "market_id"])
        removed = before - len(df)

        if removed > 0:
            issues.append(f"duplicates_removed:{removed}")

    if "title" in df.columns:
        before = len(df)
        df = df[df["title"].astype(str).str.strip() != ""]
        removed = before - len(df)

        if removed > 0:
            issues.append(f"empty_titles_removed:{removed}")

    final_rows = len(df)

    issues.append(f"rows_before:{original_rows}")
    issues.append(f"rows_after:{final_rows}")

    return df, issues


def choose_input_files():
    snapshot_files = list_csv_files(SNAPSHOT_DIR)

    if snapshot_files:
        return snapshot_files, SNAPSHOT_DIR

    normalized_files = list_csv_files(NORMALIZED_DIR)

    if normalized_files:
        return normalized_files, NORMALIZED_DIR

    data_files = list_csv_files(DATA_DIR)

    return data_files, DATA_DIR


def run():
    log("Stage 23 data quality repair started.", log_file=LOG_FILE)

    csv_files, source_dir = choose_input_files()

    if not csv_files:
        log_warning("No CSV files found for repair.", log_file=LOG_FILE)
        return

    log(f"Source directory: {source_dir}", log_file=LOG_FILE)

    report_rows = []

    for file in csv_files:
        try:
            log(f"Repairing {file.name}", log_file=LOG_FILE)

            df = load_csv(file)

            repaired_df, issues = repair_market_dataframe(df)

            output_file = NORMALIZED_DIR / file.name
            save_csv(repaired_df, output_file)

            report_rows.append(
                {
                    "file": file.name,
                    "source": str(source_dir),
                    "input_rows": len(df),
                    "repaired_rows": len(repaired_df),
                    "issues": "; ".join(issues),
                    "repair_time": utc_now_iso(),
                }
            )

            log_success(
                f"Saved repaired file -> {output_file.name}",
                log_file=LOG_FILE,
            )

        except Exception as e:
            report_rows.append(
                {
                    "file": file.name,
                    "source": str(source_dir),
                    "input_rows": 0,
                    "repaired_rows": 0,
                    "issues": f"error:{e}",
                    "repair_time": utc_now_iso(),
                }
            )

            log_error(
                f"Error repairing {file.name}: {e}",
                log_file=LOG_FILE,
            )

    report_df = pd.DataFrame(report_rows)

    report_path = REPORT_DIR / "stage23_data_quality_repair_report.csv"
    save_csv(report_df, report_path)

    summary = f"""Stage 23 Data Quality Repair Summary

Run time:
- {utc_now_iso()}

Source:
- {source_dir}

Created:
- reports/stage23_data_quality_repair_report.csv

Metrics:
- files_processed: {len(report_rows)}
- files_successful: {sum(1 for r in report_rows if not str(r["issues"]).startswith("error:"))}
- files_failed: {sum(1 for r in report_rows if str(r["issues"]).startswith("error:"))}

Status:
- ok
"""

    summary_path = REPORT_DIR / "stage23_summary.txt"
    summary_path.write_text(summary, encoding="utf-8")

    log_success("Stage 23 data quality repair complete.", log_file=LOG_FILE)


if __name__ == "__main__":
    run()