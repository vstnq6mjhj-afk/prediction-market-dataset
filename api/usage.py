from api.supabase_client import supabase


def log_api_request(
    api_key: str,
    endpoint: str,
    status_code: int = 200,
    rows_returned: int = 0,
):
    supabase.table("api_usage").insert({
        "api_key": api_key,
        "endpoint": endpoint,
        "status_code": status_code,
        "rows_returned": rows_returned,
    }).execute()