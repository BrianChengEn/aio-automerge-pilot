import re


def slugify(text):
    """Lowercase, keep [a-z0-9], join words with '-'."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    return "-".join(words)
