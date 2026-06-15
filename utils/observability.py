from pathlib import Path
from datetime import datetime, timezone
import json
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BASE_DIR / "data" / "observability"
DATA_DIR.mkdir(parents=True, exist_ok=True)

EVENT_LOG = DATA_DIR / "agent_events.jsonl"
DECISION_LOG = DATA_DIR / "agent_decisions.jsonl"


def append_jsonl(path, payload):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload, default=str) + "\n")


def save_payload_to_db(table_name, payload):
    cleaned = {}

    for key, value in payload.items():
        if isinstance(value, (dict, list)):
            cleaned[key] = json.dumps(value, default=str)
        else:
            cleaned[key] = value

    df = pd.DataFrame([cleaned])

    db_dir = BASE_DIR / "data" / "database"
    db_dir.mkdir(parents=True, exist_ok=True)

    output_path = db_dir / f"{table_name}.csv"

    if output_path.exists():
        try:
            existing = pd.read_csv(
                output_path,
                on_bad_lines="skip",
                engine="python",
            )
        except Exception:
            existing = pd.DataFrame()

        df = pd.concat([existing, df], ignore_index=True)

    df.to_csv(output_path, index=False)


def log_agent_event(
    run_id,
    agent_name,
    event_type,
    stage,
    records_in=0,
    records_out=0,
    message="",
    metadata=None,
    **extra,
):
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "agent_name": agent_name,
        "event_type": event_type,
        "stage": stage,
        "records_in": records_in,
        "records_out": records_out,
        "message": message,
        "metadata": metadata or {},
    }

    payload.update(extra)

    append_jsonl(EVENT_LOG, payload)
    save_payload_to_db("agent_events", payload)


def log_agent_decision(
    run_id,
    stage,
    agent_name,
    market_id=None,
    title=None,
    action=None,
    confidence=0,
    score=0,
    reason="",
    metadata=None,
    decision_type=None,
    decision=None,
    **extra,
):
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "stage": stage,
        "agent_name": agent_name,
        "market_id": market_id,
        "title": title,
        "action": action or decision_type or decision,
        "confidence": confidence,
        "score": score,
        "reason": reason,
        "metadata": metadata or {},
    }

    payload.update(extra)

    append_jsonl(DECISION_LOG, payload)
    save_payload_to_db("agent_decisions", payload)


def summarize_scores(df, score_column):
    if df is None or df.empty or score_column not in df.columns:
        return {
            "count": 0,
            "min": 0,
            "max": 0,
            "mean": 0,
        }

    scores = pd.to_numeric(
        df[score_column],
        errors="coerce",
    ).fillna(0)

    return {
        "count": int(len(scores)),
        "min": float(scores.min()),
        "max": float(scores.max()),
        "mean": float(scores.mean()),
    }