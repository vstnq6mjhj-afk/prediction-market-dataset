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


def _stripe_dict(value: Any) -> dict[str, Any]:
    return billing_v2._stripe_dict(value)


def _subscription_sort_key(subscription: dict[str, Any]) -> tuple[int, int, int]:
    status = str(subscription.get("status") or "").lower()
    status_priority = STATUS_PRIORITY.get(status, -1)
    not_cancel_scheduled = 0 if bool(
        subscription.get("cancel_at_period_end")
        or subscription.get("cancel_at")
    ) else 1
    created = int(subscription.get("created") or 0)
    return status_priority, not_cancel_scheduled, created


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
    """Synchronize the logged-in account from Stripe using billing_v2's full fields.

    The selected Stripe subscription is passed to billing_v2.sync_subscription(),
    which stores stripe_subscription_id, stripe_price_id, billing_term, period
    timestamps, cancellation state, plan, status, and daily limit.
    """

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

    subscriptions = [
        _stripe_dict(item)
        for item in (getattr(listing, "data", None) or [])
    ]
    if not subscriptions:
        return RedirectResponse(
            url="/dashboard?billing_sync=no-subscription",
            status_code=303,
        )

    selected = max(subscriptions, key=_subscription_sort_key)
    selected_id = str(selected.get("id") or "").strip()
    if not selected_id:
        return _error_page("Stripe returned a subscription without an ID.")

    try:
        billing_v2.sync_subscription(selected)
        refreshed = billing_v2._get_api_key_row(email) or {}
    except Exception as exc:
        return _error_page(exc)

    stored_id = str(refreshed.get("stripe_subscription_id") or "").strip()
    if stored_id != selected_id:
        return _error_page(
            "Stripe sync completed, but Supabase did not retain "
            f"stripe_subscription_id={selected_id}. Confirm that "
            "supabase_phase16_billing.sql has been applied and inspect "
            "the Render log for the api_keys update error."
        )

    return RedirectResponse(
        url="/dashboard?billing_sync=ok",
        status_code=303,
    )
