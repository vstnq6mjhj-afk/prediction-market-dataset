from __future__ import annotations

import json
import os
from pathlib import Path


DIAGNOSTICS_DIR = Path(
    os.getenv(
        "CONNECTOR_DIAGNOSTICS_DIR",
        "/var/data",
    )
)


def main() -> None:
    for mode in ("fast", "discovery"):
        path = (
            DIAGNOSTICS_DIR
            / f"connector_diagnostics_{mode}.json"
        )
        print(f"\n{mode.upper()} CONNECTOR DIAGNOSTICS")

        if not path.exists():
            print(f"No diagnostics file yet: {path}")
            continue

        payload = json.loads(
            path.read_text(encoding="utf-8")
        )
        print(
            "completed_at:",
            payload.get("completed_at"),
        )
        print(
            "total_unique_rows:",
            payload.get("total_unique_rows"),
        )
        print(
            "elapsed_seconds:",
            payload.get("elapsed_seconds"),
        )

        for name, values in (
            payload.get("connectors") or {}
        ).items():
            print(
                name,
                "returned=",
                values.get("returned_rows"),
                "accepted=",
                values.get("accepted_rows"),
                "elapsed=",
                values.get("elapsed_seconds"),
                "error=",
                values.get("error"),
            )


if __name__ == "__main__":
    main()
