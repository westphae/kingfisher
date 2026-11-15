"""Flag flight DBs that look like the same recording twice.

The 2026-07-18 session ``20260718T165100Z`` has the same GPS row count and
the same max groundspeed as ``20260712T210041Z``, and the JPI track fit on
the later copy was 621 m. That is under the 750 m refusal, so the merge can
succeed and still be the wrong hour. A matching fingerprint is recorded; it
does not block a merge.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def flag_sessions(flights_dir: Path) -> list[dict]:
    """Current flags. Recomputed every run; not a history."""
    flights_dir = Path(flights_dir)
    fingerprints = []
    for path in sorted(flights_dir.glob("*.db")):
        fp = _fingerprint(path)
        if fp is not None:
            fingerprints.append(fp)
    return flags_from_fingerprints(fingerprints)


def flags_from_fingerprints(fingerprints: list[dict]) -> list[dict]:
    """Duplicate and clock-step flags for fingerprints already collected."""
    flags = []
    groups: dict[tuple, list[dict]] = {}
    for fp in fingerprints:
        if fp["gps_rows"] is None or fp["max_gs"] is None or fp["gps_rows"] < 10:
            continue
        key = (fp["gps_rows"], round(float(fp["max_gs"]), 5))
        groups.setdefault(key, []).append(fp)
    for (rows, max_gs), members in groups.items():
        if len(members) < 2:
            continue
        names = [m["name"] for m in members]
        residuals = [
            f"{m['name']} JPI residual {m['jpi_residual_m']:.0f} m"
            for m in members
            if m.get("jpi_residual_m") is not None
        ]
        detail = (
            f"{', '.join(names)} share {rows} GPS rows and max groundspeed {max_gs:.5f} m/s. "
            "Suspected duplicate or clock-jumped session."
        )
        if residuals:
            detail += " " + "; ".join(residuals) + "."
        loose = [
            m
            for m in members
            if m.get("jpi_residual_m") is not None and m["jpi_residual_m"] >= 400.0
        ]
        if loose:
            detail += (
                " A residual of several hundred metres can still pass the 750 m gate "
                "and is a poor match for a copied track."
            )
        flags.append(
            {
                "kind": "suspected_duplicate",
                "sessions": names,
                "gps_rows": rows,
                "max_gs_mps": max_gs,
                "detail": detail,
            }
        )
    for fp in fingerprints:
        step = fp.get("clock_step_s")
        if step is None or abs(step) < 60.0:
            continue
        flags.append(
            {
                "kind": "clock_step",
                "sessions": [fp["name"]],
                "gps_rows": fp["gps_rows"],
                "max_gs_mps": fp["max_gs"],
                "detail": (
                    f"{fp['name']} has a clock_offsets step of {step:.0f} s. "
                    "Worth a look next to any duplicate fingerprint."
                ),
            }
        )
    return flags


def _fingerprint(path: Path) -> dict | None:
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        return fingerprint_conn(conn, path.name)
    finally:
        conn.close()


def fingerprint_conn(conn: sqlite3.Connection, name: str) -> dict | None:
    """COUNT/MAX and metadata only. Does not read the track into Python."""
    try:
        gps_rows = None
        max_gs = None
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='gps'"
        ).fetchone()
        if row:
            gps_rows, max_gs = conn.execute("SELECT COUNT(*), MAX(gs) FROM gps").fetchone()
        residual = _meta_float(conn, "jpi_edm930_residual")
        method = _meta(conn, "jpi_edm930_method")
        if method and method != "gps_track":
            residual = None
        return {
            "name": name,
            "path": name,
            "gps_rows": int(gps_rows) if gps_rows else 0,
            "max_gs": None if max_gs is None else float(max_gs),
            "jpi_residual_m": residual,
            "clock_step_s": _clock_step_s(conn),
        }
    except sqlite3.Error:
        return None


def _meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='metadata'"
    ).fetchone()
    if not row:
        return None
    found = conn.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
    if not found or found[0] in (None, ""):
        return None
    return str(found[0])


def _meta_float(conn: sqlite3.Connection, key: str) -> float | None:
    raw = _meta(conn, key)
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _clock_step_s(conn: sqlite3.Connection) -> float | None:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='clock_offsets'"
    ).fetchone()
    if not row:
        return None
    cols = [r[1] for r in conn.execute("PRAGMA table_info(clock_offsets)")]
    if "delta_ns" not in cols:
        return None
    note = ", note" if "note" in cols else ""
    biggest = 0.0
    for rec in conn.execute(f"SELECT delta_ns{note} FROM clock_offsets"):
        delta = rec[0]
        if delta is None:
            continue
        if note and len(rec) > 1 and rec[1] in {"anchor", None, ""}:
            continue
        biggest = max(biggest, abs(float(delta)) / 1e9)
    return biggest or None
