from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PATTERNS = {
    "mkdir": "mkdir(",
    "read_csv": "pd.read_csv",
    "to_csv": ".to_csv(",
    "datetime_now": "datetime.now",
    "manual_logging": "print(",
    "root_path": "parents[2]",
}

EXCLUDED = {
    "migration_audit.py",
    "__pycache__",
}

def scan():
    results = {}

    for py_file in ROOT.rglob("*.py"):
        if any(x in str(py_file) for x in EXCLUDED):
            continue

        try:
            content = py_file.read_text(encoding="utf-8")
        except Exception:
            continue

        matches = []

        for name, pattern in PATTERNS.items():
            if pattern in content:
                matches.append(name)

        if matches:
            results[str(py_file.relative_to(ROOT))] = matches

    return results


def main():
    results = scan()

    print("\n=== MIGRATION AUDIT ===\n")

    for file, matches in sorted(results.items()):
        print(f"{file}")
        for match in matches:
            print(f"  - {match}")
        print()


if __name__ == "__main__":
    main()