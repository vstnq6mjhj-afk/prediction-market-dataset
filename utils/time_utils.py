from datetime import datetime, timezone
import time


def utc_now():
    return datetime.now(timezone.utc)


def utc_now_iso():
    return utc_now().isoformat()


def timestamp_for_filename():
    return utc_now().strftime("%Y-%m-%dT%H-%M-%SZ")


def sleep_seconds(seconds):
    time.sleep(seconds)


def sleep_minutes(minutes):
    time.sleep(minutes * 60)


if __name__ == "__main__":
    print("UTC NOW:", utc_now())
    print("ISO:", utc_now_iso())
    print("FILENAME TS:", timestamp_for_filename())