"""Infer auxiliary-pump position from fuel-flow steps.

Boost, mixture, and cowl flaps are not logged. A step that shows up at
steady RPM and MAP is treated as the pump, using Eric's 11/11/26 check
as the scale: LO is about +0.5 gph and +20°F EGT, HI is about +3.5 gph,
+60°F EGT and +0.2 in Hg MAP. The known anomaly is LO selected and fuel
flow going down instead of up.

A smooth mixture pull (many gph over a few seconds) is not a boost step.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from analysis.engine.limits import Limits
from analysis.engine.stats import rolling_median, rolling_std
from analysis.engine.trace import Trace, segment_parts


@dataclass
class BoostStep:
    kind: str  # lo, hi, lo_ff_drop
    i: int
    d_ff: float
    d_egt: float
    d_map: float


@dataclass
class VaporEvent:
    kind: str  # wiggle, sag, tit_rise
    i: int
    detail: str


def egt_mean(tr: Trace) -> np.ndarray:
    with np.errstate(all="ignore"):
        return np.nanmean(tr.egt, axis=1)


def find_boost_steps(tr: Trace, mask: np.ndarray, lim: Limits) -> list[BoostStep]:
    parts = segment_parts(tr.segment)
    if parts is not None:
        out: list[BoostStep] = []
        for idx in parts:
            if idx.size < 8:
                continue
            for step in find_boost_steps(tr.slice(idx), mask[idx], lim):
                step.i = int(idx[step.i])
                out.append(step)
        return out
    n = tr.n
    if n < 8:
        return []
    ff = rolling_median(tr.ff, 3)
    egt = rolling_median(egt_mean(tr), 3)
    rpm = rolling_median(tr.rpm, 3)
    mp = rolling_median(tr.map_inhg, 3)
    # Samples to look ahead: about 4–8 s.
    hop = max(2, int(round(6.0 / max(tr.dt_s, 0.5))))
    hold = max(3, int(round(10.0 / max(tr.dt_s, 0.5))))
    steps: list[BoostStep] = []
    i = 0
    while i < n - hop - 1:
        if not mask[i]:
            i += 1
            continue
        j = i + hop
        if j >= n or not mask[j]:
            i += 1
            continue
        d_ff = float(ff[j] - ff[i])
        d_egt = float(egt[j] - egt[i]) if np.isfinite(egt[j]) and np.isfinite(egt[i]) else float("nan")
        d_rpm = abs(float(rpm[j] - rpm[i])) if np.isfinite(rpm[j]) and np.isfinite(rpm[i]) else 999.0
        d_map = float(mp[j] - mp[i]) if np.isfinite(mp[j]) and np.isfinite(mp[i]) else 0.0
        if d_rpm > 80.0 or not np.isfinite(d_ff) or not np.isfinite(d_egt):
            i += 1
            continue
        tail = ff[j : min(n, j + hold)]
        if tail.size < 2 or not np.isfinite(tail).any():
            i += 1
            continue
        if abs(float(np.nanmean(tail)) - float(ff[j])) > 0.45:
            i += 1
            continue
        # A noisy stretch is vapor, not a commanded step.
        pre = ff[max(0, i - hold) : i]
        if pre.size >= 3 and float(np.nanstd(pre)) > 0.45:
            i += 1
            continue
        # A mixture pull is a slope. A pump selection is a step off a flat line.
        if pre.size >= 3 and abs(float(np.nanmedian(pre[-2:]) - np.nanmedian(pre[:2]))) > 0.4:
            i += 1
            continue
        kind = None
        if lim.boost_lo_ff_lo <= d_ff <= lim.boost_lo_ff_hi and d_egt >= lim.boost_lo_egt_min_f:
            kind = "lo"
        elif (
            -lim.boost_lo_ff_hi <= d_ff <= -lim.boost_lo_ff_lo
            and d_egt >= lim.boost_lo_drop_egt_min_f
        ):
            kind = "lo_ff_drop"
        elif lim.boost_hi_ff_lo <= d_ff <= lim.boost_hi_ff_hi and d_egt >= lim.boost_hi_egt_min_f:
            kind = "hi"
        if kind is None:
            i += 1
            continue
        steps.append(BoostStep(kind=kind, i=i, d_ff=d_ff, d_egt=d_egt, d_map=d_map))
        i = j + hold
    return steps


def find_vapor(tr: Trace, mask: np.ndarray, lim: Limits) -> list[VaporEvent]:
    """Fuel-flow wiggle, a sag at steady power, or a TIT rise that FF does not explain."""
    parts = segment_parts(tr.segment)
    if parts is not None:
        out: list[VaporEvent] = []
        for idx in parts:
            if idx.size < 12:
                continue
            for event in find_vapor(tr.slice(idx), mask[idx], lim):
                event.i = int(idx[event.i])
                out.append(event)
        return out
    n = tr.n
    if n < 12 or not mask.any():
        return []
    win = max(5, int(round(lim.vapor_window_s / max(tr.dt_s, 0.5))))
    if win % 2 == 0:
        win += 1
    ff = tr.ff
    base = rolling_median(ff, win)
    resid = ff - base
    ff_std = rolling_std(resid, win)
    rpm_std = rolling_std(tr.rpm, win)
    map_std = rolling_std(tr.map_inhg, max(3, win // 2))
    events: list[VaporEvent] = []
    i = 0
    while i < n:
        if not mask[i] or not np.isfinite(ff_std[i]):
            i += 1
            continue
        steady = (not np.isfinite(rpm_std[i]) or rpm_std[i] < 40.0) and (
            not np.isfinite(map_std[i]) or map_std[i] < 0.35
        )
        if steady and ff_std[i] >= lim.vapor_ff_std_gph:
            j = i
            while j < n and mask[j] and np.isfinite(ff_std[j]) and ff_std[j] >= lim.vapor_ff_std_gph:
                j += 1
            if tr.t[min(j, n - 1)] - tr.t[i] >= 15.0:
                events.append(
                    VaporEvent(
                        "wiggle",
                        i,
                        f"fuel flow residual σ {ff_std[i]:.2f} gph over ~{lim.vapor_window_s:.0f}s",
                    )
                )
            i = j
            continue
        i += 1

    # Sag: residual drops below -sag and comes back, RPM/MAP steady.
    i = win
    while i < n - 2:
        if mask[i] and np.isfinite(resid[i]) and resid[i] <= -lim.vapor_sag_gph:
            steady = (not np.isfinite(rpm_std[i]) or rpm_std[i] < 40.0) and (
                not np.isfinite(map_std[i]) or map_std[i] < 0.4
            )
            if steady:
                # Ignore the big commanded lean (residual of a 10 gph pull).
                if resid[i] > -4.0:
                    events.append(
                        VaporEvent("sag", i, f"fuel flow {resid[i]:.1f} gph under the {lim.vapor_window_s:.0f}s median")
                    )
                    i += win
                    continue
        i += 1

    tit = rolling_median(tr.tit, 3)
    hop = max(2, int(round(20.0 / max(tr.dt_s, 0.5))))
    i = 0
    while i < n - hop:
        if not mask[i]:
            i += 1
            continue
        j = i + hop
        d_tit = float(tit[j] - tit[i]) if np.isfinite(tit[j]) and np.isfinite(tit[i]) else 0.0
        d_ff = float(ff[j] - ff[i]) if np.isfinite(ff[j]) and np.isfinite(ff[i]) else 0.0
        d_rpm = abs(float(tr.rpm[j] - tr.rpm[i])) if np.isfinite(tr.rpm[j]) and np.isfinite(tr.rpm[i]) else 0.0
        d_map = abs(float(tr.map_inhg[j] - tr.map_inhg[i])) if np.isfinite(tr.map_inhg[j]) and np.isfinite(tr.map_inhg[i]) else 0.0
        # A commanded lean drops FF and TIT rises through peak. That is not vapor.
        back = max(0, i - max(hop, int(round(90.0 / max(tr.dt_s, 0.5)))))
        leaning = (
            np.isfinite(ff[i])
            and np.isfinite(ff[back])
            and float(ff[back] - ff[i]) > 1.0
        )
        if leaning:
            i += 1
            continue
        if d_tit >= lim.vapor_tit_rise_f and d_ff > -0.6 and d_rpm < 50 and d_map < 0.4:
            events.append(VaporEvent("tit_rise", i, f"TIT +{d_tit:.0f}°F with fuel flow {d_ff:+.1f} gph"))
            i = j + hop
            continue
        i += 1
    return events
