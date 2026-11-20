"""JSON cache under ``analysis-cache/engine/``.

A flight is recomputed when the database mtime or size changes, the
limits fingerprint changes, or ``REVIEWER_VERSION`` is bumped. The
cache is disposable. ``engine --all`` ignores the cache and rewrites it.
"""

from __future__ import annotations

import json
from pathlib import Path

from analysis.engine.limits import Limits, limits_fingerprint

# Bump this when review code changes so incremental runs recompute.
# ``engine --all`` also forces a full re-review without a bump.
REVIEWER_VERSION = "1"


def flight_cache_path(cache_dir: Path, session: str) -> Path:
    safe = Path(session).name.replace("/", "_")
    return cache_dir / f"{safe}.json"


def load_cached(path: Path, *, stamp: str, limits: Limits, reviewer_version: str | None = None) -> dict | None:
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
    want = REVIEWER_VERSION if reviewer_version is None else reviewer_version
    # Cache files written before this field existed were produced by version 1.
    have = data.get("reviewer_version") or "1"
    if have != want:
        return None
    return data.get("review")


def store_cached(path: Path, review: dict, *, stamp: str, limits: Limits) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "cache_stamp": stamp,
        "limits_fingerprint": limits_fingerprint(limits),
        "reviewer_version": REVIEWER_VERSION,
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


def state_path(cache_dir: Path) -> Path:
    return cache_dir / "state.json"


def load_review_state(cache_dir: Path | None) -> dict:
    if cache_dir is None:
        return {}
    path = state_path(cache_dir)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def store_review_state(cache_dir: Path, state: dict) -> None:
    path = state_path(cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, allow_nan=False), encoding="utf-8")
    tmp.replace(path)


def file_stamp(path: Path) -> str:
    st = path.stat()
    return f"{st.st_mtime_ns}:{st.st_size}"
