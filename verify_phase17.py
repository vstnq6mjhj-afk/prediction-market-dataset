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

    for text in (
        "app.include_router(billing_v2_router)",
        "app.include_router(billing_sync_fix_router)",
        "app.include_router(admin_data_health_router)",
    ):
        if text not in source:
            raise AssertionError(f"Missing router registration: {text}")

    if "attribute_fields" not in source or '"id"' not in source:
        raise AssertionError(
            "api.main.stripe_object_to_dict does not contain the Phase 17B "
            "identifier-preservation fix."
        )


def _verify_router_route(router, path: str, method: str, module: str) -> None:
    matches = [
        route
        for route in router.routes
        if getattr(route, "path", None) == path
        and method in (getattr(route, "methods", set()) or set())
    ]
    if len(matches) != 1:
        raise AssertionError(
            f"Expected one {method} route for {path}; found {len(matches)}."
        )
    endpoint = matches[0].endpoint
    owner = getattr(endpoint, "__module__", "")
    if owner != module:
        raise AssertionError(f"{path} is owned by {owner}, expected {module}.")
    print(
        f"PASS router {method} {path}: "
        f"{owner}.{getattr(endpoint, '__name__', '')}"
    )


def verify_router_definitions() -> None:
    from api.routes import admin_data_health, billing_sync_fix, billing_v2

    _verify_router_route(
        billing_v2.router,
        "/pricing",
        "GET",
        "api.routes.billing_v2",
    )
    _verify_router_route(
        billing_v2.router,
        "/billing/portal",
        "POST",
        "api.routes.billing_v2",
    )
    _verify_router_route(
        billing_sync_fix.router,
        "/billing/sync",
        "POST",
        "api.routes.billing_sync_fix",
    )
    _verify_router_route(
        billing_v2.router,
        "/stripe/webhook",
        "POST",
        "api.routes.billing_v2",
    )
    _verify_router_route(
        admin_data_health.router,
        "/admin/data-health",
        "GET",
        "api.routes.admin_data_health",
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
    print("PASS static compile, route registration, and Stripe-ID fix.")
    verify_router_definitions()
    verify_safety_environment()
    print("\nPHASE 17B VERIFICATION PASSED")


if __name__ == "__main__":
    main()
