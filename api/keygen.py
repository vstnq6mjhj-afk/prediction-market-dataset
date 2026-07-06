import secrets


def generate_api_key() -> str:
    return "pmd_live_" + secrets.token_urlsafe(32)