PLANS = {
    "free": {
        "daily_limit": 100,
    },
    "developer": {
        "daily_limit": 10_000,
    },
    "pro": {
        "daily_limit": 100_000,
    },
    "enterprise": {
        "daily_limit": None,
    },
}


def get_plan_limit(plan: str):
    plan_data = PLANS.get(plan, PLANS["free"])
    return plan_data["daily_limit"]