from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from api.routes import billing_v2

router = APIRouter()

STATUS_PRIORITY = {
    "active": 70,
    "trialing": 60,
    "past_due": 50,
    "unpaid": 40,
    "paused": 30,
    "incomplete": 20,
    "canceled": 10,
    "incomplete_expired": 0,
}

_MISSING = object()


def _stripe_dict(value: Any) -> dict[str, Any]:
    return billing_v2._stripe_dict(value)


def _object_value(value: Any, key: str, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, dict):
        return value.get(key, default)
    try:
        candidate = getattr(value, key)
    except Exception:
        candidate = _MISSING
    if candidate is not _MISSING:
        return candidate
    return _stripe_dict(value).get(key, default)


def _normalize_price(value: Any) -> dict[str, Any]:
    result = _stripe_dict(value)
    for key in ("id", "unit_amount", "recurring", "active", "currency"):
        candidate = _object_value(value, key, _MISSING)
        if candidate is not _MISSING:
            result[key] = candidate
    recurring = result.get("recurring")
    if recurring is not None and not isinstance(recurring, dict):
        result["recurring"] = _stripe_dict(recurring)
    return result


def _normalize_item(value: Any) -> dict[str, Any]:
    result = _stripe_dict(value)
    for key in ("id", "current_period_start", "current_period_end"):
        candidate = _object_value(value, key, _MISSING)
        if candidate is not _MISSING:
            result[key] = candidate
    price = _object_value(value, "price", result.get("price"))
    if price is not None:
        result["price"] = _normalize_price(price)
    return result


def _normalize_subscription(value: Any) -> dict[str, Any]:
    result = _stripe_dict(value)
    for key in (
        "id",
        "customer",
        "status",
        "created",
        "cancel_at",
        "cancel_at_period_end",
        "current_period_start",
        "current_period_end",
        "metadata",
    ):
        candidate = _object_value(value, key, _MISSING)
        if candidate is not _MISSING:
            result[key] = candidate

    metadata = result.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        result["metadata"] = _stripe_dict(metadata)

    items_value = _object_value(value, "items", result.get("items"))
    item_data: list[Any] = []
    if items_value is not None:
        raw_data = _object_value(items_value, "data", _MISSING)
        if raw_data is _MISSING:
            raw_data = _stripe_dict(items_value).get("data")
        if raw_data:
            item_data = list(raw_data)
    result["items"] = {
        "data": [_normalize_item(item) for item in item_data]
    }
    return result


def _subscription_sort_key(subscription: dict[str, Any]) -> tuple[int, int, int]:
    status = str(subscription.get("status") or "").lower()
    status_priority = STATUS_PRIORITY.get(status, -1)
    cancellation_scheduled = 1 if bool(
        subscription.get("cancel_at_period_end")
        or subscription.get("cancel_at")
    ) else 0
    created = int(subscription.get("created") or 0)
    return status_priority, cancellation_scheduled, created


def _error_page(message: Any, status_code: int = 500) -> HTMLResponse:
    return HTMLResponse(
        billing_v2._billing_shell(
            "Billing sync failed",
            f"""
            <a href="/dashboard">← Dashboard</a>
            <h1>Billing sync failed</h1>
            <p>{billing_v2.escape(message)}</p>
            """,
        ),
        status_code=status_code,
    )


@router.post("/billing/sync", include_in_schema=False)
def billing_sync_full_identifiers(request: Request):
    """Synchronize the logged-in account and persist complete Stripe IDs."""

    email, redirect = billing_v2._portal_email_or_redirect(request)
    if redirect:
        return redirect

    row = billing_v2._get_api_key_row(email) or {}
    customer_id = str(row.get("stripe_customer_id") or "").strip()
    if not customer_id:
        return RedirectResponse(
            url="/dashboard?billing_sync=no-customer",
            status_code=303,
        )

    try:
        listing = billing_v2.stripe.Subscription.list(
            customer=customer_id,
            status="all",
            limit=100,
        )
    except Exception as exc:
        return _error_page(exc)

    raw_subscriptions = list(getattr(listing, "data", None) or [])
    normalized = [_normalize_subscription(item) for item in raw_subscriptions]
    if not normalized:
        return RedirectResponse(
            url="/dashboard?billing_sync=no-subscription",
            status_code=303,
        )

    selected = max(normalized, key=_subscription_sort_key)
    selected_id = str(selected.get("id") or "").strip()
    if not selected_id:
        return _error_page(
            "Stripe returned subscription data, but the SDK conversion omitted "
            "the subscription ID. Deploy the Phase 17B Stripe object fix."
        )

    # Retrieve a full copy so nested item and price identifiers are present.
    try:
        retrieved = billing_v2.stripe.Subscription.retrieve(
            selected_id,
            expand=["items.data.price"],
        )
        full_subscription = _normalize_subscription(retrieved)
    except Exception:
        full_subscription = selected

    full_subscription["id"] = selected_id
    if not str(full_subscription.get("customer") or "").strip():
        full_subscription["customer"] = customer_id

    try:
        billing_v2.sync_subscription(full_subscription)
        refreshed = billing_v2._get_api_key_row(email) or {}
    except Exception as exc:
        return _error_page(exc)

    stored_id = str(refreshed.get("stripe_subscription_id") or "").strip()
    if stored_id != selected_id:
        return _error_page(
            "Stripe sync completed, but Supabase did not retain the "
            "subscription ID. The billing columns exist, so inspect the "
            "Render log for the api_keys update error."
        )

    return RedirectResponse(
        url="/dashboard?billing_sync=ok",
        status_code=303,
    )
