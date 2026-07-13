from __future__ import annotations

import subprocess
import sys


def main() -> None:
    commands = [
        [sys.executable, "collect_kalshi_normalized.py"],
        [sys.executable, "build_semantics_separate_db.py"],
    ]

    for command in commands:
        print(f"[kalshi-pipeline] Running: {' '.join(command)}")
        subprocess.run(command, check=True)

    print("[kalshi-pipeline] Complete")


if __name__ == "__main__":
    main()
