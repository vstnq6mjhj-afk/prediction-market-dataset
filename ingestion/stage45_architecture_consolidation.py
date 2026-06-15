from pathlib import Path
import sys
import json
import ast
import pandas as pd

from utils.bootstrap import bootstrap_project

ROOT = bootstrap_project()

from utils.config_loader import get_path, ensure_directories
from utils.logging_utils import log, log_success, log_warning, log_error
from utils.file_utils import list_csv_files, load_csv, save_csv
from utils.time_utils import utc_now_iso


# ============================================================
# STAGE 45 — ARCHITECTURE CONSOLIDATION AUDIT
# ============================================================

ensure_directories()

DATA_DIR = get_path("data")
REPORT_DIR = get_path("reports")
CONFIG_DIR = get_path("config")

LOG_FILE = "stage45_architecture_consolidation.log"


CANONICAL_SCHEMAS = {
    "market_like": [
        "market_id",
        "platform",
        "title",
        "category",
        "probability",
        "yes_price",
        "no_price",
        "volume",
        "liquidity",
    ],
    "entity_like": [
        "entity_id",
        "entity_name",
        "entity_type",
        "market_id",
        "platform",
    ],
    "cluster_like": [
        "cluster_id",
        "markets_linked",
        "venues",
        "consensus_probability",
        "cross_venue_spread",
    ],
}


DUPLICATE_LOGIC_PATTERNS = {
    "read_csv_safe": "read_csv",
    "safe_read": "safe_read",
    "normalize": "normalize",
    "clamp": "clamp",
    "num": "num",
    "infer_category": "infer_category",
    "extract_entities": "extract_entities",
    "log": "def log",
    "utc_now": "utc_now",
    "root_path": "parents[2]",
}


def infer_schema_type(columns):
    col_set = set(columns)

    scores = {}

    for schema_name, required_cols in CANONICAL_SCHEMAS.items():
        matches = len(col_set.intersection(required_cols))
        scores[schema_name] = matches

    best_schema = max(scores, key=scores.get)

    if scores[best_schema] == 0:
        return "unknown", 0

    return best_schema, scores[best_schema]


def audit_schema_files():
    rows = []

    csv_files = list_csv_files(DATA_DIR) + list_csv_files(REPORT_DIR)

    for file in csv_files:
        try:
            df = load_csv(file)
            schema_type, matches = infer_schema_type(df.columns)

            canonical_fields = CANONICAL_SCHEMAS.get(schema_type, [])
            missing_fields = [
                field for field in canonical_fields if field not in df.columns
            ]

            rows.append(
                {
                    "file": str(file),
                    "rows": len(df),
                    "columns": len(df.columns),
                    "schema_type_guess": schema_type,
                    "canonical_matches": matches,
                    "missing_canonical_fields": ", ".join(missing_fields)
                    if missing_fields
                    else "None",
                    "status": "ok" if not missing_fields else "needs_review",
                }
            )

        except Exception as e:
            rows.append(
                {
                    "file": str(file),
                    "rows": 0,
                    "columns": 0,
                    "schema_type_guess": "error",
                    "canonical_matches": 0,
                    "missing_canonical_fields": "",
                    "status": f"error:{e}",
                }
            )

    return pd.DataFrame(rows)


def audit_validation_files(schema_audit):
    rows = []

    for _, row in schema_audit.iterrows():
        file = Path(row["file"])

        if not file.exists() or row["status"].startswith("error"):
            continue

        try:
            df = load_csv(file)

            probability_issues = 0
            duplicate_market_ids = 0
            null_market_ids = 0
            null_titles = 0

            if "probability" in df.columns:
                probability_issues += int(
                    ((df["probability"] < 0) | (df["probability"] > 1)).sum()
                )

            if "yes_price" in df.columns:
                probability_issues += int(
                    ((df["yes_price"] < 0) | (df["yes_price"] > 1)).sum()
                )

            if "no_price" in df.columns:
                probability_issues += int(
                    ((df["no_price"] < 0) | (df["no_price"] > 1)).sum()
                )

            if "market_id" in df.columns:
                duplicate_market_ids = int(df["market_id"].duplicated().sum())
                null_market_ids = int(df["market_id"].isna().sum())

            if "title" in df.columns:
                null_titles = int(df["title"].isna().sum())

            valid = (
                probability_issues == 0
                and null_market_ids == 0
                and null_titles == 0
            )

            rows.append(
                {
                    "file": str(file),
                    "rows": len(df),
                    "probability_issues": probability_issues,
                    "duplicate_market_ids": duplicate_market_ids,
                    "null_market_ids": null_market_ids,
                    "null_titles": null_titles,
                    "validation_status": "pass" if valid else "review",
                }
            )

        except Exception as e:
            rows.append(
                {
                    "file": str(file),
                    "rows": 0,
                    "probability_issues": 0,
                    "duplicate_market_ids": 0,
                    "null_market_ids": 0,
                    "null_titles": 0,
                    "validation_status": f"error:{e}",
                }
            )

    return pd.DataFrame(rows)


def audit_paths():
    paths = [
        DATA_DIR,
        REPORT_DIR,
        CONFIG_DIR,
        ROOT / "logs",
        ROOT / "utils",
        ROOT / "pipelines",
        ROOT / "pipelines" / "ingestion",
        ROOT / "pipelines" / "validation",
        ROOT / "pipelines" / "analytics",
        ROOT / "pipelines" / "execution",
        ROOT / "pipelines" / "ml",
    ]

    rows = []

    for path in paths:
        rows.append(
            {
                "path": str(path),
                "exists": path.exists(),
                "csv_files": len(list(path.glob("*.csv"))) if path.exists() else 0,
                "py_files": len(list(path.glob("*.py"))) if path.exists() else 0,
                "json_files": len(list(path.glob("*.json"))) if path.exists() else 0,
            }
        )

    return pd.DataFrame(rows)


def find_python_files():
    return sorted(
        [
            file
            for file in ROOT.rglob("*.py")
            if "__pycache__" not in str(file)
        ]
    )


def extract_function_names(file_path):
    try:
        source = file_path.read_text(encoding="utf-8")
        tree = ast.parse(source)

        return [
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
        ]

    except Exception:
        return []


def audit_duplicate_logic():
    rows = []

    py_files = find_python_files()

    for logic_name, pattern in DUPLICATE_LOGIC_PATTERNS.items():
        matches = []

        for file in py_files:
            try:
                text = file.read_text(encoding="utf-8", errors="ignore")

                if pattern in text:
                    matches.append(str(file.relative_to(ROOT)))

            except Exception:
                continue

        if matches:
            rows.append(
                {
                    "logic_name": logic_name,
                    "occurrences": len(matches),
                    "files": ", ".join(matches[:20]),
                    "recommendation": "centralize_in_utils"
                    if len(matches) > 1
                    else "ok",
                }
            )

    return pd.DataFrame(rows)


def audit_function_reuse():
    function_map = {}

    for file in find_python_files():
        for fn in extract_function_names(file):
            function_map.setdefault(fn, []).append(str(file.relative_to(ROOT)))

    rows = []

    for fn, files in function_map.items():
        if len(files) > 1:
            rows.append(
                {
                    "function_name": fn,
                    "occurrences": len(files),
                    "files": ", ".join(files[:20]),
                    "recommendation": "review_for_reuse",
                }
            )

    return pd.DataFrame(rows)


def write_config_snapshot():
    rows = []

    for file in sorted(CONFIG_DIR.glob("*.json")):
        try:
            content = json.loads(file.read_text(encoding="utf-8"))

            rows.append(
                {
                    "config_file": file.name,
                    "loaded": True,
                    "top_level_keys": ", ".join(content.keys())
                    if isinstance(content, dict)
                    else "",
                }
            )

        except Exception as e:
            rows.append(
                {
                    "config_file": file.name,
                    "loaded": False,
                    "top_level_keys": f"error:{e}",
                }
            )

    return pd.DataFrame(rows)


def build_metrics(
    schema_audit,
    validation_report,
    path_audit,
    duplicate_logic,
    function_reuse,
    config_snapshot,
):
    return pd.DataFrame(
        [
            {"metric": "csv_files_reviewed", "value": len(schema_audit)},
            {
                "metric": "schema_files_needing_review",
                "value": int((schema_audit["status"] == "needs_review").sum())
                if not schema_audit.empty
                else 0,
            },
            {
                "metric": "validation_files_needing_review",
                "value": int(
                    (validation_report["validation_status"] == "review").sum()
                )
                if not validation_report.empty
                else 0,
            },
            {
                "metric": "duplicate_logic_items",
                "value": len(duplicate_logic),
            },
            {
                "metric": "duplicate_function_names",
                "value": len(function_reuse),
            },
            {
                "metric": "paths_missing",
                "value": int((path_audit["exists"] == False).sum())
                if not path_audit.empty
                else 0,
            },
            {
                "metric": "config_files_loaded",
                "value": int((config_snapshot["loaded"] == True).sum())
                if not config_snapshot.empty
                else 0,
            },
        ]
    )


def run():
    log("Stage 45 architecture consolidation started.", log_file=LOG_FILE)

    schema_audit = audit_schema_files()
    validation_report = audit_validation_files(schema_audit)
    path_audit = audit_paths()
    duplicate_logic = audit_duplicate_logic()
    function_reuse = audit_function_reuse()
    config_snapshot = write_config_snapshot()

    metrics = build_metrics(
        schema_audit,
        validation_report,
        path_audit,
        duplicate_logic,
        function_reuse,
        config_snapshot,
    )

    save_csv(schema_audit, REPORT_DIR / "stage45_schema_audit.csv")
    save_csv(validation_report, REPORT_DIR / "stage45_validation_report.csv")
    save_csv(path_audit, REPORT_DIR / "stage45_path_audit.csv")
    save_csv(duplicate_logic, REPORT_DIR / "stage45_duplicate_logic_report.csv")
    save_csv(function_reuse, REPORT_DIR / "stage45_function_reuse_report.csv")
    save_csv(config_snapshot, REPORT_DIR / "stage45_config_snapshot.csv")
    save_csv(metrics, REPORT_DIR / "stage45_consolidation_metrics.csv")

    summary = f"""Stage 45 Architecture Consolidation Summary

Run time:
- {utc_now_iso()}

Created:
- reports/stage45_schema_audit.csv
- reports/stage45_validation_report.csv
- reports/stage45_path_audit.csv
- reports/stage45_duplicate_logic_report.csv
- reports/stage45_function_reuse_report.csv
- reports/stage45_config_snapshot.csv
- reports/stage45_consolidation_metrics.csv

Metrics:
{metrics.to_string(index=False)}

Status:
- ok
"""

    summary_path = REPORT_DIR / "stage45_summary.txt"
    summary_path.write_text(summary, encoding="utf-8")

    log_success("Stage 45 architecture consolidation complete.", log_file=LOG_FILE)


if __name__ == "__main__":
    run()