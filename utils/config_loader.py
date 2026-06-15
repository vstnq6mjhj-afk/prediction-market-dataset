from pathlib import Path
import json


ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"


def load_json(filename: str, default=None):
    path = CONFIG_DIR / filename

    if not path.exists():
        if default is not None:
            return default
        raise FileNotFoundError(f"Missing config file: {path}")

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_settings():
    return load_json("settings.json", default={})


def load_thresholds():
    return load_json("thresholds.json", default={})


def load_schemas():
    return load_json("schemas.json", default={})


def get_project_root():
    return ROOT


def get_path(name: str):
    settings = load_settings()

    paths = settings.get("paths", {})

    default_paths = {
        "data": ROOT / "data",
        "raw": ROOT / "data" / "raw",
        "snapshots": ROOT / "data" / "snapshots",
        "normalized": ROOT / "data" / "normalized",
        "processed": ROOT / "data" / "processed",
        "reports": ROOT / "reports",
        "logs": ROOT / "logs",
        "config": ROOT / "config",
    }

    if name in paths:
        return ROOT / paths[name]

    if name in default_paths:
        return default_paths[name]

    raise KeyError(f"Unknown configured path: {name}")


def get_threshold(name: str, default=None):
    thresholds = load_thresholds()

    if name in thresholds:
        return thresholds[name]

    if default is not None:
        return default

    raise KeyError(f"Unknown threshold: {name}")


def get_schema(name: str, default=None):
    schemas = load_schemas()

    if name in schemas:
        return schemas[name]

    if default is not None:
        return default

    raise KeyError(f"Unknown schema: {name}")


def ensure_directories():
    for name in [
        "data",
        "raw",
        "snapshots",
        "normalized",
        "processed",
        "reports",
        "logs",
        "config",
    ]:
        get_path(name).mkdir(parents=True, exist_ok=True)


if __name__ == "__main__":
    ensure_directories()

    print("Config loader OK")
    print(f"Root: {ROOT}")
    print(f"Config: {CONFIG_DIR}")