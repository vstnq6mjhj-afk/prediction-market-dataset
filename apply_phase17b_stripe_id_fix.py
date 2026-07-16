from __future__ import annotations

import ast
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MAIN_PATH = ROOT / "api" / "main.py"
BILLING_PATH = ROOT / "api" / "routes" / "billing_v2.py"

MAIN_REPLACEMENT = '''def stripe_object_to_dict(value):
    """Convert Stripe objects to plain dictionaries while preserving IDs.

    Some Stripe SDK objects expose important fields such as ``id`` as
    attributes even when ``to_dict_recursive()`` omits them.  Billing sync
    must never lose subscription or price identifiers during conversion.
    """
    if value is None:
        return {}

    source = value
    if isinstance(value, dict):
        result = dict(value)
    else:
        result = {}
        try:
            converted = value.to_dict_recursive()
            if isinstance(converted, dict):
                result = dict(converted)
        except Exception:
            try:
                converted = dict(value)
                if isinstance(converted, dict):
                    result = dict(converted)
            except Exception:
                result = {}

    attribute_fields = (
        "id",
        "object",
        "customer",
        "status",
        "created",
        "cancel_at",
        "cancel_at_period_end",
        "current_period_start",
        "current_period_end",
        "metadata",
        "items",
        "price",
        "recurring",
        "unit_amount",
        "subscription",
    )
    for key in attribute_fields:
        if key in result and result[key] is not None:
            continue
        try:
            candidate = getattr(source, key)
        except Exception:
            candidate = None
        if candidate is not None:
            result[key] = candidate

    return result
'''

BILLING_REPLACEMENT = '''def _stripe_dict(value: Any) -> dict[str, Any]:
    """Convert Stripe objects to dictionaries without dropping identifiers."""
    if value is None:
        return {}

    source = value
    if isinstance(value, dict):
        result: dict[str, Any] = dict(value)
    else:
        result = {}
        try:
            converted = value.to_dict_recursive()
            if isinstance(converted, dict):
                result = dict(converted)
        except Exception:
            try:
                converted = dict(value)
                if isinstance(converted, dict):
                    result = dict(converted)
            except Exception:
                result = {}

    attribute_fields = (
        "id",
        "object",
        "customer",
        "status",
        "created",
        "cancel_at",
        "cancel_at_period_end",
        "current_period_start",
        "current_period_end",
        "metadata",
        "items",
        "price",
        "recurring",
        "unit_amount",
        "subscription",
    )
    for key in attribute_fields:
        if key in result and result[key] is not None:
            continue
        try:
            candidate = getattr(source, key)
        except Exception:
            candidate = None
        if candidate is not None:
            result[key] = candidate

    return result
'''


def replace_top_level_function(path: Path, function_name: str, replacement: str) -> None:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    target = None
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name:
            target = node
            break
    if target is None or target.end_lineno is None:
        raise RuntimeError(f"Could not find top-level function {function_name!r} in {path}")

    lines = source.splitlines(keepends=True)
    start = target.lineno - 1
    end = target.end_lineno
    replacement_text = replacement.rstrip() + "\n"
    updated = "".join(lines[:start]) + replacement_text + "".join(lines[end:])
    ast.parse(updated)
    path.write_text(updated, encoding="utf-8", newline="\n")


def backup(path: Path) -> Path:
    backup_path = path.with_name(path.name + ".before_phase17b")
    if not backup_path.exists():
        shutil.copy2(path, backup_path)
    return backup_path


def main() -> None:
    for path in (MAIN_PATH, BILLING_PATH):
        if not path.exists():
            raise SystemExit(f"Missing required file: {path}")

    main_backup = backup(MAIN_PATH)
    billing_backup = backup(BILLING_PATH)

    replace_top_level_function(
        MAIN_PATH,
        "stripe_object_to_dict",
        MAIN_REPLACEMENT,
    )
    replace_top_level_function(
        BILLING_PATH,
        "_stripe_dict",
        BILLING_REPLACEMENT,
    )

    print("Phase 17B Stripe identifier fix applied successfully.")
    print(f"Backup: {main_backup}")
    print(f"Backup: {billing_backup}")
    print("Replaced:")
    print("  - api.main.stripe_object_to_dict")
    print("  - api.routes.billing_v2._stripe_dict")


if __name__ == "__main__":
    main()
