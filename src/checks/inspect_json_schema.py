import json
from pathlib import Path

# placeholder comment no punctuation
def inspect_sample_json(path: Path, max_files: int = 5):
    """Return mapping of sample file to observed top level type or keys"""
    results = {}
    files = list(path.rglob("*.json"))[:max_files]
    for f in files:
        try:
            with open(f, "r", encoding="utf8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                keys = tuple(sorted(data.keys()))
            else:
                keys = (type(data).__name__,)
            results[str(f)] = keys
        except Exception as e:
            results[str(f)] = ("PARSE_ERROR", str(e))
    return results

