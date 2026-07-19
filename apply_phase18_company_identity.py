from __future__ import annotations

from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parent
MAIN = ROOT / "api" / "main.py"
MARKER = "PHASE18_COMPANY_IDENTITY"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Expected exactly one {label}; found {count}")
    return text.replace(old, new, 1)


def main() -> None:
    text = MAIN.read_text(encoding="utf-8")
    if MARKER in text:
        print("Phase 18 company identity patch already present.")
        return

    backup = MAIN.with_suffix(".py.before_phase18")
    if not backup.exists():
        shutil.copy2(MAIN, backup)

    config_anchor = (
        'DATASET_EXPLORER_URL = os.getenv('
        '"DATASET_EXPLORER_URL", '
        '"https://prediction-market-dataset.onrender.com")\n'
    )
    config = config_anchor + """

# PHASE18_COMPANY_IDENTITY
COMPANY_LEGAL_NAME = os.getenv(
    "COMPANY_LEGAL_NAME", "PMD Data Systems Ltd"
).strip()
COMPANY_NUMBER = os.getenv("COMPANY_NUMBER", "17347262").strip()
COMPANY_JURISDICTION = os.getenv(
    "COMPANY_JURISDICTION", "England and Wales"
).strip()
COMPANY_TRADING_NAME = os.getenv(
    "COMPANY_TRADING_NAME", "Prediction Market Dataset"
).strip()
COMPANY_REGISTERED_OFFICE = os.getenv(
    "COMPANY_REGISTERED_OFFICE", ""
).strip()
COMPANY_CONTACT_EMAIL = os.getenv(
    "COMPANY_CONTACT_EMAIL", ""
).strip()
"""
    text = replace_once(text, config_anchor, config, "configuration anchor")

    old_shell = '''<div class="container">
{body}
</div>
</body>
</html>
"""'''
    new_shell = '''<div class="container">
{body}
<footer>
  <div>{escape(COMPANY_TRADING_NAME)} is a trading name of {escape(COMPANY_LEGAL_NAME)}.</div>
  <div>{escape(COMPANY_LEGAL_NAME)} is registered in {escape(COMPANY_JURISDICTION)} under company number {escape(COMPANY_NUMBER)}.</div>
  <div>Registered office: {escape(COMPANY_REGISTERED_OFFICE) if COMPANY_REGISTERED_OFFICE else "Registered office available on the Companies House register."}</div>
  <div>© 2026 {escape(COMPANY_LEGAL_NAME)}. All rights reserved.</div>
</footer>
</div>
</body>
</html>
"""'''
    text = replace_once(text, old_shell, new_shell, "page shell")

    terms_anchor = '''<div class="card">
    <h2>1. Service</h2>'''
    terms_new = '''<div class="card">
    <h2>About us</h2>
    <p>
        Prediction Market Dataset is a trading name of PMD Data Systems Ltd,
        a private limited company registered in England and Wales under company
        number 17347262. References to “PMD”, “Prediction Market Dataset”, “we”,
        “us” or “our” mean PMD Data Systems Ltd.
    </p>

    <h2>1. Service</h2>'''
    text = replace_once(text, terms_anchor, terms_new, "terms anchor")

    privacy_anchor = '''<div class="card">
    <h2>1. Information We Collect</h2>'''
    privacy_new = '''<div class="card">
    <h2>Who we are</h2>
    <p>
        PMD Data Systems Ltd is the controller responsible for personal data
        processed through Prediction Market Dataset. Company number: 17347262.
    </p>

    <h2>1. Information We Collect</h2>'''
    text = replace_once(text, privacy_anchor, privacy_new, "privacy anchor")

    MAIN.write_text(text, encoding="utf-8")
    print("Phase 18 main.py company identity patch applied.")
    print(f"Backup: {backup}")


if __name__ == "__main__":
    main()
