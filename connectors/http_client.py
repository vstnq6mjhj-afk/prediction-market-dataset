from __future__ import annotations

import os
import time
from typing import Any, Mapping, Optional

import requests

DEFAULT_TIMEOUT = max(
    int(os.getenv("CONNECTOR_REQUEST_TIMEOUT_SECONDS", "30")),
    5,
)
DEFAULT_ATTEMPTS = max(
    int(os.getenv("CONNECTOR_REQUEST_ATTEMPTS", "5")),
    1,
)
DEFAULT_BACKOFF = max(
    float(os.getenv("CONNECTOR_RETRY_BACKOFF_SECONDS", "1.0")),
    0.1,
)

RETRYABLE_STATUS_CODES = {
    408,
    425,
    429,
    500,
    502,
    503,
    504,
}


def build_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "Accept": "application/json",
            "User-Agent": (
                "PredictionMarketDataset/1.0 "
                "(https://predictionmarketdataset.com)"
            ),
        }
    )
    return session


def _retry_delay(
    response: Optional[requests.Response],
    attempt: int,
) -> float:
    if response is not None:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return max(float(retry_after), 0.0)
            except (TypeError, ValueError):
                pass

    return DEFAULT_BACKOFF * (2 ** max(attempt - 1, 0))


def get_json(
    session: requests.Session,
    url: str,
    *,
    params: Optional[Mapping[str, Any]] = None,
    timeout: Optional[int] = None,
    attempts: Optional[int] = None,
) -> Any:
    request_timeout = timeout or DEFAULT_TIMEOUT
    maximum_attempts = attempts or DEFAULT_ATTEMPTS
    last_error: Optional[Exception] = None

    for attempt in range(1, maximum_attempts + 1):
        response: Optional[requests.Response] = None

        try:
            response = session.get(
                url,
                params=dict(params or {}),
                timeout=request_timeout,
            )

            if (
                response.status_code in RETRYABLE_STATUS_CODES
                and attempt < maximum_attempts
            ):
                delay = _retry_delay(response, attempt)
                print(
                    f"[http] retryable status={response.status_code} "
                    f"attempt={attempt}/{maximum_attempts} "
                    f"sleep={delay:.1f}s url={url}",
                    flush=True,
                )
                time.sleep(delay)
                continue

            response.raise_for_status()
            return response.json()
        except (
            requests.Timeout,
            requests.ConnectionError,
            requests.HTTPError,
            ValueError,
        ) as exc:
            last_error = exc

            retryable = (
                isinstance(
                    exc,
                    (
                        requests.Timeout,
                        requests.ConnectionError,
                    ),
                )
                or (
                    response is not None
                    and response.status_code
                    in RETRYABLE_STATUS_CODES
                )
            )

            if not retryable or attempt >= maximum_attempts:
                raise

            delay = _retry_delay(response, attempt)
            print(
                f"[http] request failed attempt={attempt}/"
                f"{maximum_attempts} sleep={delay:.1f}s "
                f"url={url}: {exc}",
                flush=True,
            )
            time.sleep(delay)

    raise RuntimeError(
        f"Request failed after {maximum_attempts} attempts: "
        f"{url}: {last_error}"
    )
