from __future__ import annotations

import ast
import py_compile
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MAIN_PATH = ROOT / "api" / "main.py"
ADMIN_ROUTE_PATH = ROOT / "api" / "routes" / "admin_data_health.py"
SYNC_ROUTE_PATH = ROOT / "api" / "routes" / "billing_sync_fix.py"
BACKUP_PATH = ROOT / "api" / "main.py.before_phase17"

LEGACY_BILLING_PATHS = {
    "/pricing",
    "/billing/portal",
    "/billing/sync",
    "/billing/debug",
    "/billing/checkout/developer",
    "/billing/checkout/professional",
    "/billing/success",
    "/stripe/webhook",
}

ADMIN_IMPORT = (
    "from api.routes.admin_data_health import "
    "router as admin_data_health_router"
)
SYNC_IMPORT = (
    "from api.routes.billing_sync_fix import "
    "router as billing_sync_fix_router"
)
ADMIN_INCLUDE = "app.include_router(admin_data_health_router)"
SYNC_INCLUDE = "app.include_router(billing_sync_fix_router)"
BILLING_IMPORT = (
    "from api.routes.billing_v2 import router as billing_v2_router"
)
BILLING_INCLUDE = "app.include_router(billing_v2_router)"
EXPLORER_IMPORT = "from api.routes.explorer import router as explorer_router"


def decorated_app_path(node: ast.AST) -> str | None:
    decorators = getattr(node, "decorator_list", [])
    for decorator in decorators:
        if not isinstance(decorator, ast.Call):
            continue
        function = decorator.func
        if not (
            isinstance(function, ast.Attribute)
            and isinstance(function.value, ast.Name)
            and function.value.id == "app"
            and function.attr in {
                "get",
                "post",
                "put",
                "patch",
                "delete",
                "api_route",
            }
        ):
            continue
        if not decorator.args:
            continue
        first = decorator.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return first.value
    return None


def remove_legacy_billing_routes(source: str) -> tuple[str, list[str]]:
    tree = ast.parse(source)
    line_ranges: list[tuple[int, int, str]] = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        path = decorated_app_path(node)
        if path in LEGACY_BILLING_PATHS:
            if node.end_lineno is None:
                raise RuntimeError(f"Python did not provide end_lineno for {path}")
            decorator_lines = [
                decorator.lineno
                for decorator in node.decorator_list
                if getattr(decorator, "lineno", None) is not None
            ]
            start_line = min([node.lineno, *decorator_lines])
            line_ranges.append((start_line, node.end_lineno, path))

    lines = source.splitlines(keepends=True)
    removed: list[str] = []
    for start, end, path in sorted(line_ranges, reverse=True):
        del lines[start - 1 : end]
        removed.append(path)
    return "".join(lines), sorted(removed)


def add_imports(source: str) -> str:
    additions = []
    if ADMIN_IMPORT not in source:
        additions.append(ADMIN_IMPORT)
    if SYNC_IMPORT not in source:
        additions.append(SYNC_IMPORT)
    if not additions:
        return source

    anchor = BILLING_IMPORT if BILLING_IMPORT in source else EXPLORER_IMPORT
    if anchor not in source:
        raise RuntimeError(
            "Could not find the billing_v2 or explorer router import anchor in api/main.py."
        )
    return source.replace(
        anchor,
        anchor + "\n" + "\n".join(additions),
        1,
    )


def add_router_includes(source: str) -> str:
    additions = []
    if SYNC_INCLUDE not in source:
        additions.append(SYNC_INCLUDE)
    if ADMIN_INCLUDE not in source:
        additions.append(ADMIN_INCLUDE)
    if not additions:
        return source

    if BILLING_INCLUDE not in source:
        raise RuntimeError(
            "Could not find app.include_router(billing_v2_router) in api/main.py."
        )
    return source.replace(
        BILLING_INCLUDE,
        BILLING_INCLUDE + "\n" + "\n".join(additions),
        1,
    )


def remaining_legacy_paths(source: str) -> list[str]:
    tree = ast.parse(source)
    paths = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            path = decorated_app_path(node)
            if path in LEGACY_BILLING_PATHS:
                paths.append(path)
    return sorted(paths)


def main() -> None:
    for path in (MAIN_PATH, ADMIN_ROUTE_PATH, SYNC_ROUTE_PATH):
        if not path.exists():
            raise SystemExit(f"Required file not found: {path}")

    original = MAIN_PATH.read_text(encoding="utf-8")
    if not BACKUP_PATH.exists():
        shutil.copy2(MAIN_PATH, BACKUP_PATH)

    updated, removed = remove_legacy_billing_routes(original)
    updated = add_imports(updated)
    updated = add_router_includes(updated)

    leftovers = remaining_legacy_paths(updated)
    if leftovers:
        raise RuntimeError(
            "Legacy billing routes remain in api/main.py: " + ", ".join(leftovers)
        )

    MAIN_PATH.write_text(updated, encoding="utf-8")

    for path in (MAIN_PATH, ADMIN_ROUTE_PATH, SYNC_ROUTE_PATH):
        py_compile.compile(str(path), doraise=True)

    print("Phase 17 patch applied successfully.")
    print(f"Backup: {BACKUP_PATH}")
    if removed:
        print("Removed legacy main.py routes:")
        for path in removed:
            print(f"  - {path}")
    else:
        print("No legacy billing routes were present; router registration was verified.")
    print("Added/verified routers:")
    print("  - billing_v2_router")
    print("  - billing_sync_fix_router")
    print("  - admin_data_health_router")


if __name__ == "__main__":
    main()
