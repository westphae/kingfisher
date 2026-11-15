"""One flight of engine samples on a single time base.

A Kingfisher flight uses ``jpi_edm930`` for the engine and borrows altitude
from ``press_alt`` when that table exists, otherwise ``gps.alt_msl``. IAS
comes from ``airspeed.ias_kt``. A raw EDM-930 download has no IAS and, on
this airplane, no altitude. Groundspeed can still be derived from the
monitor's own lat/lon so taxi versus airborne still splits.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

import numpy as np

from analysis.engine.stats import haversine_m
from analysis.jpi.parse import Flight

_M_TO_FT = 3.280839895
_MPS_TO_KT = 1.9438444924406
_SERIES_TABLE = "jpi_edm930"


@dataclass
class Trace:
    session: str
    source: str  # "db" or "jpi"
    t: np.ndarray  # unix seconds
    t_ns: np.ndarray
    rpm: np.ndarray
    map_inhg: np.ndarray
    ff: np.ndarray
    tit: np.ndarray
    oil_temp_f: np.ndarray
    oil_press: np.ndarray
    oat_c: np.ndarray
    egt: np.ndarray  # (n, 6) °F
    cht: np.ndarray  # (n, 6) °F
    alt_ft: np.ndarray | None
    alt_source: str | None
    ias_kt: np.ndarray | None
    ias_note: str
    gs_kt: np.ndarray | None
    gs_source: str | None
    hp_pct: np.ndarray | None
    series_table: str = _SERIES_TABLE
    jpi_flight_id: int | None = None

    @property
    def n(self) -> int:
        return int(self.t.size)

    @property
    def dt_s(self) -> float:
        if self.n < 2:
            return 2.0
        d = np.diff(self.t)
        d = d[np.isfinite(d) & (d > 0) & (d < 30)]
        if d.size == 0:
            return 2.0
        return float(np.median(d))

    @property
    def ias_usable(self) -> bool:
        return self.ias_note == "ok"


def _col(rows: list, idx: int) -> np.ndarray:
    out = np.empty(len(rows), dtype=np.float64)
    for i, row in enumerate(rows):
        v = row[idx]
        out[i] = np.nan if v is None else float(v)
    return out


def _f_or_c(rows, names: list[str], idx_of: dict[str, int]) -> np.ndarray:
    """Prefer °F. Convert a °C column when that is all the download stored."""
    n = len(rows)
    if any(name.endswith("_f") and name in idx_of for name in names):
        for name in names:
            if name.endswith("_f") and name in idx_of:
                return _col(rows, idx_of[name])
    for name in names:
        if name.endswith("_c") and name in idx_of:
            c = _col(rows, idx_of[name])
            return c * 9.0 / 5.0 + 32.0
    return np.full(n, np.nan)


def _oat_c(rows, idx_of: dict[str, int]) -> np.ndarray:
    n = len(rows)
    if "oat_c" in idx_of:
        return _col(rows, idx_of["oat_c"])
    if "oat_f" in idx_of:
        f = _col(rows, idx_of["oat_f"])
        return (f - 32.0) * 5.0 / 9.0
    return np.full(n, np.nan)


def _bank(rows, idx_of: dict[str, int], stem: str) -> np.ndarray:
    n = len(rows)
    out = np.full((n, 6), np.nan)
    for cyl in range(1, 7):
        f_name = f"{stem}{cyl}_f"
        c_name = f"{stem}{cyl}_c"
        if f_name in idx_of:
            out[:, cyl - 1] = _col(rows, idx_of[f_name])
        elif c_name in idx_of:
            out[:, cyl - 1] = _col(rows, idx_of[c_name]) * 9.0 / 5.0 + 32.0
    return out


def _gs_from_track(t: np.ndarray, lat: np.ndarray, lon: np.ndarray) -> np.ndarray | None:
    finite = np.flatnonzero(np.isfinite(lat) & np.isfinite(lon))
    if finite.size < 2:
        return None
    gs = np.full(t.size, np.nan)
    prev = int(finite[0])
    moved = False
    for i in finite[1:]:
        i = int(i)
        if abs(lat[i] - lat[prev]) < 1e-7 and abs(lon[i] - lon[prev]) < 1e-7:
            continue
        dt = float(t[i] - t[prev])
        if dt <= 0 or dt > 90:
            prev = i
            continue
        dist = haversine_m(float(lat[prev]), float(lon[prev]), float(lat[i]), float(lon[i]))
        kt = (dist / dt) * _MPS_TO_KT
        if kt > 250:
            prev = i
            continue
        gs[prev : i + 1] = kt
        prev = i
        moved = True
    return gs if moved else None


def _ias_note(ias: np.ndarray | None, gs: np.ndarray | None) -> str:
    if ias is None:
        return "missing"
    valid = ias[np.isfinite(ias) & (ias > 20.0) & (ias < 220.0)]
    if valid.size < 30:
        return "missing"
    if float(np.percentile(ias[np.isfinite(ias) & (ias > 0)], 95)) >= 200.0:
        return "saturated"
    if gs is not None:
        both = np.isfinite(ias) & np.isfinite(gs) & (gs > 60.0) & (ias > 20.0)
        if int(both.sum()) > 30:
            gap = ias[both] - gs[both]
            if float(np.median(gap)) > 40.0:
                return "inconsistent with groundspeed"
    return "ok"


def _empty_like(n: int) -> dict:
    return {
        "rpm": np.full(n, np.nan),
        "map": np.full(n, np.nan),
        "ff": np.full(n, np.nan),
        "tit": np.full(n, np.nan),
        "oil_t": np.full(n, np.nan),
        "oil_p": np.full(n, np.nan),
        "oat": np.full(n, np.nan),
        "egt": np.full((n, 6), np.nan),
        "cht": np.full((n, 6), np.nan),
        "hp": np.full(n, np.nan),
    }


def from_columns(
    *,
    session: str,
    source: str,
    t: np.ndarray,
    t_ns: np.ndarray,
    rpm,
    map_inhg,
    ff,
    tit,
    oil_temp_f,
    oil_press,
    oat_c,
    egt,
    cht,
    alt_ft,
    alt_source,
    ias_kt,
    gs_kt,
    gs_source,
    hp_pct,
    jpi_flight_id: int | None = None,
) -> Trace:
    ias = None if ias_kt is None else np.asarray(ias_kt, dtype=np.float64)
    gs = None if gs_kt is None else np.asarray(gs_kt, dtype=np.float64)
    return Trace(
        session=session,
        source=source,
        t=np.asarray(t, dtype=np.float64),
        t_ns=np.asarray(t_ns, dtype=np.int64),
        rpm=np.asarray(rpm, dtype=np.float64),
        map_inhg=np.asarray(map_inhg, dtype=np.float64),
        ff=np.asarray(ff, dtype=np.float64),
        tit=np.asarray(tit, dtype=np.float64),
        oil_temp_f=np.asarray(oil_temp_f, dtype=np.float64),
        oil_press=np.asarray(oil_press, dtype=np.float64),
        oat_c=np.asarray(oat_c, dtype=np.float64),
        egt=np.asarray(egt, dtype=np.float64),
        cht=np.asarray(cht, dtype=np.float64),
        alt_ft=None if alt_ft is None else np.asarray(alt_ft, dtype=np.float64),
        alt_source=alt_source,
        ias_kt=ias,
        ias_note=_ias_note(ias, gs),
        gs_kt=gs,
        gs_source=gs_source,
        hp_pct=None if hp_pct is None else np.asarray(hp_pct, dtype=np.float64),
        jpi_flight_id=jpi_flight_id,
    )


def _interp(t_src: np.ndarray, y: np.ndarray, t_dst: np.ndarray) -> np.ndarray:
    ok = np.isfinite(t_src) & np.isfinite(y)
    if int(ok.sum()) < 2:
        return np.full(t_dst.size, np.nan)
    return np.interp(t_dst, t_src[ok], y[ok], left=np.nan, right=np.nan)


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]


def from_connection(conn: sqlite3.Connection, session: str) -> Trace | None:
    if not _table_exists(conn, _SERIES_TABLE):
        return None
    cols = _columns(conn, _SERIES_TABLE)
    if "ts_ns" not in cols:
        return None
    wanted = [
        "ts_ns",
        "rpm",
        "map_inhg",
        "fuel_flow_gph",
        "tit1_f",
        "tit1_c",
        "oil_temp_f",
        "oil_temp_c",
        "oil_press_psi",
        "oat_c",
        "oat_f",
        "hp_pct",
        "lat_deg",
        "lon_deg",
    ]
    for cyl in range(1, 7):
        wanted.extend([f"egt{cyl}_f", f"egt{cyl}_c", f"cht{cyl}_f", f"cht{cyl}_c"])
    select = [c for c in wanted if c in cols]
    if "rpm" not in select:
        return None
    rows = conn.execute(
        f'SELECT {", ".join(select)} FROM "{_SERIES_TABLE}" ORDER BY ts_ns'
    ).fetchall()
    if not rows:
        return None
    idx = {name: i for i, name in enumerate(select)}
    t_ns = _col(rows, idx["ts_ns"]).astype(np.int64)
    t = t_ns.astype(np.float64) / 1e9
    n = len(rows)
    blank = _empty_like(n)
    rpm = _col(rows, idx["rpm"]) if "rpm" in idx else blank["rpm"]
    mp = _col(rows, idx["map_inhg"]) if "map_inhg" in idx else blank["map"]
    ff = _col(rows, idx["fuel_flow_gph"]) if "fuel_flow_gph" in idx else blank["ff"]
    tit = _f_or_c(rows, ["tit1_f", "tit1_c"], idx)
    oil_t = _f_or_c(rows, ["oil_temp_f", "oil_temp_c"], idx)
    oil_p = _col(rows, idx["oil_press_psi"]) if "oil_press_psi" in idx else blank["oil_p"]
    oat = _oat_c(rows, idx)
    egt = _bank(rows, idx, "egt")
    cht = _bank(rows, idx, "cht")
    hp = _col(rows, idx["hp_pct"]) if "hp_pct" in idx else None

    alt_ft = None
    alt_source = None
    if _table_exists(conn, "press_alt"):
        pcols = _columns(conn, "press_alt")
        pcol = "pressure_alt_ft" if "pressure_alt_ft" in pcols else None
        if pcol is None and "indicated_alt_ft" in pcols:
            pcol = "indicated_alt_ft"
        if pcol is not None:
            prows = conn.execute(
                f'SELECT ts_ns, "{pcol}" FROM press_alt WHERE "{pcol}" IS NOT NULL ORDER BY ts_ns'
            ).fetchall()
            if len(prows) >= 2:
                pt = np.array([r[0] / 1e9 for r in prows])
                py = np.array([r[1] for r in prows], dtype=np.float64)
                alt_ft = _interp(pt, py, t)
                alt_source = f"press_alt.{pcol}"
    if alt_ft is None and _table_exists(conn, "gps") and "alt_msl" in _columns(conn, "gps"):
        grows = conn.execute(
            "SELECT ts_ns, alt_msl FROM gps WHERE alt_msl IS NOT NULL ORDER BY ts_ns"
        ).fetchall()
        if len(grows) >= 2:
            gt = np.array([r[0] / 1e9 for r in grows])
            gy = np.array([r[1] for r in grows], dtype=np.float64) * _M_TO_FT
            alt_ft = _interp(gt, gy, t)
            alt_source = "gps.alt_msl"

    gs = None
    gs_source = None
    if _table_exists(conn, "gps") and "gs" in _columns(conn, "gps"):
        grows = conn.execute(
            "SELECT ts_ns, gs FROM gps WHERE gs IS NOT NULL ORDER BY ts_ns"
        ).fetchall()
        if len(grows) >= 2:
            gt = np.array([r[0] / 1e9 for r in grows])
            gy = np.array([r[1] for r in grows], dtype=np.float64) * _MPS_TO_KT
            gs = _interp(gt, gy, t)
            gs_source = "gps.gs"
    if gs is None and "lat_deg" in idx and "lon_deg" in idx:
        gs = _gs_from_track(t, _col(rows, idx["lat_deg"]), _col(rows, idx["lon_deg"]))
        gs_source = "jpi lat/lon" if gs is not None else None

    ias = None
    if _table_exists(conn, "airspeed") and "ias_kt" in _columns(conn, "airspeed"):
        arows = conn.execute(
            "SELECT ts_ns, ias_kt FROM airspeed WHERE ias_kt IS NOT NULL ORDER BY ts_ns"
        ).fetchall()
        if len(arows) >= 2:
            at = np.array([r[0] / 1e9 for r in arows])
            ay = np.array([r[1] for r in arows], dtype=np.float64)
            # A stuck zero is "no indication", not a real IAS of 0.
            ay = np.where(ay > 1.0, ay, np.nan)
            ias = _interp(at, ay, t)

    flight_id = None
    if _table_exists(conn, "metadata"):
        row = conn.execute(
            "SELECT value FROM metadata WHERE key='jpi_edm930_flight_id'"
        ).fetchone()
        if row and row[0] not in (None, ""):
            try:
                flight_id = int(float(row[0]))
            except ValueError:
                flight_id = None

    return from_columns(
        session=session,
        source="db",
        t=t,
        t_ns=t_ns,
        rpm=rpm,
        map_inhg=mp,
        ff=ff,
        tit=tit,
        oil_temp_f=oil_t,
        oil_press=oil_p,
        oat_c=oat,
        egt=egt,
        cht=cht,
        alt_ft=alt_ft,
        alt_source=alt_source,
        ias_kt=ias,
        gs_kt=gs,
        gs_source=gs_source,
        hp_pct=hp,
        jpi_flight_id=flight_id,
    )


def from_flight(flight: Flight, session: str) -> Trace | None:
    if not flight.samples:
        return None
    n = len(flight.samples)
    t = np.empty(n, dtype=np.float64)
    from datetime import timezone

    for i, sample in enumerate(flight.samples):
        t[i] = sample.t.replace(tzinfo=timezone.utc).timestamp()
    t_ns = np.rint(t * 1e9).astype(np.int64)

    def series(name: str) -> np.ndarray:
        out = np.full(n, np.nan)
        for i, sample in enumerate(flight.samples):
            v = sample.values.get(name)
            if v is not None:
                out[i] = float(v)
        return out

    def f_or_c(stem: str) -> np.ndarray:
        f = series(f"{stem}_f")
        if np.isfinite(f).any():
            return f
        c = series(f"{stem}_c")
        if np.isfinite(c).any():
            return c * 9.0 / 5.0 + 32.0
        return f

    egt = np.column_stack([f_or_c(f"egt{c}") for c in range(1, 7)])
    cht = np.column_stack([f_or_c(f"cht{c}") for c in range(1, 7)])
    oat = series("oat_c")
    if not np.isfinite(oat).any():
        oat_f = series("oat_f")
        oat = (oat_f - 32.0) * 5.0 / 9.0
    alt = series("alt_ft")
    alt_ft = alt if np.isfinite(alt).any() else None
    lat = series("lat_deg")
    lon = series("lon_deg")
    gs = _gs_from_track(t, lat, lon)
    hp = series("hp_pct")
    return from_columns(
        session=session,
        source="jpi",
        t=t,
        t_ns=t_ns,
        rpm=series("rpm"),
        map_inhg=series("map_inhg"),
        ff=series("fuel_flow_gph"),
        tit=f_or_c("tit1"),
        oil_temp_f=f_or_c("oil_temp"),
        oil_press=series("oil_press_psi"),
        oat_c=oat,
        egt=egt,
        cht=cht,
        alt_ft=alt_ft,
        alt_source="jpi.alt_ft" if alt_ft is not None else None,
        ias_kt=None,
        gs_kt=gs,
        gs_source="jpi lat/lon" if gs is not None else None,
        hp_pct=hp if np.isfinite(hp).any() else None,
        jpi_flight_id=flight.flight_id,
    )
