"""1 Hz alignment of a flight DB for offline calibration.

``gps.gs`` is stored in m/s (gpsd TPV speed). Track is degrees true, and is
averaged as a circular mean because a plain SQL ``AVG`` breaks at north.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import numpy as np
import pandas as pd

from analysis.db import list_tables, session_start_ns, table_columns

# ns. Ignore a _session.start_time that is not a real 2020s timestamp.
_TMIN_OK = 1_600_000_000_000_000_000
_TMAX_OK = 2_000_000_000_000_000_000


def _sane_ns(ns: int | None) -> int | None:
    if ns is None:
        return None
    if _TMIN_OK < int(ns) < _TMAX_OK:
        return int(ns)
    return None


def _avg_table(
    conn: sqlite3.Connection,
    table: str,
    cols: list[str],
    tmin: int | None,
    have: set[str],
    *,
    required: list[str] | None = None,
) -> pd.DataFrame | None:
    if table not in have:
        return None
    present = set(table_columns(conn, table))
    use = [c for c in cols if c in present]
    need = required if required is not None else cols
    if any(c not in use for c in need) or not use:
        return None
    sel = ", ".join(f'AVG("{c}") AS "{c}"' for c in use)
    where = "WHERE ts_ns >= ?" if tmin is not None else ""
    params: tuple[Any, ...] = (tmin,) if tmin is not None else ()
    q = f'SELECT ts_ns/1000000000 AS ep, {sel} FROM "{table}" {where} GROUP BY ep'
    df = pd.read_sql_query(q, conn, params=params)
    if df.empty:
        return None
    df["ep"] = df["ep"].astype(np.int64)
    return df


def _track_means(conn: sqlite3.Connection, tmin: int | None) -> pd.DataFrame | None:
    where = "WHERE track IS NOT NULL"
    params: tuple[Any, ...] = ()
    if tmin is not None:
        where += " AND ts_ns >= ?"
        params = (tmin,)
    raw = pd.read_sql_query(
        f"SELECT ts_ns/1000000000 AS ep, track FROM gps {where}",
        conn,
        params=params,
    )
    if raw.empty:
        return None
    ang = np.deg2rad(raw["track"].to_numpy(dtype=float))
    raw = raw.assign(s=np.sin(ang), c=np.cos(ang))
    g = raw.groupby("ep", sort=True)[["s", "c"]].mean()
    g["track"] = np.rad2deg(np.arctan2(g["s"], g["c"])) % 360.0
    out = g[["track"]].reset_index()
    out["ep"] = out["ep"].astype(np.int64)
    return out


def _track_rate_deg_s(ep: np.ndarray, track: np.ndarray) -> np.ndarray:
    """Derivative of unwrapped track. Samples across a gap > 3 s are dropped."""
    rate = np.full(len(ep), np.nan)
    m = np.isfinite(track)
    idx = np.flatnonzero(m)
    if len(idx) < 5:
        return rate
    t = ep[idx].astype(float)
    un = np.unwrap(np.deg2rad(track[idx]))
    dt = np.diff(t)
    r = np.gradient(un, t)
    bad = np.zeros(len(idx), dtype=bool)
    bad[1:] |= dt > 3.0
    bad[:-1] |= dt > 3.0
    r[bad] = np.nan
    rate[idx] = np.rad2deg(r)
    return rate


def load_epochs(conn: sqlite3.Connection) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Return a 1 Hz frame and the WMM scalars stored in ``geo``.

    Columns that a session does not have are absent. ``track_rate`` is
    degrees per second, positive when the ground track turns right.
    """
    have = set(list_tables(conn))
    tmin = _sane_ns(session_start_ns(conn))
    parts: list[pd.DataFrame] = []

    gps = _avg_table(
        conn, "gps", ["gs", "lat", "lon", "vs"], tmin, have, required=["gs"]
    )
    if gps is not None:
        parts.append(gps)
    trk = _track_means(conn, tmin) if "gps" in have else None
    if trk is not None:
        parts.append(trk)

    named = {
        "mmc5983": ["mag_x_ut", "mag_y_ut", "mag_z_ut"],
        "icm45686_accel": ["accel_x", "accel_y", "accel_z"],
        "icm45686_gyro": ["anglvel_x", "anglvel_y", "anglvel_z"],
        "ahrs": ["roll", "pitch", "yaw"],
        "compass": ["heading_true_deg", "heading_mag_deg"],
    }
    for table, cols in named.items():
        df = _avg_table(conn, table, cols, tmin, have)
        if df is not None:
            parts.append(df)

    meta: dict[str, Any] = {
        "tmin_ns": tmin,
        "declination_deg": None,
        "wmm_h_ut": None,
        "wmm_z_ut": None,
        "wmm_f_ut": None,
        "wmm_inclination_deg": None,
    }
    if "geo" in have:
        row = conn.execute(
            """
            SELECT AVG(declination), AVG(field_h_nt), AVG(field_z_nt),
                   AVG(field_f_nt), AVG(inclination)
            FROM geo
            """
        ).fetchone()
        if row and row[0] is not None:
            meta["declination_deg"] = float(row[0])
            meta["wmm_h_ut"] = float(row[1]) / 1000.0 if row[1] is not None else None
            meta["wmm_z_ut"] = float(row[2]) / 1000.0 if row[2] is not None else None
            meta["wmm_f_ut"] = float(row[3]) / 1000.0 if row[3] is not None else None
            meta["wmm_inclination_deg"] = float(row[4]) if row[4] is not None else None

    if not parts:
        return pd.DataFrame(columns=["ep"]), meta

    out = parts[0]
    for p in parts[1:]:
        out = out.merge(p, on="ep", how="outer")
    out = out.sort_values("ep").reset_index(drop=True)
    if "track" in out.columns:
        out["track_rate"] = _track_rate_deg_s(
            out["ep"].to_numpy(dtype=np.int64),
            out["track"].to_numpy(dtype=float),
        )
    else:
        out["track_rate"] = np.nan
    return out, meta
