"""Align a JPI flight clock to a Kingfisher flight database.

The monitor clock is an offset plus a linear drift. A free warp per sample
is not used.

::

    t_kingfisher = t_jpi + offset_s + drift_s_per_s * (t_jpi - t_ref)

``t_ref`` is the mean JPI timestamp of the samples that overlap the
database, so offset and drift are not mixed. ``offset_s`` is seconds to add
at ``t_ref``. ``drift_s_per_s`` is dimensionless (seconds of Kingfisher clock
per second of JPI clock, minus one). Reports also give ``drift_s_per_h``.

Observables, in order:

1. GPS track, when the JPI file has a lat/lon path longer than
   ``GPS_MIN_PATH_M``. The monitor holds the last fix between updates, so
   the score uses only the samples where the position changes. Coarse time
   still has to fall inside the search window. This wins outright when the
   residual is under the refusal limit.
2. Otherwise altitude rate-of-climb, with demeaned altitude as a smaller
   term so a constant altimeter bias does not dominate. Kingfisher
   ``press_alt.pressure_alt_ft`` is preferred, then ``indicated_alt_ft``,
   then GPS ``alt_msl`` converted to feet.
3. OAT (JPI vs pod ``static_temp_c``, else a cabin ``temp_c``) is computed
   after the fit and reported. It is not in the objective.

The search window defaults to ±15 minutes (the floor asked for is ±10) and
±3 minutes of accumulated drift over the overlap. A weak peak is a refusal:
nothing is written.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np

from analysis.jpi.parse import Flight

# ±15 min. Callers may widen this; the default stays above the ±10 min floor.
DEFAULT_WINDOW_S = 900.0
# Accumulated drift searched, in seconds, across the overlap.
DRIFT_ACCUM_S = 180.0

GPS_MIN_PATH_M = 1500.0
GPS_MIN_POINTS = 20
GPS_REFUSE_MEDIAN_M = 750.0

ALT_MIN_POINTS = 30
ALT_REFUSE_ROC_CORR = 0.55
ALT_PEAK_MARGIN = 0.12
ALT_ROC_WEIGHT = 0.75

_M_PER_DEG_LAT = 111_320.0
_FT_PER_M = 3.280839895


@dataclass
class AlignResult:
    ok: bool
    method: str
    offset_s: float
    drift_s_per_s: float
    t_ref_unix: float
    residual: float
    residual_unit: str
    detail: str
    oat_pearson: float | None = None
    oat_source: str | None = None
    oat_best_offset_s: float | None = None
    n_points: int = 0
    reason: str = ""

    @property
    def drift_s_per_h(self) -> float:
        return self.drift_s_per_s * 3600.0

    def summary(self) -> str:
        if not self.ok:
            return f"REFUSED {self.method}: {self.reason}"
        oat = "n/a" if self.oat_pearson is None else f"{self.oat_pearson:.3f}"
        return (
            f"method: {self.method}\n"
            f"offset_s: {self.offset_s:.3f} at t_ref_unix {self.t_ref_unix:.3f}\n"
            f"drift: {self.drift_s_per_h:.3f} s/h ({self.drift_s_per_s:.6e} s/s)\n"
            f"residual: {self.residual:.4g} {self.residual_unit}\n"
            f"points: {self.n_points}\n"
            f"oat_pearson: {oat}"
            + (f" source {self.oat_source}" if self.oat_source else "")
            + (
                f"\noat_best_offset_s: {self.oat_best_offset_s:.1f} (check only, not in the fit)"
                if self.oat_best_offset_s is not None
                else ""
            )
            + f"\n{self.detail}"
        )


def flight_unix(flight: Flight) -> np.ndarray:
    """JPI clock as Unix seconds, treating the monitor face as UTC.

    The search window is minutes, not hours. A timezone-sized error is a
    refusal, which is what a wrong time base should do.
    """
    out = np.empty(len(flight.samples), dtype=np.float64)
    for i, sample in enumerate(flight.samples):
        out[i] = sample.t.replace(tzinfo=timezone.utc).timestamp()
    return out


def column_array(flight: Flight, name: str) -> np.ndarray:
    out = np.full(len(flight.samples), np.nan, dtype=np.float64)
    for i, sample in enumerate(flight.samples):
        val = sample.values.get(name)
        if val is not None:
            out[i] = val
    return out


def align_flight(
    flight: Flight,
    conn: sqlite3.Connection,
    *,
    window_s: float = DEFAULT_WINDOW_S,
) -> AlignResult:
    if not flight.samples:
        return _refuse("empty", "flight has no samples")
    t_j = flight_unix(flight)
    t_ref = float(np.mean(t_j))
    gps = _jpi_track(flight, t_j)
    if gps is not None:
        result = _align_gps(gps, conn, t_ref, window_s)
        if result is not None and result.ok:
            _attach_oat(result, flight, t_j, conn, window_s)
            return result
        gps_reason = result.reason if result is not None else "no Kingfisher gps table"
    else:
        gps_reason = "JPI file has no lat/lon track"

    alt = _align_altitude(flight, t_j, conn, t_ref, window_s)
    if alt.ok:
        _attach_oat(alt, flight, t_j, conn, window_s)
        if gps is not None:
            alt.detail += f" GPS was available but not used ({gps_reason})."
        return alt
    alt.reason = f"{alt.reason} GPS: {gps_reason}."
    _attach_oat(alt, flight, t_j, conn, window_s)
    return alt


def _refuse(method: str, reason: str) -> AlignResult:
    return AlignResult(
        ok=False,
        method=method,
        offset_s=0.0,
        drift_s_per_s=0.0,
        t_ref_unix=0.0,
        residual=float("nan"),
        residual_unit="",
        detail="",
        reason=reason,
    )


def _jpi_track(flight: Flight, t_j: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    if "lat_deg" not in flight.channels or "lon_deg" not in flight.channels:
        return None
    lat = column_array(flight, "lat_deg")
    lon = column_array(flight, "lon_deg")
    ok = _gps_fix_epochs(lat, lon)
    if int(ok.sum()) < GPS_MIN_POINTS:
        return None
    path = _path_length_m(lat[ok], lon[ok])
    if path < GPS_MIN_PATH_M:
        return None
    return t_j[ok], lat[ok], lon[ok]


def _gps_fix_epochs(lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    """True on rows where a finite position differs from the previous fix.

    Between GPS sentences the download repeats the last lat/lon. Those holds
    are up to half an update of along-track staleness and are not extra
    measurements. ``GPS_MIN_POINTS`` counts fixes.
    """
    keep = np.zeros(len(lat), dtype=bool)
    finite = np.flatnonzero(np.isfinite(lat) & np.isfinite(lon))
    if len(finite) == 0:
        return keep
    keep[finite[0]] = True
    if len(finite) == 1:
        return keep
    prev = finite[:-1]
    nxt = finite[1:]
    moved = (np.abs(lat[nxt] - lat[prev]) > 1e-8) | (np.abs(lon[nxt] - lon[prev]) > 1e-8)
    keep[nxt[moved]] = True
    return keep


def _path_length_m(lat: np.ndarray, lon: np.ndarray) -> float:
    if len(lat) < 2:
        return 0.0
    dlat = np.diff(lat) * _M_PER_DEG_LAT
    mean_lat = np.radians((lat[:-1] + lat[1:]) * 0.5)
    dlon = np.diff(lon) * _M_PER_DEG_LAT * np.cos(mean_lat)
    return float(np.sum(np.hypot(dlat, dlon)))


def _load_gps(conn: sqlite3.Connection) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    if not _has_column(conn, "gps", "lat") or not _has_column(conn, "gps", "lon"):
        return None
    rows = conn.execute(
        'SELECT ts_ns, lat, lon FROM gps WHERE lat IS NOT NULL AND lon IS NOT NULL ORDER BY ts_ns'
    ).fetchall()
    if len(rows) < GPS_MIN_POINTS:
        return None
    ts = np.array([r[0] / 1e9 for r in rows], dtype=np.float64)
    lat = np.array([r[1] for r in rows], dtype=np.float64)
    lon = np.array([r[2] for r in rows], dtype=np.float64)
    return ts, lat, lon


def _align_gps(track, conn, t_ref: float, window_s: float) -> AlignResult | None:
    kf = _load_gps(conn)
    if kf is None:
        return None
    t_j, lat_j, lon_j = track
    t_k, lat_k, lon_k = kf

    def score(offset: float, drift: float) -> tuple[float, int]:
        t_hat = t_j + offset + drift * (t_j - t_ref)
        inside = (t_hat >= t_k[0]) & (t_hat <= t_k[-1])
        n = int(inside.sum())
        if n < GPS_MIN_POINTS:
            return float("inf"), n
        lat_i = np.interp(t_hat[inside], t_k, lat_k)
        lon_i = np.interp(t_hat[inside], t_k, lon_k)
        err = _haversine_m(lat_j[inside], lon_j[inside], lat_i, lon_i)
        return float(np.median(err)), n

    offset, drift, best, n, margin_ok = _search(score, t_j, t_ref, window_s, lower_is_better=True)
    if not np.isfinite(best) or best > GPS_REFUSE_MEDIAN_M or not margin_ok:
        return AlignResult(
            ok=False,
            method="gps_track",
            offset_s=offset,
            drift_s_per_s=drift,
            t_ref_unix=t_ref,
            residual=best,
            residual_unit="m median horizontal",
            detail="GPS track was the candidate observable.",
            n_points=n,
            reason=(
                f"GPS median error {best:.0f} m over ±{window_s:.0f}s "
                f"(refusal above {GPS_REFUSE_MEDIAN_M:.0f} m, or the peak is flat)"
            ),
        )
    return AlignResult(
        ok=True,
        method="gps_track",
        offset_s=offset,
        drift_s_per_s=drift,
        t_ref_unix=t_ref,
        residual=best,
        residual_unit="m median horizontal",
        detail=(
            f"Matched {n} JPI GPS fixes to gps.lat/lon. "
            f"Held samples between fixes are not scored. "
            f"Median horizontal residual {best:.1f} m."
        ),
        n_points=n,
    )


def _kingfisher_altitude(conn: sqlite3.Connection) -> tuple[str, np.ndarray, np.ndarray] | None:
    if _has_column(conn, "press_alt", "pressure_alt_ft"):
        rows = conn.execute(
            "SELECT ts_ns, pressure_alt_ft FROM press_alt "
            "WHERE pressure_alt_ft IS NOT NULL ORDER BY ts_ns"
        ).fetchall()
        if len(rows) >= ALT_MIN_POINTS:
            return (
                "press_alt.pressure_alt_ft",
                np.array([r[0] / 1e9 for r in rows]),
                np.array([r[1] for r in rows], dtype=np.float64),
            )
    if _has_column(conn, "press_alt", "indicated_alt_ft"):
        rows = conn.execute(
            "SELECT ts_ns, indicated_alt_ft FROM press_alt "
            "WHERE indicated_alt_ft IS NOT NULL ORDER BY ts_ns"
        ).fetchall()
        if len(rows) >= ALT_MIN_POINTS:
            return (
                "press_alt.indicated_alt_ft",
                np.array([r[0] / 1e9 for r in rows]),
                np.array([r[1] for r in rows], dtype=np.float64),
            )
    if _has_column(conn, "gps", "alt_msl"):
        rows = conn.execute(
            "SELECT ts_ns, alt_msl FROM gps WHERE alt_msl IS NOT NULL ORDER BY ts_ns"
        ).fetchall()
        if len(rows) >= ALT_MIN_POINTS:
            return (
                "gps.alt_msl",
                np.array([r[0] / 1e9 for r in rows]),
                np.array([r[1] * _FT_PER_M for r in rows], dtype=np.float64),
            )
    return None


def _jpi_alt_name(flight: Flight) -> str | None:
    if "alt_ft" in flight.channels:
        return "alt_ft"
    return None


def _align_altitude(flight, t_j, conn, t_ref: float, window_s: float) -> AlignResult:
    name = _jpi_alt_name(flight)
    if name is None:
        return _refuse("altitude_roc", "JPI file has no altitude channel")
    kf = _kingfisher_altitude(conn)
    if kf is None:
        return _refuse("altitude_roc", "flight DB has no press_alt or gps altitude")
    source, t_k, alt_k = kf
    alt_j = column_array(flight, name)
    ok = np.isfinite(alt_j)
    if int(ok.sum()) < ALT_MIN_POINTS:
        return _refuse("altitude_roc", f"only {int(ok.sum())} JPI altitude samples")
    t_j = t_j[ok]
    alt_j = alt_j[ok]
    t_ref = float(np.mean(t_j))
    roc_j = np.gradient(alt_j, t_j)
    # Kingfisher rate on its own clock, sampled by interpolation.
    roc_k = np.gradient(alt_k, t_k)

    def score(offset: float, drift: float) -> tuple[float, int]:
        t_hat = t_j + offset + drift * (t_j - t_ref)
        inside = (t_hat >= t_k[0]) & (t_hat <= t_k[-1])
        n = int(inside.sum())
        if n < ALT_MIN_POINTS:
            return -1.0, n
        roc_i = np.interp(t_hat[inside], t_k, roc_k)
        alt_i = np.interp(t_hat[inside], t_k, alt_k)
        c_roc = _pearson(roc_j[inside], roc_i)
        c_alt = _pearson(alt_j[inside], alt_i)
        return ALT_ROC_WEIGHT * c_roc + (1.0 - ALT_ROC_WEIGHT) * c_alt, n

    def roc_only(offset: float, drift: float) -> float:
        t_hat = t_j + offset + drift * (t_j - t_ref)
        inside = (t_hat >= t_k[0]) & (t_hat <= t_k[-1])
        if int(inside.sum()) < ALT_MIN_POINTS:
            return -1.0
        roc_i = np.interp(t_hat[inside], t_k, roc_k)
        return _pearson(roc_j[inside], roc_i)

    offset, drift, best, n, margin_ok = _search(score, t_j, t_ref, window_s, lower_is_better=False)
    roc_corr = roc_only(offset, drift)
    t_hat = t_j + offset + drift * (t_j - t_ref)
    inside = (t_hat >= t_k[0]) & (t_hat <= t_k[-1])
    if int(inside.sum()) >= 2:
        roc_i = np.interp(t_hat[inside], t_k, roc_k)
        rms = float(np.sqrt(np.mean((roc_j[inside] - roc_i) ** 2)))
    else:
        rms = float("nan")
    if roc_corr < ALT_REFUSE_ROC_CORR or not margin_ok or best < ALT_REFUSE_ROC_CORR:
        return AlignResult(
            ok=False,
            method="altitude_roc",
            offset_s=offset,
            drift_s_per_s=drift,
            t_ref_unix=t_ref,
            residual=rms,
            residual_unit="ft/s rms rate-of-climb",
            detail=f"Compared JPI {name} with {source}.",
            n_points=n,
            reason=(
                f"altitude rate correlation {roc_corr:.3f} "
                f"(need ≥ {ALT_REFUSE_ROC_CORR:.2f} and a distinct peak)"
            ),
        )
    return AlignResult(
        ok=True,
        method="altitude_roc",
        offset_s=offset,
        drift_s_per_s=drift,
        t_ref_unix=t_ref,
        residual=rms,
        residual_unit="ft/s rms rate-of-climb",
        detail=(
            f"Compared JPI {name} with {source}. "
            f"Rate correlation {roc_corr:.3f}; combined score {best:.3f}."
        ),
        n_points=n,
    )


def _search(score_fn, t_j, t_ref, window_s: float, *, lower_is_better: bool):
    """Grid search offset and drift. Returns offset, drift, best score, n, peak_ok."""
    span = float(t_j[-1] - t_j[0]) if len(t_j) > 1 else 1.0
    span = max(span, 1.0)
    worse = float("inf") if lower_is_better else -1.0

    def better(a, b) -> bool:
        return a < b if lower_is_better else a > b

    # Coarse offset, no drift.
    coarse = np.arange(-window_s, window_s + 1.0, 2.0)
    coarse_scores = []
    best_off = 0.0
    best_s = worse
    best_n = 0
    for off in coarse:
        s, n = score_fn(float(off), 0.0)
        coarse_scores.append(s)
        if better(s, best_s):
            best_s = s
            best_off = float(off)
            best_n = n

    # Joint refine: ±20 s of offset, ±30 s of accumulated drift, then a zoom.
    best_drift = 0.0
    for accum in np.linspace(-30.0, 30.0, 31):
        drift = float(accum) / span
        for off in np.arange(best_off - 20.0, best_off + 20.0 + 0.5, 0.5):
            if abs(off) > window_s:
                continue
            s, n = score_fn(float(off), drift)
            if better(s, best_s):
                best_s = s
                best_off = float(off)
                best_drift = drift
                best_n = n
    zoom_accum = best_drift * span
    for accum in np.linspace(zoom_accum - 2.0, zoom_accum + 2.0, 21):
        drift = float(accum) / span
        for off in np.arange(best_off - 1.0, best_off + 1.0 + 0.05, 0.1):
            if abs(off) > window_s:
                continue
            s, n = score_fn(float(off), drift)
            if better(s, best_s):
                best_s = s
                best_off = float(off)
                best_drift = drift
                best_n = n

    finite = [s for s in coarse_scores if np.isfinite(s) and (s < 1e8 if lower_is_better else s > -0.5)]
    if not finite:
        return best_off, best_drift, best_s, best_n, False
    if lower_is_better:
        # Peak must be clearly better than the median of the coarse window.
        med = float(np.median(finite))
        margin_ok = best_s < 0.5 * med if med > 0 else best_s < med
    else:
        med = float(np.median(finite))
        margin_ok = best_s >= med + ALT_PEAK_MARGIN
    return best_off, best_drift, best_s, best_n, margin_ok


def _attach_oat(result: AlignResult, flight: Flight, t_j, conn, window_s: float) -> None:
    oat_name = "oat_c" if "oat_c" in flight.channels else ("oat_f" if "oat_f" in flight.channels else None)
    loaded = _load_oat(conn)
    if oat_name is None or loaded is None:
        return
    source, t_k, temp_c = loaded
    oat = column_array(flight, oat_name)
    if oat_name.endswith("_f"):
        oat = (oat - 32.0) * 5.0 / 9.0
    ok = np.isfinite(oat)
    if int(ok.sum()) < 10:
        return
    tj = t_j[ok]
    oj = oat[ok]
    t_ref = result.t_ref_unix or float(np.mean(tj))

    def corr_at(offset: float) -> float:
        t_hat = tj + offset
        inside = (t_hat >= t_k[0]) & (t_hat <= t_k[-1])
        if int(inside.sum()) < 10:
            return -1.0
        return _pearson(oj[inside], np.interp(t_hat[inside], t_k, temp_c))

    best_off = 0.0
    best_c = -1.0
    for off in np.arange(-window_s, window_s + 1.0, 5.0):
        c = corr_at(float(off))
        if c > best_c:
            best_c = c
            best_off = float(off)
    # Pearson at the engine/GPS fit, not at the OAT lag.
    fitted = corr_at(result.offset_s) if result.ok else best_c
    result.oat_pearson = fitted
    result.oat_source = source
    result.oat_best_offset_s = best_off


def _load_oat(conn: sqlite3.Connection) -> tuple[str, np.ndarray, np.ndarray] | None:
    if _has_column(conn, "bmp581", "static_temp_c"):
        rows = conn.execute(
            "SELECT ts_ns, static_temp_c FROM bmp581 "
            "WHERE static_temp_c IS NOT NULL ORDER BY ts_ns"
        ).fetchall()
        if len(rows) >= 10:
            return (
                "bmp581.static_temp_c",
                np.array([r[0] / 1e9 for r in rows]),
                np.array([r[1] for r in rows], dtype=np.float64),
            )
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    for table in tables:
        if table in {"jpi_edm930", "gps", "metadata"}:
            continue
        if not _has_column(conn, table, "static_temp_c"):
            continue
        rows = conn.execute(
            f'SELECT ts_ns, static_temp_c FROM "{table}" '
            "WHERE static_temp_c IS NOT NULL ORDER BY ts_ns"
        ).fetchall()
        if len(rows) >= 10:
            return (
                f"{table}.static_temp_c",
                np.array([r[0] / 1e9 for r in rows]),
                np.array([r[1] for r in rows], dtype=np.float64),
            )
    for table in tables:
        if table.startswith("jpi") or table in {"gps", "metadata", "ahrs"}:
            continue
        if not _has_column(conn, table, "temp_c"):
            continue
        rows = conn.execute(
            f'SELECT ts_ns, temp_c FROM "{table}" WHERE temp_c IS NOT NULL ORDER BY ts_ns'
        ).fetchall()
        if len(rows) >= 10:
            return (
                f"{table}.temp_c",
                np.array([r[0] / 1e9 for r in rows]),
                np.array([r[1] for r in rows], dtype=np.float64),
            )
    return None


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    if not row:
        return False
    cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]
    return column in cols


def _pearson(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) < 3:
        return -1.0
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    a = a - a.mean()
    b = b - b.mean()
    denom = float(np.sqrt(np.dot(a, a) * np.dot(b, b)))
    if denom < 1e-12:
        return -1.0
    return float(np.dot(a, b) / denom)


def _haversine_m(lat1, lon1, lat2, lon2) -> np.ndarray:
    r = 6_371_000.0
    p1 = np.radians(lat1)
    p2 = np.radians(lat2)
    dphi = p2 - p1
    dl = np.radians(lon2 - lon1)
    h = np.sin(dphi / 2.0) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2.0) ** 2
    return 2.0 * r * np.arcsin(np.minimum(1.0, np.sqrt(h)))


def suggest_flight(flights: list[Flight], conn: sqlite3.Connection, window_s: float) -> Flight | None:
    """Pick the JPI flight whose raw clock overlaps the DB, ± the search window.

    This is a suggestion. It does not write.
    """
    span = _db_span(conn)
    if span is None or not flights:
        return flights[0] if len(flights) == 1 else None
    db0, db1 = span
    best: Flight | None = None
    best_overlap = -1.0
    for flight in flights:
        if not flight.samples:
            continue
        t0 = flight.start.replace(tzinfo=timezone.utc).timestamp() - window_s
        t1 = flight.end.replace(tzinfo=timezone.utc).timestamp() + window_s
        overlap = min(t1, db1) - max(t0, db0)
        if overlap > best_overlap:
            best_overlap = overlap
            best = flight
    if best_overlap <= 0:
        return None
    return best


def _db_span(conn: sqlite3.Connection) -> tuple[float, float] | None:
    for table, column in (
        ("gps", "ts_ns"),
        ("press_alt", "ts_ns"),
    ):
        if not _has_column(conn, table, column):
            continue
        row = conn.execute(f'SELECT MIN(ts_ns), MAX(ts_ns) FROM "{table}"').fetchone()
        if row and row[0] is not None:
            return row[0] / 1e9, row[1] / 1e9
    return None
