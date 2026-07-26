"""SQLite helpers for kingfisher flight databases."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SKIP_TABLES = frozenset(
    {
        "metadata",
        "_session",
        "sensor_attrs",
        "sqlite_sequence",
        "howgozit_log",
    }
)

# Meta / non-sample tables that are not sensor streams.
META_PREFIXES = ("hgz_",)

TABLE_DEVICE_ALIASES = {
    "icm45686_accel": "icm45686-accel",
    "icm45686_gyro": "icm45686-gyro",
}

# Canonical sensors we expect on a "full" modern session (era-dependent).
CORE_CABIN = ("gps", "icm45686_accel", "icm45686_gyro", "ahrs", "geo", "compass")
CORE_POD = ("bmp581", "mmc5983", "bq27441")
OPTIONAL_POD = ("ms4525", "airspeed")
OPTIONAL_SYS = ("system", "ups", "clock_offsets", "press_alt")


def connect_ro(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def session_start_ns(conn: sqlite3.Connection) -> int | None:
    """Hub session open time from `_session.start_time` (UTC), as ns since epoch.

    Pod sensors are often powered before the cabin hub; aged UDP backlog can
    land with `ts_ns` earlier than this. Gap analysis should ignore that window.
    """
    if "_session" not in list_tables(conn):
        return None
    try:
        cols = [r[1] for r in conn.execute('PRAGMA table_info("_session")')]
        row = conn.execute("SELECT * FROM _session LIMIT 1").fetchone()
    except sqlite3.Error:
        return None
    if not row or not cols:
        return None
    m = dict(zip(cols, row))
    raw = m.get("start_time") or m.get("StartTime")
    if not raw or not isinstance(raw, str):
        return None
    try:
        # Accept ...Z or +00:00
        s = raw.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1e9)
    except ValueError:
        return None


def list_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    return [r[0] for r in rows]


def sensor_tables(tables: list[str]) -> list[str]:
    out = []
    for t in tables:
        if t in SKIP_TABLES:
            continue
        if any(t.startswith(p) for p in META_PREFIXES):
            continue
        out.append(t)
    return out


def howgozit_tables(tables: list[str]) -> list[str]:
    return [t for t in tables if t == "howgozit_log" or t.startswith("hgz_")]


def table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]


def table_span(
    conn: sqlite3.Connection, table: str
) -> tuple[int | None, int | None, int]:
    """Return (min_ts_ns, max_ts_ns, count)."""
    try:
        row = conn.execute(
            f'SELECT MIN(ts_ns), MAX(ts_ns), COUNT(*) FROM "{table}"'
        ).fetchone()
    except sqlite3.Error:
        return None, None, 0
    if not row:
        return None, None, 0
    return row[0], row[1], int(row[2] or 0)


def latest_expected_hz(conn: sqlite3.Connection, table: str) -> float | None:
    if "sensor_attrs" not in list_tables(conn):
        return None
    device = TABLE_DEVICE_ALIASES.get(table, table)
    for attr in ("sampling_frequency", "default_hz"):
        row = conn.execute(
            """
            SELECT value FROM sensor_attrs
            WHERE device=? AND attr=? AND (channel='' OR channel IS NULL)
            ORDER BY ts_ns DESC LIMIT 1
            """,
            (device, attr),
        ).fetchone()
        if row:
            try:
                return float(row[0])
            except (TypeError, ValueError):
                pass
    return None


def gps_speed_col(cols: list[str]) -> str | None:
    for c in ("gs", "speed_kt", "speed", "speed_mps"):
        if c in cols:
            return c
    return None
