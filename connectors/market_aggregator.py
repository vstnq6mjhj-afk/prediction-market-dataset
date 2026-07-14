from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from connectors.kalshi_connector import (
    fetch_kalshi_markets,
    get_last_fetch_diagnostics as kalshi_diagnostics,
)
from connectors.manifold_connector import (
    fetch_manifold_markets,
    get_last_fetch_diagnostics as manifold_diagnostics,
)
from connectors.metaculus_connector import fetch_metaculus_questions
from connectors.polymarket_connector import (
    fetch_polymarket_markets,
    get_last_fetch_diagnostics as polymarket_diagnostics,
)
from connectors.predictit_connector import fetch_predictit_markets
from connectors.title_utils import normalize_title

DIAGNOSTICS_DIR = Path(
    os.getenv(
        "CONNECTOR_DIAGNOSTICS_DIR",
        "/var/data",
    )
)


def _write_diagnostics(
    mode: str,
    diagnostics: dict[str, Any],
) -> None:
    try:
        DIAGNOSTICS_DIR.mkdir(parents=True, exist_ok=True)
        destination = (
            DIAGNOSTICS_DIR
            / f"connector_diagnostics_{mode}.json"
        )
        temporary = destination.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(
                diagnostics,
                indent=2,
                sort_keys=True,
                default=str,
            ),
            encoding="utf-8",
        )
        temporary.replace(destination)
    except Exception as exc:
        print(
            f"[aggregator:{mode}] diagnostics write failed: "
            f"{exc}",
            flush=True,
        )


def aggregate_markets(
    mode: str | None = None,
) -> list[dict[str, Any]]:
    refresh_mode = (
        mode
        or os.getenv("MARKET_REFRESH_MODE", "fast")
    ).strip().lower()
    if refresh_mode not in {"fast", "discovery"}:
        raise ValueError(
            "MARKET_REFRESH_MODE must be 'fast' or 'discovery'."
        )

    started_at = datetime.now(timezone.utc)
    connector_specs: list[
        tuple[
            str,
            Callable[..., list[dict[str, Any]]],
            bool,
            Optional[Callable[[], dict[str, Any]]],
        ]
    ] = [
        (
            "kalshi",
            fetch_kalshi_markets,
            True,
            kalshi_diagnostics,
        ),
        (
            "polymarket",
            fetch_polymarket_markets,
            True,
            polymarket_diagnostics,
        ),
        (
            "manifold",
            fetch_manifold_markets,
            True,
            manifold_diagnostics,
        ),
        (
            "metaculus",
            fetch_metaculus_questions,
            False,
            None,
        ),
        (
            "predictit",
            fetch_predictit_markets,
            False,
            None,
        ),
    ]

    rows_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    connector_results: dict[str, Any] = {}

    for (
        name,
        function,
        accepts_mode,
        diagnostic_getter,
    ) in connector_specs:
        connector_started = time.monotonic()

        try:
            data = (
                function(mode=refresh_mode)
                if accepts_mode
                else function()
            )
            error = None
        except Exception as exc:
            data = []
            error = str(exc)
            print(
                f"[aggregator:{refresh_mode}] "
                f"{name} failed: {exc}",
                flush=True,
            )

        accepted = 0
        for row in data:
            platform = str(
                row.get("platform") or name
            ).strip().lower()
            market_id = str(
                row.get("market_id") or ""
            ).strip()
            if not platform or not market_id:
                continue

            title = row.get("title") or ""
            row["platform"] = platform
            row["market_id"] = market_id
            row["canonical_title"] = normalize_title(title)

            source = str(row.get("source") or name)
            if not source.endswith(f":{refresh_mode}"):
                row["source"] = f"{source}:{refresh_mode}"

            rows_by_key[(platform, market_id)] = row
            accepted += 1

        elapsed = time.monotonic() - connector_started
        pagination = (
            diagnostic_getter()
            if diagnostic_getter is not None
            else {}
        )

        connector_results[name] = {
            "returned_rows": len(data),
            "accepted_rows": accepted,
            "elapsed_seconds": round(elapsed, 3),
            "error": error,
            "pagination": pagination,
        }

        completion = pagination.get("complete")
        termination = pagination.get("termination_reason")
        print(
            f"[aggregator:{refresh_mode}] {name}: "
            f"returned={len(data):,} accepted={accepted:,} "
            f"elapsed={elapsed:.1f}s "
            f"complete={completion} "
            f"termination={termination}",
            flush=True,
        )

    rows = list(rows_by_key.values())
    completed_at = datetime.now(timezone.utc)

    diagnostics = {
        "mode": refresh_mode,
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "elapsed_seconds": round(
            (completed_at - started_at).total_seconds(),
            3,
        ),
        "total_unique_rows": len(rows),
        "connectors": connector_results,
    }
    _write_diagnostics(refresh_mode, diagnostics)

    print(
        f"[aggregator:{refresh_mode}] complete: "
        f"{len(rows):,} unique rows",
        flush=True,
    )
    return rows
