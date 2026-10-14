"""Downsampled time-series reads from a kingfisher flight DB."""

from __future__ import annotations

import sqlite3
from typing import Any

from analysis.clockalign import load_clock
from analysis.db import table_columns

# Soft cap on SQL groups; two extrema per bucket → ~2× points out.
DEFAULT_POINTS = 2500
MAX_POINTS = 8000


def _ident(name: str) -> str:
    if not name or not all(c.isalnum() or c == "_" for c in name):
        raise ValueError(f"invalid identifier: {name!r}")
    return name


def query_series(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    *,
    t0_ns: int,
    t1_ns: int,
    n: int = DEFAULT_POINTS,
    align: bool = True,
) -> dict[str, Any]:
    table = _ident(table)
    column = _ident(column)
    cols = table_columns(conn, table)
    if "ts_ns" not in cols:
        raise ValueError(f"{table} has no ts_ns")
    if column == "ts_ns" or column not in cols:
        raise ValueError(f"{table} has no column {column}")
    if t1_ns <= t0_ns:
        raise ValueError("t1 must be after t0")

    n = max(50, min(int(n), MAX_POINTS))
    model = load_clock(conn) if align else None
    texpr = model.aligned_sql("ts_ns") if model is not None else "ts_ns"
    yexpr = f'"{column}"'
    buckets = max(n // 2, 1)
    width = max(int((t1_ns - t0_ns) // buckets), 1)

    sql = f"""
        SELECT MIN({texpr}), MAX({texpr}), MIN({yexpr}), MAX({yexpr}), COUNT(*)
        FROM "{table}"
        WHERE {texpr} >= ? AND {texpr} <= ? AND {yexpr} IS NOT NULL
        GROUP BY {texpr} / ?
        ORDER BY 1
    """
    rows = conn.execute(sql, (t0_ns, t1_ns, width)).fetchall()

    ts: list[int] = []
    ys: list[float] = []
    raw_n = 0
    gap_thr = 3 * width
    prev_t: int | None = None
    for tmin, tmax, ymin, ymax, cnt in rows:
        if tmin is None or ymin is None:
            continue
        raw_n += int(cnt or 0)
        tmin, tmax = int(tmin), int(tmax)
        ymin, ymax = float(ymin), float(ymax)
        # Two extrema in time order so spikes survive the bucket.
        if ymin <= ymax:
            points = ((tmin, ymin), (tmax, ymax)) if tmin <= tmax else ((tmax, ymax), (tmin, ymin))
            if ymin == ymax:
                points = ((tmin, ymin),)
        else:
            points = ((tmin, ymin),)
        for t, y in points:
            if prev_t is not None and (t - prev_t) > gap_thr:
                ts.append(t)
                ys.append(None)
            ts.append(t)
            ys.append(y)
            prev_t = t

    return {
        "table": table,
        "column": column,
        "id": f"{table}.{column}",
        "align": bool(model is not None and model.needs_align),
        "t0_ns": t0_ns,
        "t1_ns": t1_ns,
        "rows_in_range": raw_n,
        "points": len(ts),
        "ts_ns": ts,
        "values": ys,
    }
