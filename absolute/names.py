import re


def normalize(s: str) -> str:
    """Same rule as scorer/metrics.normalize_name — keep in lockstep."""
    s = str(s).lower().strip().replace("-", " ").replace("_", " ")
    return re.sub(r"\s+", " ", s)


def names_match(a: str, b: str) -> bool:
    """Scorer's station rule: substring either direction after normalisation."""
    a, b = normalize(a), normalize(b)
    return a in b or b in a
