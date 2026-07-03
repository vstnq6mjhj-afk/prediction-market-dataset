import os
from fastapi import Header, HTTPException

API_KEYS = {
    os.getenv("API_KEY", "pmd_demo_key"): {
        "tier": "pro",
        "active": True,
    }
}


def verify_api_key(
    authorization: str | None = Header(default=None)
):
    if authorization is None:
        raise HTTPException(
            status_code=401,
            detail="Missing Authorization header"
        )

    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Invalid Authorization header"
        )

    api_key = authorization.replace("Bearer ", "")

    if api_key not in API_KEYS:
        raise HTTPException(
            status_code=401,
            detail="Invalid API key"
        )

    account = API_KEYS[api_key]

    if not account["active"]:
        raise HTTPException(
            status_code=403,
            detail="API key disabled"
        )

    return account