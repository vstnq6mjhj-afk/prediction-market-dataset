import re


def normalize_title(title):
    if not title:
        return ""

    title = str(title).lower()
    title = re.sub(r"[^a-z0-9\s]", " ", title)
    title = re.sub(r"\s+", " ", title)

    return title.strip()


def canonicalize_title(title):
    return normalize_title(title)