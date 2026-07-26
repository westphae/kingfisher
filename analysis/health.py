"""Per-session sampling health: rates, gaps, coverage, missing sensors.

Gap / rate stats use only samples with ``ts_ns >= _session.start_time``.
Typical workflow: power the wing pod first, then enter the cabin and start
the hub — aged pod backlog before session open is expected, not link loss.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from analysis.db import (
    CORE_CABIN,
    CORE_POD,
    connect_ro,
    gps_speed_col,
    latest_expected_hz,
    list_tables,
    sensor_tables,
    session_start_ns,
    table_columns,
    table_span,
)

# Pod (and derived) tables that commonly carry pre-session backlog.
PODISH = frozenset(
    {
        "bmp581",
        "mmc5983",
        "ms4525",
        "bq27441",
        "airspeed",
        "press_alt",
        "compass",  # may use pod mag
    }
)


@dataclass
class TableHealth:
    table: str
    rows: int = 0
    rows_pre_session: int = 0
    rows_analyzed: int = 0
    duration_h: float = 0.0
    expected_hz: float | None = None
    median_dt_ms: float | None = None
    mean_hz: float | None = None
    p99_dt_ms: float | None = None
    max_gap_s: float | None = None
    gaps_gt_1s: int = 0
    gaps_gt_thr: int = 0
    gap_threshold_ms: float = 250.0
    gap_pct: float = 0.0
    grade: str = "n/a"  # ok | warn | bad | empty


@dataclass
class SessionHealth:
    file: str
    path: str
    session_start_utc: str | None = None
    grade: str = "unknown"  # ok | warn | bad
    coverage: str = "unknown"  # full | late_start | early_end | partial | stationary
    max_gs: float | None = None
    airborne_min: float = 0.0  # minutes with gs >= 40
    pre_taxi_min: float = 0.0  # minutes before first gs >= 5
    post_land_min: float = 0.0  # minutes after last gs >= 5
    missing_core_cabin: list[str] = field(default_factory=list)
    missing_core_pod: list[str] = field(default_factory=list)
    aligned_pod_gaps_gt_1s: int = 0
    pre_session_pod_rows: int = 0
    tables: list[TableHealth] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    error: str | None = None

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def _load_ts(
    conn: sqlite3.Connection,
    table: str,
    *,
    min_ts_ns: int | None,
    max_rows: int,
) -> np.ndarray:
    """Load timestamps, optionally only at/after min_ts_ns."""
    if min_ts_ns is not None:
        # Count after cutoff for sampling step
        n = conn.execute(
            f'SELECT COUNT(*) FROM "{table}" WHERE ts_ns >= ?', (min_ts_ns,)
        ).fetchone()[0]
        if n == 0:
            return np.array([], dtype=np.int64)
        if n > max_rows:
            step = max(1, n // max_rows)
            # rowid % step is imperfect with WHERE; pull in chunks instead
            ts = np.array(
                [
                    r[0]
                    for r in conn.execute(
                        f'SELECT ts_ns FROM "{table}" WHERE ts_ns >= ? ORDER BY ts_ns',
                        (min_ts_ns,),
                    )
                ],
                dtype=np.int64,
            )
            idx = np.linspace(0, len(ts) - 1, num=min(max_rows, len(ts)), dtype=int)
            return ts[idx]
        return np.array(
            [
                r[0]
                for r in conn.execute(
                    f'SELECT ts_ns FROM "{table}" WHERE ts_ns >= ? ORDER BY ts_ns',
                    (min_ts_ns,),
                )
            ],
            dtype=np.int64,
        )

    tmin, tmax, n = table_span(conn, table)
    del tmin, tmax
    if n > max_rows:
        step = max(1, n // max_rows)
        return np.array(
            [
                r[0]
                for r in conn.execute(
                    f'SELECT ts_ns FROM "{table}" WHERE (rowid % ?) = 0 ORDER BY ts_ns',
                    (step,),
                )
            ],
            dtype=np.int64,
        )
    return np.array(
        [r[0] for r in conn.execute(f'SELECT ts_ns FROM "{table}" ORDER BY ts_ns')],
        dtype=np.int64,
    )


def _table_health(
    conn: sqlite3.Connection,
    table: str,
    *,
    session_ns: int | None,
    max_rows: int = 400_000,
) -> TableHealth:
    th = TableHealth(table=table)
    tmin, tmax, n = table_span(conn, table)
    th.rows = n
    if n < 2 or tmin is None or tmax is None:
        th.grade = "empty"
        return th

    # Pre-session backlog (common for pod when powered before hub).
    if session_ns is not None:
        th.rows_pre_session = int(
            conn.execute(
                f'SELECT COUNT(*) FROM "{table}" WHERE ts_ns < ?', (session_ns,)
            ).fetchone()[0]
        )

    # Gap/rate window: from session open (hub on). Falls back to full table
    # only if _session is missing.
    analyze_from = session_ns
    ts = _load_ts(conn, table, min_ts_ns=analyze_from, max_rows=max_rows)
    th.rows_analyzed = int(len(ts)) if analyze_from is not None else n

    if len(ts) < 2:
        # All samples were pre-session (or nearly so)
        if th.rows_pre_session > 0 and th.rows_analyzed < 2:
            th.grade = "empty"
            th.duration_h = 0.0
            return th
        th.grade = "empty"
        return th

    th.duration_h = float((ts[-1] - ts[0]) / 1e9 / 3600.0)
    th.expected_hz = latest_expected_hz(conn, table)

    d = np.diff(ts) / 1e9
    th.median_dt_ms = float(np.median(d) * 1e3)
    mean_d = float(np.mean(d))
    th.mean_hz = (1.0 / mean_d) if mean_d > 0 else None
    th.p99_dt_ms = float(np.percentile(d, 99) * 1e3)
    th.max_gap_s = float(np.max(d))
    th.gaps_gt_1s = int(np.sum(d > 1.0))

    if th.expected_hz and th.expected_hz > 0:
        nominal_ms = 1000.0 / th.expected_hz
        th.gap_threshold_ms = max(3.0 * nominal_ms, 250.0)
    elif th.median_dt_ms:
        th.gap_threshold_ms = max(3.0 * th.median_dt_ms, 250.0)

    thr_s = th.gap_threshold_ms / 1000.0
    th.gaps_gt_thr = int(np.sum(d > thr_s))
    th.gap_pct = 100.0 * th.gaps_gt_thr / len(d) if len(d) else 0.0

    # Grade (1 Hz battery tolerates longer holes than IMU/baro)
    slow = table in ("bq27441", "geo", "ups", "system", "clock_offsets")
    bad_gap = 300.0 if slow else 60.0
    warn_gap = 30.0 if slow else 5.0
    if th.max_gap_s is not None and th.max_gap_s > bad_gap:
        th.grade = "bad"
    elif th.gap_pct > 1.0 or (th.max_gap_s is not None and th.max_gap_s > warn_gap):
        th.grade = "warn"
    else:
        th.grade = "ok"

    return th


def _gps_coverage(conn: sqlite3.Connection) -> tuple[float | None, float, float, float, str]:
    """Return max_gs, airborne_min, pre_taxi_min, post_land_min, coverage label."""
    if "gps" not in list_tables(conn):
        return None, 0.0, 0.0, 0.0, "unknown"
    cols = table_columns(conn, "gps")
    scol = gps_speed_col(cols)
    if not scol:
        return None, 0.0, 0.0, 0.0, "unknown"
    rows = conn.execute(
        f'SELECT ts_ns, "{scol}" FROM gps WHERE "{scol}" IS NOT NULL ORDER BY ts_ns'
    ).fetchall()
    if len(rows) < 10:
        return None, 0.0, 0.0, 0.0, "unknown"

    ts = np.array([r[0] for r in rows], dtype=np.int64)
    gs = np.array([float(r[1]) for r in rows], dtype=np.float64)
    if scol == "speed_mps":
        gs *= 1.94384

    max_gs = float(np.max(gs))
    moving = gs >= 5.0
    airborne = gs >= 40.0

    def span_min(mask: np.ndarray) -> float:
        if not mask.any():
            return 0.0
        d = np.diff(ts) / 1e9
        both = mask[:-1] & mask[1:]
        return float(np.sum(d[both]) / 60.0)

    airborne_min = span_min(airborne)
    if not moving.any():
        return max_gs, 0.0, 0.0, 0.0, "stationary"

    first_mv = int(np.argmax(moving))
    last_mv = int(len(moving) - 1 - np.argmax(moving[::-1]))
    pre = (ts[first_mv] - ts[0]) / 1e9 / 60.0
    post = (ts[-1] - ts[last_mv]) / 1e9 / 60.0

    if pre >= 2 and post >= 2 and airborne_min >= 5:
        cov = "full"
    elif airborne_min >= 5 and pre < 2:
        cov = "late_start"
    elif airborne_min >= 5 and post < 2:
        cov = "early_end"
    elif airborne_min >= 5:
        cov = "partial"
    elif max_gs >= 40:
        cov = "partial"
    else:
        cov = "stationary"

    return max_gs, airborne_min, pre, post, cov


def _aligned_pod_gaps(
    conn: sqlite3.Connection,
    *,
    session_ns: int | None,
    thr_s: float = 1.0,
) -> int:
    pod = [t for t in ("bmp581", "mmc5983", "ms4525") if t in list_tables(conn)]
    if len(pod) < 2:
        return 0

    def gap_centers(table: str) -> list[int]:
        if session_ns is not None:
            ts = [
                r[0]
                for r in conn.execute(
                    f'SELECT ts_ns FROM "{table}" WHERE ts_ns >= ? ORDER BY ts_ns',
                    (session_ns,),
                )
            ]
        else:
            ts = [
                r[0]
                for r in conn.execute(f'SELECT ts_ns FROM "{table}" ORDER BY ts_ns')
            ]
        out = []
        for i in range(1, len(ts)):
            dt = (ts[i] - ts[i - 1]) / 1e9
            if dt > thr_s:
                out.append((ts[i - 1] + ts[i]) // 2)
        return out

    primary = gap_centers(pod[0])
    if not primary:
        return 0
    others = [gap_centers(t) for t in pod[1:]]
    aligned = 0
    for c in primary:
        if all(any(abs(o - c) < 50_000_000 for o in ots) for ots in others):
            aligned += 1
    return aligned


def analyze_health(db_path: Path, *, max_rows: int = 400_000) -> SessionHealth:
    sh = SessionHealth(file=db_path.name, path=str(db_path.resolve()))
    try:
        conn = connect_ro(db_path)
    except sqlite3.Error as e:
        sh.error = str(e)
        sh.grade = "bad"
        return sh

    try:
        sess_ns = session_start_ns(conn)
        if sess_ns is not None:
            sh.session_start_utc = datetime.fromtimestamp(
                sess_ns / 1e9, tz=timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%SZ")

        sensors = sensor_tables(list_tables(conn))
        sh.missing_core_cabin = [t for t in CORE_CABIN if t not in sensors]
        sh.missing_core_pod = [t for t in CORE_POD if t not in sensors]

        for t in sensors:
            th = _table_health(conn, t, session_ns=sess_ns, max_rows=max_rows)
            sh.tables.append(th)
            if t in PODISH:
                sh.pre_session_pod_rows += th.rows_pre_session

        max_gs, air_m, pre, post, cov = _gps_coverage(conn)
        sh.max_gs = max_gs
        sh.airborne_min = round(air_m, 1)
        sh.pre_taxi_min = round(pre, 1)
        sh.post_land_min = round(post, 1)
        sh.coverage = cov

        try:
            sh.aligned_pod_gaps_gt_1s = _aligned_pod_gaps(conn, session_ns=sess_ns)
        except Exception:  # noqa: BLE001
            sh.aligned_pod_gaps_gt_1s = 0

        # Session grade
        bad_tables = [t for t in sh.tables if t.grade == "bad"]
        warn_tables = [t for t in sh.tables if t.grade == "warn"]
        if sh.missing_core_cabin:
            sh.notes.append("missing cabin: " + ", ".join(sh.missing_core_cabin))
        if sh.missing_core_pod:
            sh.notes.append("missing pod: " + ", ".join(sh.missing_core_pod))
        if sh.aligned_pod_gaps_gt_1s:
            sh.notes.append(
                f"{sh.aligned_pod_gaps_gt_1s} aligned pod gaps >1s after session start"
            )
        if sh.pre_session_pod_rows:
            sh.notes.append(
                f"{sh.pre_session_pod_rows} pre-session pod rows (ignored for gaps)"
            )
        if cov in ("late_start", "early_end", "partial"):
            sh.notes.append(f"coverage={cov}")

        if bad_tables or len(sh.missing_core_cabin) >= 3:
            sh.grade = "bad"
        elif warn_tables or sh.missing_core_pod or cov != "full":
            sh.grade = "warn"
        else:
            sh.grade = "ok"
    except Exception as e:  # noqa: BLE001
        sh.error = str(e)
        sh.grade = "bad"
    finally:
        conn.close()
    return sh


def write_health_report(results: list[SessionHealth], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "count": len(results),
        "sessions": [r.to_json() for r in results],
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
