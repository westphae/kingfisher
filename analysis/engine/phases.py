"""Slice a flight into ground, runup, takeoff, climb, level-off, cruise, descent, landing.

RPM, MAP, fuel flow, groundspeed, and altitude are the inputs. Boost, cowl
flaps, and mixture position are not measured, so they are not phases.
Without altitude the vertical-speed split is skipped: a descent flown at
cruise power stays in cruise, and the descent checks say so.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from analysis.engine.limits import Limits
from analysis.engine.mag import detect_mag
from analysis.engine.stats import fill_short_gaps, rolling_median
from analysis.engine.trace import Trace

# Painted in this order. ``ground`` is engine-stopped; ``taxi`` is the rest
# of the time the airplane is not airborne.
PHASES = (
    "ground",
    "taxi",
    "mag_check",
    "takeoff",
    "climb",
    "level_off",
    "cruise",
    "descent",
    "landing",
    "shutdown",
)


@dataclass
class Span:
    name: str
    i0: int
    i1: int
    t0: float
    t1: float

    @property
    def duration_s(self) -> float:
        return float(self.t1 - self.t0)


def label_phases(tr: Trace, lim: Limits) -> np.ndarray:
    """Label each engine run on its own.

    A gap or a new ``capture_id`` is a second start. Shutdown on the first
    run must not paint the restart, and a rolling window must not cross the
    gap.
    """
    if tr.segment is not None and tr.n and int(np.max(tr.segment)) > 0:
        labels = np.empty(tr.n, dtype=object)
        for seg_id in range(int(np.max(tr.segment)) + 1):
            idx = np.flatnonzero(tr.segment == seg_id)
            if idx.size == 0:
                continue
            labels[idx] = _label_one(tr.slice(idx), lim)
        return labels
    return _label_one(tr, lim)


def _label_one(tr: Trace, lim: Limits) -> np.ndarray:
    n = tr.n
    labels = np.array(["ground"] * n, dtype=object)
    if n == 0:
        return labels
    alt = rolling_median(tr.alt_ft, 7) if tr.alt_ft is not None else None
    vs = _vs_fpm(tr.t, alt) if alt is not None else None
    dep_field, arr_field = _fields(tr, alt)
    airborne = _airborne(tr, lim, alt, dep_field)
    rpm = tr.rpm
    for i in range(n):
        if not airborne[i] and (not np.isfinite(rpm[i]) or rpm[i] < lim.engine_off_rpm):
            labels[i] = "ground"
        elif not airborne[i]:
            labels[i] = "taxi"
        else:
            labels[i] = _air_label(tr, vs, i, lim)
    if vs is None:
        _descent_without_altitude(labels, tr, lim, airborne)
    _overlay_takeoff(labels, tr, lim, alt, dep_field, airborne)
    _overlay_level_off(labels, tr, lim, vs)
    _overlay_landing(labels, tr, lim, alt, arr_field, airborne)
    _overlay_mag(labels, tr, lim)
    _overlay_shutdown(labels, tr, lim, airborne)
    return _compress(labels, tr.t, lim)


def spans_from_labels(tr: Trace, labels: np.ndarray) -> list[Span]:
    spans: list[Span] = []
    n = labels.size
    i = 0
    while i < n:
        j = i + 1
        while j < n and labels[j] == labels[i]:
            if tr.segment is not None and int(tr.segment[j]) != int(tr.segment[i]):
                break
            j += 1
        split = j < n and tr.segment is not None and int(tr.segment[j]) != int(tr.segment[i])
        if split:
            # End on the last sample of this capture. The next sample is a
            # later engine run, and the clock gap between them is not flight.
            t1 = float(tr.t[j - 1])
        elif j < n:
            t1 = float(tr.t[j])
        else:
            t1 = float(tr.t[n - 1])
        if j == n and n >= 2 and not split:
            t1 = float(tr.t[n - 1] + (tr.t[n - 1] - tr.t[n - 2]))
        spans.append(Span(str(labels[i]), i, j, float(tr.t[i]), t1))
        i = j
    return spans


def _vs_fpm(t: np.ndarray, alt: np.ndarray) -> np.ndarray:
    n = t.size
    out = np.full(n, np.nan)
    span = 20.0
    j = 0
    for i in range(n):
        while j < i and t[i] - t[j] > span:
            j += 1
        if i == j or not np.isfinite(alt[i]) or not np.isfinite(alt[j]):
            continue
        dt = float(t[i] - t[j])
        if dt < 8.0:
            continue
        out[i] = (float(alt[i]) - float(alt[j])) / dt * 60.0
    return out


def _fields(tr: Trace, alt: np.ndarray | None) -> tuple[float | None, float | None]:
    if alt is None:
        return None, None
    gs = tr.gs_kt
    if gs is not None:
        early = (tr.t - tr.t[0] < 180.0) & np.isfinite(gs) & (gs < 12.0) & np.isfinite(alt)
        late = (tr.t[-1] - tr.t < 180.0) & np.isfinite(gs) & (gs < 12.0) & np.isfinite(alt)
    else:
        early = (tr.t - tr.t[0] < 180.0) & (tr.rpm < 1800) & np.isfinite(alt)
        late = (tr.t[-1] - tr.t < 180.0) & (tr.rpm < 1800) & np.isfinite(alt)
    dep = float(np.median(alt[early])) if int(early.sum()) >= 5 else None
    arr = float(np.median(alt[late])) if int(late.sum()) >= 5 else None
    if dep is None and np.isfinite(alt).any():
        dep = float(np.nanpercentile(alt, 10))
    if arr is None:
        arr = dep
    return dep, arr


def _airborne(tr: Trace, lim: Limits, alt: np.ndarray | None, dep_field: float | None) -> np.ndarray:
    n = tr.n
    if tr.gs_kt is not None and np.isfinite(tr.gs_kt).sum() > 10:
        mask = np.isfinite(tr.gs_kt) & (tr.gs_kt >= lim.gs_airborne_kt)
        return fill_short_gaps(mask, tr.t, lim.airborne_gap_s)
    if alt is not None and dep_field is not None:
        return np.isfinite(alt) & (alt > dep_field + lim.airborne_agl_ft)
    return (tr.rpm >= 2000.0) & (tr.map_inhg >= 20.0) & (tr.ff >= 8.0)


def _air_label(tr: Trace, vs: np.ndarray | None, i: int, lim: Limits) -> str:
    v = float(vs[i]) if vs is not None and np.isfinite(vs[i]) else 0.0
    rpm = float(tr.rpm[i]) if np.isfinite(tr.rpm[i]) else 0.0
    mp = float(tr.map_inhg[i]) if np.isfinite(tr.map_inhg[i]) else 0.0
    ff = float(tr.ff[i]) if np.isfinite(tr.ff[i]) else 0.0
    # Full-rich climb power stays climb through a brief level-off bump.
    # A real descent (negative vertical speed) still wins.
    high = rpm >= lim.climb_rpm_min and mp >= lim.climb_map_min and ff >= lim.climb_ff_min
    if high and v > -lim.descent_vs_fpm:
        return "climb"
    if v <= -lim.descent_vs_fpm:
        return "descent"
    if vs is None and high:
        return "climb"
    return "cruise"


def _descent_without_altitude(labels: np.ndarray, tr: Trace, lim: Limits, airborne: np.ndarray) -> None:
    """After the last high-power stretch, a pulled-back throttle is a descent."""
    cruise = np.flatnonzero((labels == "cruise") & airborne)
    if cruise.size == 0:
        return
    last_cruise = int(cruise[-1])
    med_map = float(np.nanmedian(tr.map_inhg[labels == "cruise"]))
    if not np.isfinite(med_map):
        return
    for i in range(last_cruise, tr.n):
        if not airborne[i]:
            break
        if labels[i] == "climb":
            continue
        mp = tr.map_inhg[i]
        if np.isfinite(mp) and mp < med_map - 4.0 and tr.rpm[i] < lim.climb_rpm_min:
            labels[i] = "descent"


def _overlay_takeoff(labels, tr, lim, alt, dep_field, airborne) -> None:
    n = tr.n
    rpm = tr.rpm
    i = 0
    while i < n - 3:
        high = (
            np.isfinite(rpm[i])
            and rpm[i] >= lim.takeoff_rpm_min
            and np.isfinite(tr.map_inhg[i])
            and tr.map_inhg[i] >= lim.takeoff_map_min
            and np.isfinite(tr.ff[i])
            and tr.ff[i] >= lim.takeoff_ff_min
        )
        if not high:
            i += 1
            continue
        back = max(0, i - max(3, int(30.0 / max(tr.dt_s, 0.5))))
        prev = float(np.nanmedian(rpm[back:i])) if i > back else 0.0
        was_slow = prev < 2200.0
        gs_slow = tr.gs_kt is None or not np.isfinite(tr.gs_kt[i]) or tr.gs_kt[i] < 80.0
        near_ground = True
        if alt is not None and dep_field is not None and np.isfinite(alt[i]):
            near_ground = alt[i] < dep_field + lim.takeoff_agl_ft + 200.0
        if not (was_slow and (gs_slow or near_ground)):
            i += 1
            continue
        j = i
        while j < n and tr.t[j] - tr.t[i] < lim.takeoff_min_s:
            labels[j] = "takeoff"
            j += 1
        while j < n and tr.t[j] - tr.t[i] < lim.takeoff_max_s:
            if (
                alt is not None
                and dep_field is not None
                and np.isfinite(alt[j])
                and alt[j] > dep_field + lim.takeoff_agl_ft
            ):
                break
            if np.isfinite(rpm[j]) and rpm[j] < 2300.0:
                break
            labels[j] = "takeoff"
            j += 1
        i = max(j, i + 1)


def _overlay_level_off(labels, tr, lim, vs) -> None:
    n = tr.n
    ff = rolling_median(tr.ff, 3)
    rpm = tr.rpm
    i = 0
    while i < n - 4:
        if labels[i] == "takeoff" or not np.isfinite(ff[i]) or ff[i] < 24.0:
            i += 1
            continue
        found = None
        j = i + 1
        while j < n and tr.t[j] - tr.t[i] <= 45.0:
            if np.isfinite(ff[j]) and ff[i] - ff[j] >= 8.0 and ff[j] <= 20.0:
                found = j
                break
            j += 1
        if found is None:
            i += 1
            continue
        start = i
        k = i
        while k > 0 and tr.t[i] - tr.t[k] <= lim.level_off_lookback_s:
            # Only the level stretch, not the climb that preceded it.
            v_ok = vs is None or not np.isfinite(vs[k]) or abs(vs[k]) < 400.0
            if np.isfinite(ff[k]) and ff[k] >= 28.0 and np.isfinite(rpm[k]) and rpm[k] >= 2550.0 and v_ok:
                start = k
                k -= 1
                continue
            break
        end = found
        while end + 1 < n and tr.t[end] - tr.t[found] < 20.0:
            end += 1
        for k in range(start, end + 1):
            if labels[k] != "takeoff":
                labels[k] = "level_off"
        i = end + 1


def _overlay_landing(labels, tr, lim, alt, arr_field, airborne) -> None:
    touch = _touchdown(tr, airborne)
    if touch is None:
        return
    start = touch
    i = touch
    while i > 0 and tr.t[touch] - tr.t[i] < lim.landing_max_s:
        in_pattern = True
        if alt is not None and arr_field is not None and np.isfinite(alt[i]):
            in_pattern = alt[i] < arr_field + lim.landing_pattern_ft
        gs_slow = tr.gs_kt is not None and np.isfinite(tr.gs_kt[i]) and tr.gs_kt[i] < 110.0
        map_back = np.isfinite(tr.map_inhg[i]) and tr.map_inhg[i] < 24.0
        if in_pattern or gs_slow or map_back:
            start = i
            i -= 1
            continue
        break
    for k in range(start, touch + 1):
        if labels[k] in {"climb", "takeoff", "mag_check"}:
            continue
        labels[k] = "landing"


def _touchdown(tr: Trace, airborne: np.ndarray) -> int | None:
    last = None
    n = tr.n
    need = max(4, int(round(20.0 / max(tr.dt_s, 0.5))))
    for i in range(n - need):
        if airborne[i] and not airborne[i + 1 : i + 1 + need].any():
            last = i
    return last


def _overlay_mag(labels, tr, lim) -> None:
    takeoff = np.flatnonzero(labels == "takeoff")
    before = int(takeoff[0]) if takeoff.size else None
    found = detect_mag(tr, lim, before=before)
    if found is None:
        return
    for i in range(found.i0, found.i1):
        if labels[i] in {"taxi", "ground"}:
            labels[i] = "mag_check"


def _overlay_shutdown(labels, tr, lim, airborne) -> None:
    # Engine cutoff after the last airborne sample. A flight that ends at
    # taxi RPM keeps the taxi label; the cooldown check reports that.
    if not airborne.any():
        start = 0
    else:
        start = int(np.flatnonzero(airborne)[-1])
    seen_running = False
    for i in range(start, tr.n):
        if np.isfinite(tr.rpm[i]) and tr.rpm[i] > 800.0:
            seen_running = True
        if seen_running and np.isfinite(tr.rpm[i]) and tr.rpm[i] < lim.engine_off_rpm:
            labels[i:] = "shutdown"
            return


def _compress(labels: np.ndarray, t: np.ndarray, lim: Limits) -> np.ndarray:
    keep = {"takeoff", "mag_check", "level_off", "shutdown", "landing"}
    n = labels.size
    runs: list[list] = []
    i = 0
    while i < n:
        j = i + 1
        while j < n and labels[j] == labels[i]:
            j += 1
        runs.append([str(labels[i]), i, j])
        i = j
    changed = True
    while changed and len(runs) > 1:
        changed = False
        out: list[list] = []
        for name, a, b in runs:
            dur = float(t[b - 1] - t[a]) if b > a else 0.0
            if out and name not in keep and dur < lim.phase_min_s:
                out[-1][2] = b
                changed = True
            elif out and out[-1][0] == name:
                out[-1][2] = b
                changed = True
            else:
                out.append([name, a, b])
        runs = out
    painted = labels.copy()
    for name, a, b in runs:
        painted[a:b] = name
    return painted
