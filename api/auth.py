from datetime import datetime, timezone

from fastapi import Header, HTTPException

from api.plans import get_plan_limit
from api.supabase_client import supabase


def verify_api_key(authorization: str | None = Header(default=None)):
    if authorization is None:
        raise HTTPException(status_code=401, detail="Missing Authorization header")

    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid Authorization header")

    api_key = authorization.replace("Bearer ", "").strip()

    result = (
        supabase.table("api_keys")
        .select("*")
        .eq("api_key", api_key)
        .eq("active", True)
        .limit(1)
        .execute()
    )

    if not result.data:
        raise HTTPException(status_code=401, detail="Invalid API key")

    account = result.data[0]
    daily_limit = get_plan_limit(account.get("plan", "free"))

    today_start = datetime.now(timezone.utc).date().isoformat()

    usage_result = (
        supabase.table("api_usage")
        .select("id", count="exact")
        .eq("api_key", api_key)
        .gte("created_at", today_start)
        .execute()
    )

    requests_today = usage_result.count or 0

    if daily_limit is not None and requests_today >= daily_limit:
        raise HTTPException(status_code=429, detail="Daily API request limit exceeded")

    account["api_key"] = api_key
    account["tier"] = account.get("plan", "free")
    account["daily_limit"] = daily_limit
    account["requests_today"] = requests_today
    account["remaining"] = None if daily_limit is None else daily_limit - requests_today

    return account