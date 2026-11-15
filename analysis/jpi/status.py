"""Cheap JPI chip for the flight list.

The list already opens each database for its header. This reads the
``jpi_edm930_*`` metadata keys and ``sqlite_master`` only. It does not
scan ``jpi_edm930`` rows. A refusal lives in the ingest ledger, because a
refused merge does not change the database.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def ledger_path(catalog_path: Path, flights_dir: Path) -> Path | None:
    """First ingest ledger that exists. One file for the whole list."""
    env = os.environ.get("KINGFISHER_JPI_LEDGER")
    candidates = []
    if env:
        candidates.append(Path(env))
    candidates.append(Path(catalog_path).parent / "ingest.json")
    candidates.append(Path(flights_dir).parent / "engine" / "ingest.json")
    candidates.append(Path.home() / "kingfisher" / "engine" / "ingest.json")
    for path in candidates:
        if path.is_file():
            return path
    return None


def load_attempts(catalog_path: Path, flights_dir: Path) -> dict[str, list[dict]]:
    path = ledger_path(catalog_path, flights_dir)
    if path is None:
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    attempts = data.get("attempts") if isinstance(data, dict) else None
    if not isinstance(attempts, list):
        return {}
    grouped: dict[str, list[dict]] = {}
    for attempt in attempts:
        if not isinstance(attempt, dict):
            continue
        name = str(attempt.get("db") or "")
        if name:
            grouped.setdefault(name, []).append(attempt)
    return grouped


def chip(has_table: bool, meta: dict | None, attempts: list | None) -> dict:
    """Label for one flight. ``attempts`` are that database's ledger rows."""
    meta = meta or {}
    n = 0
    if has_table:
        raw = str(meta.get("jpi_edm930_captures") or "")
        if raw.isdigit():
            n = int(raw)
        else:
            parts = [part for part in str(meta.get("jpi_edm930_capture_ids") or "").split(",") if part]
            n = len(parts) if parts else 1
        if n < 1:
            n = 1
    latest: dict[tuple, dict] = {}
    for attempt in attempts or []:
        if not isinstance(attempt, dict):
            continue
        key = (str(attempt.get("jpi_file") or ""), str(attempt.get("jpi_flight_id") or ""))
        latest[key] = attempt
    refused = [row for row in latest.values() if row.get("status") in ("refused", "overlap")]
    if n >= 1:
        label = "JPI" if n == 1 else f"JPI ×{n}"
        title = f"{n} JPI capture" + ("s" if n != 1 else "") + " merged"
        if refused:
            title += f"; {len(refused)} refused or overlapping"
        return {
            "state": "ok" if n == 1 else "multi",
            "label": label,
            "title": title,
            "captures": n,
        }
    if refused:
        reason = str(refused[-1].get("reason") or "merge refused")
        return {"state": "refused", "label": "JPI refused", "title": reason, "captures": 0}
    return {"state": "none", "label": "no JPI", "title": "No engine data merged", "captures": 0}
