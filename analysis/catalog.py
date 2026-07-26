"""Scan flight DBs and build a catalog JSON."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from analysis.classify import SessionClass, classify
from analysis.db import (
    CORE_CABIN,
    CORE_POD,
    connect_ro,
    gps_speed_col,
    howgozit_tables,
    list_tables,
    sensor_tables,
    table_columns,
    table_span,
)
from analysis.sidecar import load_sidecar


@dataclass
class SessionEntry:
    file: str
    path: str
    size_mb: float
    class_: str
    duration_h: float = 0.0
    start_utc: str | None = None
    end_utc: str | None = None
    session_start: str | None = None
    aircraft: str | None = None
    gps_rows: int = 0
    max_gs: float | None = None
    sensors: list[str] = field(default_factory=list)
    missing_core_cabin: list[str] = field(default_factory=list)
    missing_core_pod: list[str] = field(default_factory=list)
    has_pitot: bool = False
    has_system: bool = False
    has_ups: bool = False
    has_clock_offsets: bool = False
    howgozit: list[str] = field(default_factory=list)
    howgozit_rows: int = 0
    sidecar: str | None = None
    notes_preview: str = ""
    tags: list[str] = field(default_factory=list)
    error: str | None = None

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        d["class"] = d.pop("class_")
        return d


def _ns_to_utc(ts_ns: int | None) -> str | None:
    if ts_ns is None:
        return None
    return datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def inspect_db(db_path: Path) -> SessionEntry:
    size_mb = db_path.stat().st_size / 1e6
    entry = SessionEntry(
        file=db_path.name,
        path=str(db_path.resolve()),
        size_mb=round(size_mb, 2),
        class_=SessionClass.UNKNOWN.value,
    )
    sc = load_sidecar(db_path)
    if sc:
        entry.sidecar = sc.path.name
        entry.notes_preview = " ".join(sc.notes.split())[:200]
        if "tags" in sc.overrides:
            entry.tags = [t.strip() for t in sc.overrides["tags"].split(",") if t.strip()]
        for key in ("engine_dump", "weather", "flightaware"):
            if key in sc.overrides:
                entry.tags.append(f"{key}:{sc.overrides[key]}")

    try:
        conn = connect_ro(db_path)
    except sqlite3.Error as e:
        entry.error = str(e)
        entry.class_ = classify(
            size_mb=size_mb,
            duration_h=0,
            max_gs=None,
            sidecar_class=sc.overrides.get("class") if sc else None,
            tags=entry.tags,
        ).value
        return entry

    try:
        tables = list_tables(conn)
        sensors = sensor_tables(tables)
        entry.sensors = sensors
        entry.howgozit = howgozit_tables(tables)
        entry.has_pitot = "ms4525" in sensors or "airspeed" in sensors
        entry.has_system = "system" in sensors
        entry.has_ups = "ups" in sensors
        entry.has_clock_offsets = "clock_offsets" in sensors
        entry.missing_core_cabin = [t for t in CORE_CABIN if t not in sensors]
        entry.missing_core_pod = [t for t in CORE_POD if t not in sensors]

        if "_session" in tables:
            try:
                row = conn.execute("SELECT * FROM _session LIMIT 1").fetchone()
                cols = [c[1] for c in conn.execute('PRAGMA table_info("_session")')]
                if row and cols:
                    m = dict(zip(cols, row))
                    entry.session_start = m.get("start_time") or m.get("StartTime")
                    entry.aircraft = m.get("aircraft") or m.get("tail") or m.get("n_number")
            except sqlite3.Error:
                pass

        # Span from richest stream
        tmin = tmax = None
        for t in ("gps", "ahrs", "icm45686_accel", "bmp581", "system"):
            if t not in sensors:
                continue
            a, b, n = table_span(conn, t)
            if a is None:
                continue
            tmin = a if tmin is None else min(tmin, a)
            tmax = b if tmax is None else max(tmax, b)
            if t == "gps":
                entry.gps_rows = n
                cols = table_columns(conn, "gps")
                scol = gps_speed_col(cols)
                if scol and n:
                    mx = conn.execute(f'SELECT MAX("{scol}") FROM gps').fetchone()[0]
                    if mx is not None:
                        # speed_mps → kt if clearly SI
                        mx = float(mx)
                        if scol == "speed_mps":
                            mx *= 1.94384
                        entry.max_gs = round(mx, 2)

        if tmin is not None and tmax is not None:
            entry.duration_h = round((tmax - tmin) / 1e9 / 3600.0, 3)
            entry.start_utc = _ns_to_utc(tmin)
            entry.end_utc = _ns_to_utc(tmax)

        # howgozit row counts
        hgz_n = 0
        for t in entry.howgozit:
            if t == "howgozit_log":
                continue
            try:
                hgz_n += conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            except sqlite3.Error:
                pass
        entry.howgozit_rows = hgz_n

        sidecar_class = sc.overrides.get("class") if sc else None
        entry.class_ = classify(
            size_mb=size_mb,
            duration_h=entry.duration_h,
            max_gs=entry.max_gs,
            sidecar_class=sidecar_class,
            tags=entry.tags,
        ).value
    except Exception as e:  # noqa: BLE001
        entry.error = str(e)
        entry.class_ = SessionClass.UNKNOWN.value
    finally:
        conn.close()
    return entry


def scan_flights_dir(flights_dir: Path) -> list[SessionEntry]:
    dbs = sorted(flights_dir.glob("*.db"))
    return [inspect_db(p) for p in dbs]


def write_catalog(entries: list[SessionEntry], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "count": len(entries),
        "by_class": {},
        "sessions": [e.to_json() for e in entries],
    }
    for e in entries:
        payload["by_class"][e.class_] = payload["by_class"].get(e.class_, 0) + 1
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_catalog(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
