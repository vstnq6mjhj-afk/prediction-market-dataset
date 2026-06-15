from pathlib import Path
from datetime import datetime, timezone


ROOT = Path(__file__).resolve().parents[1]

LOG_DIR = ROOT / "logs"

LOG_DIR.mkdir(parents=True, exist_ok=True)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def get_log_file(name="pipeline.log"):
    return LOG_DIR / name


def log(message, log_file="pipeline.log", print_output=True):
    timestamp = utc_now()

    line = f"{timestamp} | {message}"

    if print_output:
        print(line)

    path = get_log_file(log_file)

    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def log_error(message, log_file="errors.log"):
    log(f"ERROR: {message}", log_file=log_file)


def log_warning(message, log_file="warnings.log"):
    log(f"WARNING: {message}", log_file=log_file)


def log_success(message, log_file="pipeline.log"):
    log(f"SUCCESS: {message}", log_file=log_file)


if __name__ == "__main__":
    log("Logging utils initialized.")
    log_success("Success logger working.")
    log_warning("Warning logger working.")
    log_error("Error logger working.")