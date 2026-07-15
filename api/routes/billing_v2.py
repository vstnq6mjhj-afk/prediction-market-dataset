from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import os
import time
from dataclasses import dataclass
from typing import Any, Optional

import stripe
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from api.source_policy import PolicyContext, allowed_platforms
from api.supabase_client import supabase


router = APIRouter()

APP_BASE_URL = os.getenv(
    "APP_BASE_URL",
    "https://predictionmarketdataset.com",
).rstrip("/")
APP_SECRET_KEY = (
    os.getenv("APP_SECRET_KEY")
    or os.getenv("STRIPE_SECRET_KEY")
    or "dev-change-me"
)
SESSION_COOKIE = "pmd_session"
SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 14

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
stripe.api_key = STRIPE_SECRET_KEY


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return int(default)
    value = int(str(raw).strip())
    if value < 1:
        raise ValueError(f"{name} must be greater than zero.")
    return value


@dataclass(frozen=True)
class BillingTerm:
    slug: str
    label: str
    env_label: str
    interval: str
    interval_count: int
    months: int
    renewal_text: str


TERMS: dict[str, BillingTerm] = {
    "monthly": BillingTerm(
        slug="monthly",
        label="Monthly",
        env_label="MONTHLY",
        interval="month",
        interval_count=1,
        months=1,
        renewal_text="Renews every month",
    ),
    "3-month": BillingTerm(
        slug="3-month",
        label="3 months",
        env_label="THREE_MONTH",
        interval="month",
        interval_count=3,
        months=3,
        renewal_text="Renews every 3 months",
    ),
    "6-month": BillingTerm(
        slug="6-month",
        label="6 months",
        env_label="SIX_MONTH",
        interval="month",
        interval_count=6,
        months=6,
        renewal_text="Renews every 6 months",
    ),
    "annual": BillingTerm(
        slug="annual",
        label="Annual",
        env_label="ANNUAL",
        interval="year",
        interval_count=1,
        months=12,
        renewal_text="Renews every year",
    ),
}

# PHASE16A_MONTHLY_ONLY
def visible_terms() -> tuple[str, ...]:
    raw = os.getenv("BILLING_VISIBLE_TERMS", "monthly")
    selected: list[str] = []
    unknown: list[str] = []

    for item in str(raw).split(","):
        slug = item.strip().lower()
        if not slug:
            continue
        if slug not in TERMS:
            unknown.append(slug)
            continue
        if slug not in selected:
            selected.append(slug)

    if unknown:
        raise ValueError(
            "BILLING_VISIBLE_TERMS contains unknown term(s): "
            + ", ".join(sorted(set(unknown)))
        )

    if not selected:
        return ("monthly",)

    return tuple(selected)


PLAN_CONFIG: dict[str, dict[str, Any]] = {
    "developer": {
        "name": "Prediction Market Dataset Developer",
        "display_name": "Developer",
        "monthly_amount_pence": 1900,
        "daily_limit": 1_000,
        "description": (
            "For individual developers, testing, prototypes, "
            "and small research projects."
        ),
        "features": (
            "Prediction market REST API",
            "Dataset Explorer access",
            "Market search and latest snapshots",
            "Historical market detail",
            "1,000 API requests per day",
        ),
    },
    "professional": {
        "name": "Prediction Market Dataset Professional",
        "display_name": "Professional",
        "monthly_amount_pence": 4900,
        "daily_limit": 10_000,
        "description": (
            "For researchers, data teams, and production "
            "applications."
        ),
        "features": (
            "Everything in Developer",
            "Higher-volume API access",
            "Advanced explorer workflows",
            "Matcher access when commercially enabled",
            "10,000 API requests per day",
        ),
    },
}


def normalize_email(value: Any) -> str:
    return str(value or "").strip().lower()


def escape(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def read_session_email(request: Request) -> Optional[str]:
    token = request.cookies.get(SESSION_COOKIE)
    if not token or "." not in token:
        return None

    encoded, signature = token.rsplit(".", 1)
    expected = hmac.new(
        APP_SECRET_KEY.encode(),
        encoded.encode(),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(signature, expected):
        return None

    try:
        payload = json.loads(
            base64.urlsafe_b64decode(encoded.encode()).decode()
        )
    except Exception:
        return None

    issued_at = int(payload.get("iat", 0))
    if time.time() - issued_at > SESSION_MAX_AGE_SECONDS:
        return None

    return normalize_email(payload.get("email"))


def _portal_email_or_redirect(
    request: Request,
) -> tuple[Optional[str], Optional[RedirectResponse]]:
    email = read_session_email(request)
    if not email:
        return None, RedirectResponse(
            url="/login",
            status_code=303,
        )
    return email, None


def _stripe_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    try:
        return value.to_dict_recursive()
    except Exception:
        try:
            return dict(value)
        except Exception:
            return {}


def _first_subscription_item(
    subscription: dict[str, Any],
) -> dict[str, Any]:
    items = _stripe_dict(subscription.get("items"))
    data = items.get("data") or []
    if not data:
        return {}
    return _stripe_dict(data[0])


def amount_pence(plan: str, term: str) -> int:
    plan_config = PLAN_CONFIG[plan]
    term_config = TERMS[term]
    default = (
        int(plan_config["monthly_amount_pence"])
        * term_config.months
    )
    variable = (
        f"STRIPE_{plan.upper()}_"
        f"{term_config.env_label}_AMOUNT_PENCE"
    )
    return _env_int(variable, default)


def price_id(plan: str, term: str) -> str:
    term_config = TERMS[term]
    variable = (
        f"STRIPE_{plan.upper()}_"
        f"{term_config.env_label}_PRICE_ID"
    )
    return str(os.getenv(variable, "")).strip()


def format_gbp(pence: int) -> str:
    pounds = int(pence) / 100
    if pounds.is_integer():
        return f"£{int(pounds):,}"
    return f"£{pounds:,.2f}"


def effective_monthly_pence(plan: str, term: str) -> int:
    months = TERMS[term].months
    return round(amount_pence(plan, term) / months)


def saving_percent(plan: str, term: str) -> int:
    base = (
        int(PLAN_CONFIG[plan]["monthly_amount_pence"])
        * TERMS[term].months
    )
    actual = amount_pence(plan, term)
    if actual >= base:
        return 0
    return round((1 - actual / base) * 100)


def infer_billing_term(subscription: Any) -> str:
    subscription_dict = _stripe_dict(subscription)
    metadata = _stripe_dict(
        subscription_dict.get("metadata")
    )
    metadata_term = str(
        metadata.get("billing_term") or ""
    ).strip().lower()
    if metadata_term in TERMS:
        return metadata_term

    item = _first_subscription_item(subscription_dict)
    price = _stripe_dict(item.get("price"))
    recurring = _stripe_dict(price.get("recurring"))
    interval = str(recurring.get("interval") or "")
    count = int(recurring.get("interval_count") or 1)

    for slug, term in TERMS.items():
        if (
            term.interval == interval
            and term.interval_count == count
        ):
            return slug

    if interval == "month" and count == 12:
        return "annual"
    return "monthly"


def infer_plan(subscription: Any) -> str:
    subscription_dict = _stripe_dict(subscription)
    metadata = _stripe_dict(
        subscription_dict.get("metadata")
    )
    metadata_plan = str(
        metadata.get("plan") or ""
    ).strip().lower()
    if metadata_plan in PLAN_CONFIG:
        return metadata_plan

    item = _first_subscription_item(subscription_dict)
    price = _stripe_dict(item.get("price"))
    configured_price_id = str(price.get("id") or "")

    if configured_price_id:
        for plan in PLAN_CONFIG:
            for term in TERMS:
                if price_id(plan, term) == configured_price_id:
                    return plan

    amount = int(price.get("unit_amount") or 0)
    term = infer_billing_term(subscription_dict)
    months = TERMS[term].months
    effective = round(amount / max(months, 1))

    if effective >= 4900:
        return "professional"
    return "developer"


def _period_end(subscription: dict[str, Any]) -> Any:
    if subscription.get("current_period_end"):
        return subscription.get("current_period_end")
    item = _first_subscription_item(subscription)
    return item.get("current_period_end")


def _period_start(subscription: dict[str, Any]) -> Any:
    if subscription.get("current_period_start"):
        return subscription.get("current_period_start")
    item = _first_subscription_item(subscription)
    return item.get("current_period_start")


def _timestamp_to_iso(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    try:
        return time.strftime(
            "%Y-%m-%dT%H:%M:%SZ",
            time.gmtime(int(value)),
        )
    except Exception:
        return None


def _get_api_key_row(email: str) -> Optional[dict[str, Any]]:
    result = (
        supabase.table("api_keys")
        .select("*")
        .eq("email", normalize_email(email))
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def _get_api_key_row_by_customer(
    customer_id: str,
) -> Optional[dict[str, Any]]:
    result = (
        supabase.table("api_keys")
        .select("*")
        .eq("stripe_customer_id", customer_id)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def _safe_api_key_update(
    updates: dict[str, Any],
    *,
    column: str,
    value: str,
) -> None:
    try:
        (
            supabase.table("api_keys")
            .update(updates)
            .eq(column, value)
            .execute()
        )
        return
    except Exception:
        optional_columns = {
            "stripe_subscription_id",
            "stripe_price_id",
            "billing_term",
            "current_period_start",
            "current_period_end",
            "cancel_at_period_end",
        }
        fallback = {
            key: item
            for key, item in updates.items()
            if key not in optional_columns
        }
        if fallback:
            (
                supabase.table("api_keys")
                .update(fallback)
                .eq(column, value)
                .execute()
            )


def _stored_status(stripe_status: str) -> tuple[str, int]:
    normalized = str(stripe_status or "").strip().lower()
    if normalized in {"active", "trialing"}:
        return "active", -1
    if normalized in {
        "past_due",
        "unpaid",
        "paused",
        "incomplete",
    }:
        return normalized, 100
    if normalized in {
        "canceled",
        "incomplete_expired",
    }:
        return "canceled", 100
    return normalized or "free", 100


def sync_subscription(subscription: Any) -> None:
    subscription_dict = _stripe_dict(subscription)
    metadata = _stripe_dict(
        subscription_dict.get("metadata")
    )

    plan = infer_plan(subscription_dict)
    term = infer_billing_term(subscription_dict)
    stripe_status = str(
        subscription_dict.get("status") or "free"
    ).lower()
    stored_status, forced_limit = _stored_status(
        stripe_status
    )

    email = normalize_email(metadata.get("email"))
    customer_id = str(
        subscription_dict.get("customer") or ""
    ).strip()
    subscription_id = str(
        subscription_dict.get("id") or ""
    ).strip()

    if not email and customer_id:
        existing = _get_api_key_row_by_customer(
            customer_id
        )
        email = normalize_email(
            (existing or {}).get("email")
        )

    if not email:
        return

    item = _first_subscription_item(subscription_dict)
    price = _stripe_dict(item.get("price"))

    if forced_limit == -1:
        daily_limit = int(
            PLAN_CONFIG[plan]["daily_limit"]
        )
        stored_plan = plan
    else:
        daily_limit = forced_limit
        stored_plan = (
            plan
            if stored_status not in {"canceled", "free"}
            else "free"
        )

    updates: dict[str, Any] = {
        "plan": stored_plan,
        "subscription_status": stored_status,
        "daily_limit": daily_limit,
        "billing_term": term,
        "stripe_subscription_id": subscription_id or None,
        "stripe_price_id": str(price.get("id") or "") or None,
        "current_period_start": _timestamp_to_iso(
            _period_start(subscription_dict)
        ),
        "current_period_end": _timestamp_to_iso(
            _period_end(subscription_dict)
            or subscription_dict.get("cancel_at")
        ),
        "cancel_at_period_end": bool(
            subscription_dict.get(
                "cancel_at_period_end"
            )
            or subscription_dict.get("cancel_at")
        ),
    }
    if customer_id:
        updates["stripe_customer_id"] = customer_id

    _safe_api_key_update(
        updates,
        column="email",
        value=email,
    )


def mark_customer_status(
    customer_id: str,
    status: str,
) -> None:
    customer_id = str(customer_id or "").strip()
    if not customer_id:
        return

    normalized, forced_limit = _stored_status(status)
    updates: dict[str, Any] = {
        "subscription_status": normalized,
        "daily_limit": 100 if forced_limit == -1 else forced_limit,
    }
    if normalized == "canceled":
        updates.update(
            {
                "plan": "free",
                "billing_term": None,
                "cancel_at_period_end": False,
                "current_period_start": None,
                "current_period_end": None,
            }
        )

    _safe_api_key_update(
        updates,
        column="stripe_customer_id",
        value=customer_id,
    )


def checkout_readiness() -> tuple[bool, str]:
    if not _env_bool(
        "BILLING_CHECKOUT_ENABLED",
        False,
    ):
        return (
            False,
            "Checkout is disabled while billing is being tested.",
        )

    if not STRIPE_SECRET_KEY:
        return False, "Stripe is not configured."

    if (
        _env_bool("BILLING_TEST_MODE_ONLY", True)
        and STRIPE_SECRET_KEY.startswith("sk_live_")
    ):
        return (
            False,
            "Live Stripe checkout is blocked by "
            "BILLING_TEST_MODE_ONLY.",
        )

    if _env_bool(
        "BILLING_REQUIRE_COMMERCIAL_SOURCES",
        True,
    ) and not allowed_platforms(
        PolicyContext.CUSTOMER_API
    ):
        return (
            False,
            "Checkout is unavailable until at least one "
            "commercial data source is enabled.",
        )

    return True, ""


def _billing_shell(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)} | Prediction Market Dataset</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #07111d;
      --panel: rgba(17, 31, 50, .88);
      --panel-2: rgba(24, 42, 66, .88);
      --text: #f8f3b8;
      --muted: #b8c8d9;
      --cyan: #36e5df;
      --green: #17ef78;
      --border: rgba(90, 221, 230, .32);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      background:
        radial-gradient(circle at 16% 10%, rgba(25, 178, 151, .15), transparent 35%),
        radial-gradient(circle at 88% 0%, rgba(66, 88, 160, .18), transparent 34%),
        var(--bg);
      color: var(--text);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    a {{ color: var(--cyan); text-decoration: none; }}
    .wrap {{ width: min(1120px, 92vw); margin: 0 auto; padding: 42px 0 70px; }}
    .eyebrow {{ color: var(--cyan); font-weight: 800; letter-spacing: .02em; }}
    h1 {{ font-size: clamp(2.3rem, 6vw, 4.4rem); margin: .35rem 0 .6rem; line-height: 1; }}
    .lead {{ max-width: 780px; color: var(--muted); font-size: 1.05rem; line-height: 1.65; }}
    .notice {{
      margin: 24px 0;
      border: 1px solid var(--border);
      background: rgba(12, 28, 45, .88);
      border-radius: 18px;
      padding: 16px 18px;
      color: var(--muted);
    }}
    .tabs {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin: 30px 0 22px;
    }}
    .tab {{
      border: 1px solid var(--border);
      background: rgba(17, 31, 50, .9);
      color: var(--text);
      padding: 12px 18px;
      border-radius: 999px;
      cursor: pointer;
      font-weight: 800;
    }}
    .tab.active {{
      background: var(--green);
      color: #03140b;
      border-color: var(--green);
      box-shadow: 0 0 24px rgba(23, 239, 120, .25);
    }}
    .term-panel {{ display: none; }}
    .term-panel.active {{ display: block; }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 20px;
    }}
    .card {{
      border: 1px solid var(--border);
      background: linear-gradient(145deg, var(--panel), var(--panel-2));
      border-radius: 22px;
      padding: 26px;
      box-shadow: 0 18px 55px rgba(0, 0, 0, .18);
    }}
    .card h2 {{ margin: 0 0 7px; font-size: 1.6rem; }}
    .price {{ font-size: 2.35rem; font-weight: 900; margin: 18px 0 2px; }}
    .equivalent {{ color: var(--muted); min-height: 1.5em; }}
    .saving {{
      display: inline-block;
      margin-top: 10px;
      color: #03140b;
      background: var(--green);
      border-radius: 999px;
      padding: 5px 10px;
      font-weight: 900;
      font-size: .84rem;
    }}
    ul {{ color: var(--muted); line-height: 1.8; padding-left: 1.25rem; }}
    .checkout {{
      width: 100%;
      margin-top: 10px;
      padding: 14px 18px;
      border: 0;
      border-radius: 12px;
      font-weight: 900;
      background: var(--green);
      color: #03140b;
      cursor: pointer;
    }}
    .checkout:disabled {{
      cursor: not-allowed;
      opacity: .45;
    }}
    .terms {{
      margin-top: 25px;
      border: 1px solid var(--border);
      background: rgba(11, 25, 42, .84);
      border-radius: 22px;
      padding: 24px;
    }}
    .small {{ color: var(--muted); font-size: .92rem; line-height: 1.65; }}
    @media (max-width: 760px) {{
      .grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <main class="wrap">
    {body}
  </main>
</body>
</html>"""


def _plan_card(
    plan: str,
    term: str,
    *,
    checkout_enabled: bool,
) -> str:
    config = PLAN_CONFIG[plan]
    term_config = TERMS[term]
    total = amount_pence(plan, term)
    effective = effective_monthly_pence(plan, term)
    saving = saving_percent(plan, term)

    feature_items = "".join(
        f"<li>{escape(item)}</li>"
        for item in config["features"]
    )
    saving_badge = (
        f'<span class="saving">Save {saving}%</span>'
        if saving
        else ""
    )

    return f"""
    <article class="card">
      <h2>{escape(config["display_name"])}</h2>
      <p class="small">{escape(config["description"])}</p>
      <div class="price">{escape(format_gbp(total))}</div>
      <div class="equivalent">
        Charged every {escape(term_config.label.lower())}
        · {escape(format_gbp(effective))}/month equivalent
      </div>
      {saving_badge}
      <ul>{feature_items}</ul>
      <form method="post"
            action="/billing/checkout/{escape(plan)}/{escape(term)}">
        <button class="checkout"
                type="submit"
                {"disabled" if not checkout_enabled else ""}>
          Choose {escape(config["display_name"])}
          · {escape(term_config.label)}
        </button>
      </form>
    </article>
    """


@router.get(
    "/pricing",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def pricing_page_v2(
    request: Request,
    term: str = "monthly",
):
    email, redirect = _portal_email_or_redirect(request)
    if redirect:
        return redirect

    enabled_terms = visible_terms()
    selected_term = (
        term if term in enabled_terms else enabled_terms[0]
    )
    ready, message = checkout_readiness()
    mode = (
        "Stripe test mode"
        if STRIPE_SECRET_KEY.startswith("sk_test_")
        else "Stripe live mode"
        if STRIPE_SECRET_KEY.startswith("sk_live_")
        else "Stripe not configured"
    )

    tabs = []
    panels = []
    for slug in enabled_terms:
        term_config = TERMS[slug]
        active = " active" if slug == selected_term else ""
        tabs.append(
            f'<button class="tab{active}" '
            f'data-term="{escape(slug)}" type="button">'
            f'{escape(term_config.label)}</button>'
        )
        cards = "".join(
            _plan_card(
                plan,
                slug,
                checkout_enabled=ready,
            )
            for plan in PLAN_CONFIG
        )
        panels.append(
            f'<section class="term-panel{active}" '
            f'id="term-{escape(slug)}">'
            f'<div class="grid">{cards}</div>'
            f"</section>"
        )

    notice = (
        f'<div class="notice"><strong>{escape(mode)}.</strong> '
        f'{escape(message)}</div>'
        if message
        else (
            f'<div class="notice"><strong>{escape(mode)}.</strong> '
            "Checkout is available for this account.</div>"
        )
    )

    body = f"""
    <a href="/dashboard">← Back to Dashboard</a>
    <div class="eyebrow">Plans & billing</div>
    <h1>Choose a subscription term</h1>
    <p class="lead">
      Monthly subscriptions are available during the initial
      launch. Longer commitment terms remain disabled until the
      platform has established stable paid usage and the terms
      have completed legal and operational review.
    </p>
    {notice}
    <div class="tabs" role="tablist">
      {''.join(tabs)}
    </div>
    {''.join(panels)}
    <section class="terms">
      <h2>Subscription terms</h2>
      <ul>
        <li>The full selected term is charged at checkout.</li>
        <li>The subscription renews for the same term until cancelled.</li>
        <li>Cancellation takes effect at the end of the paid billing period.</li>
        <li>Plan limits and data-source availability depend on the active subscription and commercial licensing status.</li>
        <li>Payments are processed by Stripe. Production checkout remains disabled until commercial data access is ready.</li>
      </ul>
      <p class="small">
        Logged in as {escape(email)}.
        See the <a href="/terms">Terms of Service</a>
        and <a href="/privacy">Privacy Policy</a>.
      </p>
    </section>
    <script>
      const tabs = document.querySelectorAll(".tab");
      const panels = document.querySelectorAll(".term-panel");
      tabs.forEach((tab) => {{
        tab.addEventListener("click", () => {{
          const selected = tab.dataset.term;
          tabs.forEach((item) => item.classList.remove("active"));
          panels.forEach((item) => item.classList.remove("active"));
          tab.classList.add("active");
          document.getElementById(`term-${{selected}}`).classList.add("active");
          const url = new URL(window.location.href);
          url.searchParams.set("term", selected);
          window.history.replaceState({{}}, "", url);
        }});
      }});
    </script>
    """
    return HTMLResponse(
        _billing_shell("Plans & Billing", body)
    )


def _checkout_line_item(
    plan: str,
    term: str,
) -> dict[str, Any]:
    configured_price = price_id(plan, term)
    if configured_price:
        return {
            "price": configured_price,
            "quantity": 1,
        }

    config = PLAN_CONFIG[plan]
    term_config = TERMS[term]
    return {
        "price_data": {
            "currency": "gbp",
            "unit_amount": amount_pence(plan, term),
            "recurring": {
                "interval": term_config.interval,
                "interval_count": (
                    term_config.interval_count
                ),
            },
            "product_data": {
                "name": config["name"],
                "description": config["description"],
                "metadata": {
                    "pmd_plan": plan,
                },
            },
        },
        "quantity": 1,
    }


@router.post(
    "/billing/checkout/{plan}/{term}",
    include_in_schema=False,
)
def create_checkout_v2(
    plan: str,
    term: str,
    request: Request,
):
    email, redirect = _portal_email_or_redirect(request)
    if redirect:
        return redirect

    plan = str(plan).strip().lower()
    term = str(term).strip().lower()
    if plan not in PLAN_CONFIG or term not in TERMS:
        raise HTTPException(
            status_code=404,
            detail="Unknown billing plan or term.",
        )

    if term not in visible_terms():
        raise HTTPException(
            status_code=404,
            detail="This billing term is not currently available.",
        )

    ready, message = checkout_readiness()
    if not ready:
        return HTMLResponse(
            _billing_shell(
                "Checkout unavailable",
                f"""
                <a href="/pricing?term={escape(term)}">← Back to pricing</a>
                <h1>Checkout unavailable</h1>
                <div class="notice">{escape(message)}</div>
                """,
            ),
            status_code=503,
        )

    row = _get_api_key_row(email) or {}
    customer_id = str(
        row.get("stripe_customer_id") or ""
    ).strip()

    metadata = {
        "email": email,
        "plan": plan,
        "billing_term": term,
    }

    create_arguments: dict[str, Any] = {
        "mode": "subscription",
        "client_reference_id": email,
        "line_items": [
            _checkout_line_item(plan, term)
        ],
        "success_url": (
            f"{APP_BASE_URL}/billing/success"
            "?session_id={CHECKOUT_SESSION_ID}"
        ),
        "cancel_url": (
            f"{APP_BASE_URL}/pricing?term={term}"
        ),
        "metadata": metadata,
        "subscription_data": {
            "metadata": metadata,
        },
        "allow_promotion_codes": _env_bool(
            "STRIPE_ALLOW_PROMOTION_CODES",
            False,
        ),
    }

    if _env_bool(
        "STRIPE_AUTOMATIC_TAX_ENABLED",
        False,
    ):
        create_arguments["automatic_tax"] = {
            "enabled": True,
        }

    if customer_id:
        create_arguments["customer"] = customer_id
    else:
        create_arguments["customer_email"] = email

    try:
        session = stripe.checkout.Session.create(
            **create_arguments
        )
    except Exception as exc:
        return HTMLResponse(
            _billing_shell(
                "Stripe checkout failed",
                f"""
                <a href="/pricing?term={escape(term)}">← Back to pricing</a>
                <h1>Stripe checkout failed</h1>
                <div class="notice">{escape(exc)}</div>
                """,
            ),
            status_code=500,
        )

    return RedirectResponse(
        str(session.url),
        status_code=303,
    )


@router.post(
    "/billing/checkout/{plan}",
    include_in_schema=False,
)
def legacy_monthly_checkout_v2(
    plan: str,
    request: Request,
):
    return create_checkout_v2(
        plan=plan,
        term="monthly",
        request=request,
    )


@router.get(
    "/billing/success",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def billing_success_v2(
    request: Request,
    session_id: str = "",
):
    email, redirect = _portal_email_or_redirect(request)
    if redirect:
        return redirect

    if not session_id:
        return RedirectResponse(
            url="/dashboard",
            status_code=303,
        )

    try:
        session = stripe.checkout.Session.retrieve(
            session_id,
            expand=["subscription"],
        )
        session_dict = _stripe_dict(session)
    except Exception as exc:
        return HTMLResponse(
            _billing_shell(
                "Billing confirmation pending",
                f"""
                <h1>Billing confirmation pending</h1>
                <div class="notice">{escape(exc)}</div>
                <p><a href="/dashboard">Return to dashboard</a></p>
                """,
            ),
            status_code=202,
        )

    details = _stripe_dict(
        session_dict.get("customer_details")
    )
    session_email = normalize_email(
        details.get("email")
        or session_dict.get("customer_email")
        or _stripe_dict(
            session_dict.get("metadata")
        ).get("email")
    )

    if session_email and session_email != email:
        raise HTTPException(
            status_code=403,
            detail="Checkout session does not belong to this account.",
        )

    subscription = session_dict.get("subscription")
    if isinstance(subscription, str):
        try:
            subscription = stripe.Subscription.retrieve(
                subscription
            )
        except Exception:
            subscription = None

    if subscription:
        sync_subscription(subscription)

    payment_status = str(
        session_dict.get("payment_status") or "pending"
    )
    body = f"""
    <a href="/dashboard">← Customer Dashboard</a>
    <h1>Checkout received</h1>
    <div class="notice">
      Stripe payment status: {escape(payment_status)}.
      Subscription access is granted only after Stripe confirms
      an active or trialing subscription through the signed webhook.
    </div>
    <p><a href="/dashboard">Open dashboard</a></p>
    """
    return HTMLResponse(
        _billing_shell("Checkout received", body)
    )


@router.post(
    "/billing/portal",
    include_in_schema=False,
)
def billing_portal_v2(request: Request):
    email, redirect = _portal_email_or_redirect(request)
    if redirect:
        return redirect

    row = _get_api_key_row(email) or {}
    customer_id = str(
        row.get("stripe_customer_id") or ""
    ).strip()
    if not customer_id:
        return RedirectResponse(
            url="/pricing",
            status_code=303,
        )

    try:
        session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=f"{APP_BASE_URL}/dashboard",
        )
    except Exception as exc:
        return HTMLResponse(
            _billing_shell(
                "Billing portal failed",
                f"""
                <a href="/dashboard">← Dashboard</a>
                <h1>Billing portal failed</h1>
                <div class="notice">{escape(exc)}</div>
                """,
            ),
            status_code=500,
        )

    return RedirectResponse(
        str(session.url),
        status_code=303,
    )


def _webhook_event_row(
    event_id: str,
) -> Optional[dict[str, Any]]:
    result = (
        supabase.table("stripe_webhook_events")
        .select("*")
        .eq("event_id", event_id)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def _claim_webhook_event(
    event_id: str,
    event_type: str,
) -> bool:
    existing = _webhook_event_row(event_id)
    if existing and existing.get("status") == "processed":
        return False

    if existing:
        attempts = int(
            existing.get("attempt_count") or 0
        ) + 1
        (
            supabase.table("stripe_webhook_events")
            .update(
                {
                    "status": "processing",
                    "error_message": None,
                    "attempt_count": attempts,
                    "updated_at": (
                        time.strftime(
                            "%Y-%m-%dT%H:%M:%SZ",
                            time.gmtime(),
                        )
                    ),
                }
            )
            .eq("event_id", event_id)
            .execute()
        )
        return True

    (
        supabase.table("stripe_webhook_events")
        .insert(
            {
                "event_id": event_id,
                "event_type": event_type,
                "status": "processing",
                "attempt_count": 1,
            }
        )
        .execute()
    )
    return True


def _finish_webhook_event(
    event_id: str,
    *,
    status: str,
    error_message: Optional[str] = None,
) -> None:
    (
        supabase.table("stripe_webhook_events")
        .update(
            {
                "status": status,
                "error_message": error_message,
                "processed_at": (
                    time.strftime(
                        "%Y-%m-%dT%H:%M:%SZ",
                        time.gmtime(),
                    )
                    if status == "processed"
                    else None
                ),
                "updated_at": time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ",
                    time.gmtime(),
                ),
            }
        )
        .eq("event_id", event_id)
        .execute()
    )


def _retrieve_subscription(
    subscription_value: Any,
) -> Optional[Any]:
    if not subscription_value:
        return None
    if isinstance(subscription_value, str):
        return stripe.Subscription.retrieve(
            subscription_value
        )
    return subscription_value


def _process_stripe_event(
    event_type: str,
    data_object: dict[str, Any],
) -> None:
    if event_type == "checkout.session.completed":
        subscription = _retrieve_subscription(
            data_object.get("subscription")
        )
        if subscription:
            sync_subscription(subscription)
        return

    if event_type in {
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.resumed",
        "customer.subscription.paused",
    }:
        sync_subscription(data_object)
        return

    if event_type == "customer.subscription.deleted":
        sync_subscription(data_object)
        mark_customer_status(
            str(data_object.get("customer") or ""),
            "canceled",
        )
        return

    if event_type == "invoice.paid":
        subscription = _retrieve_subscription(
            data_object.get("subscription")
        )
        if subscription:
            sync_subscription(subscription)
        return

    if event_type == "invoice.payment_failed":
        mark_customer_status(
            str(data_object.get("customer") or ""),
            "past_due",
        )
        return


@router.post(
    "/stripe/webhook",
    include_in_schema=False,
)
async def stripe_webhook_v2(request: Request):
    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(
            status_code=500,
            detail="Stripe webhook secret is not configured.",
        )

    payload = await request.body()
    signature = request.headers.get(
        "stripe-signature"
    )
    try:
        event = stripe.Webhook.construct_event(
            payload,
            signature,
            STRIPE_WEBHOOK_SECRET,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="Invalid Stripe payload.",
        ) from exc
    except stripe.error.SignatureVerificationError as exc:
        raise HTTPException(
            status_code=400,
            detail="Invalid Stripe signature.",
        ) from exc

    event_dict = _stripe_dict(event)
    event_id = str(event_dict.get("id") or "").strip()
    event_type = str(
        event_dict.get("type") or ""
    ).strip()
    if not event_id:
        raise HTTPException(
            status_code=400,
            detail="Stripe event is missing an ID.",
        )

    try:
        should_process = _claim_webhook_event(
            event_id,
            event_type,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "Could not claim Stripe webhook event. "
                "Run the Phase 16 Supabase migration."
            ),
        ) from exc

    if not should_process:
        return {
            "received": True,
            "duplicate": True,
        }

    data = _stripe_dict(event_dict.get("data"))
    data_object = _stripe_dict(data.get("object"))

    try:
        _process_stripe_event(
            event_type,
            data_object,
        )
        _finish_webhook_event(
            event_id,
            status="processed",
        )
    except Exception as exc:
        try:
            _finish_webhook_event(
                event_id,
                status="failed",
                error_message=str(exc)[:1500],
            )
        finally:
            raise HTTPException(
                status_code=500,
                detail="Stripe webhook processing failed.",
            ) from exc

    return {
        "received": True,
        "duplicate": False,
    }
