"""JSON cache under ``analysis-cache/engine/``.

A flight is recomputed when the database mtime or size changes, or the
limits fingerprint changes. The cache is disposable.
"""

from __future__ import annotations

import json
from pathlib import Path

from analysis.engine.limits import Limits, limits_fingerprint


def flight_cache_path(cache_dir: Path, session: str) -> Path:
    safe = Path(session).name.replace("/", "_")
    return cache_dir / f"{safe}.json"


def load_cached(path: Path, *, stamp: str, limits: Limits) -> dict | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if data.get("cache_stamp") != stamp:
        return None
    if data.get("limits_fingerprint") != limits_fingerprint(limits):
        return None
    return data.get("review")


def store_cached(path: Path, review: dict, *, stamp: str, limits: Limits) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "cache_stamp": stamp,
        "limits_fingerprint": limits_fingerprint(limits),
        "review": _jsonable(review),
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, allow_nan=False), encoding="utf-8")
    tmp.replace(path)


def db_stamp(path: Path) -> str:
    st = path.stat()
    return f"{st.st_mtime_ns}:{st.st_size}"


def _jsonable(obj):
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, float):
        if obj != obj or obj in (float("inf"), float("-inf")):
            return None
        return obj
    if isinstance(obj, (str, int, bool)) or obj is None:
        return obj
    return str(obj)
