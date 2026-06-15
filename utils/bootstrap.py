from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]


def bootstrap_project():
    root = Path(__file__).resolve().parents[1]

    if str(root) not in sys.path:
        sys.path.append(str(root))

    return root