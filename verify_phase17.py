from __future__ import annotations

import ast
import os
import py_compile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MAIN_PATH = ROOT / "api" / "main.py"

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

EXPECTED_ROUTE_MODULES = {
    "/pricing": "api.routes.billing_v2",
    "/billing/portal": "api.routes.billing_v2",
    "/billing/sync": "api.routes.billing_sync_fix",
    "/stripe/webhook": "api.routes.billing_v2",
    "/admin/data-health": "api.routes.admin_data_health",
}

CUSTOMER_ALLOWLIST_ENV = (
    "CUSTOMER_API_PLATFORMS",
    "EXPLORER_DATA_PLATFORMS",
    "CUSTOMER_EXPORT_PLATFORMS",
    "CUSTOMER_MATCHER_PLATFORMS",
    "PUBLIC_SUMMARY_PLATFORMS",
)


def app_decorator_path(node: ast.AST) -> str | None:
    for decorator in getattr(node, "decorator_list", []):
        if not isinstance(decorator, ast.Call):
            continue
        function = decorator.func
        if not (
            isinstance(function, ast.Attribute)
            and isinstance(function.value, ast.Name)
            and function.value.id == "app"
        ):
            continue
        if not decorator.args:
            continue
        first = decorator.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return first.value
    return None


def verify_static() -> None:
    for relative in (
        "api/main.py",
        "api/routes/billing_v2.py",
        "api/routes/billing_sync_fix.py",
        "api/routes/admin_data_health.py",
    ):
        path = ROOT / relative
        py_compile.compile(str(path), doraise=True)

    source = MAIN_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    leftovers = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            path = app_decorator_path(node)
            if path in LEGACY_BILLING_PATHS:
                leftovers.append(path)
    if leftovers:
        raise AssertionError(
            "Legacy billing routes still exist in api/main.py: "
            + ", ".join(sorted(leftovers))
        )

    required_text = (
        "app.include_router(billing_v2_router)",
        "app.include_router(billing_sync_fix_router)",
        "app.include_router(admin_data_health_router)",
    )
    for text in required_text:
        if text not in source:
            raise AssertionError(f"Missing router registration: {text}")


def verify_runtime_routes() -> None:
    from api.main import app

    for path, expected_module in EXPECTED_ROUTE_MODULES.items():
        matches = [route for route in app.routes if getattr(route, "path", None) == path]
        if len(matches) != 1:
            details = [
                f"{getattr(route, 'methods', None)} "
                f"{getattr(getattr(route, 'endpoint', None), '__module__', None)}."
                f"{getattr(getattr(route, 'endpoint', None), '__name__', None)}"
                for route in matches
            ]
            raise AssertionError(
                f"Expected exactly one route for {path}; found {len(matches)}: {details}"
            )
        endpoint = matches[0].endpoint
        module = getattr(endpoint, "__module__", "")
        if module != expected_module:
            raise AssertionError(
                f"{path} is owned by {module}, expected {expected_module}."
            )
        print(
            f"PASS route {path}: "
            f"{sorted(getattr(matches[0], 'methods', set()) or set())} "
            f"{module}.{getattr(endpoint, '__name__', '')}"
        )


def verify_safety_environment() -> None:
    nonempty = {
        name: os.getenv(name)
        for name in CUSTOMER_ALLOWLIST_ENV
        if str(os.getenv(name, "")).strip()
    }
    if nonempty:
        raise AssertionError(f"Customer allowlists are not empty: {nonempty}")

    if str(os.getenv("BILLING_CHECKOUT_ENABLED", "false")).strip().lower() not in {
        "false",
        "0",
        "no",
        "off",
        "",
    }:
        raise AssertionError("BILLING_CHECKOUT_ENABLED must be false.")
    if str(os.getenv("BILLING_TEST_MODE_ONLY", "true")).strip().lower() not in {
        "true",
        "1",
        "yes",
        "on",
    }:
        raise AssertionError("BILLING_TEST_MODE_ONLY must be true.")
    if str(
        os.getenv("BILLING_REQUIRE_COMMERCIAL_SOURCES", "true")
    ).strip().lower() not in {"true", "1", "yes", "on"}:
        raise AssertionError(
            "BILLING_REQUIRE_COMMERCIAL_SOURCES must be true."
        )
    print("PASS safety environment: customer allowlists empty; checkout locked.")


def main() -> None:
    verify_static()
    print("PASS static compile and legacy-route cleanup.")
    verify_runtime_routes()
    verify_safety_environment()
    print("\nPHASE 17 VERIFICATION PASSED")


if __name__ == "__main__":
    main()
