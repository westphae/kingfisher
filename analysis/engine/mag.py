"""Runup mag check: B-L-R-B at about 1700 RPM.

AFMS-550 Rev K checks the magnetos at 1700 RPM. The maximum drop is 150 RPM
and the two sides should agree within 50 RPM. Eric's usual pattern is both,
left, both, right, both. Per-cylinder EGT should rise on the live mag; a
cylinder whose EGT falls, or a left/right rise that does not match, is a flag.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from analysis.engine.limits import Limits
from analysis.engine.stats import nan_median
from analysis.engine.trace import Trace


@dataclass
class MagDrop:
    side: str  # "L" or "R" in time order; the first dip is called L
    i0: int
    i1: int
    drop_rpm: float
    egt_rise: np.ndarray  # length 6, °F vs the both-mags baseline


@dataclass
class MagCheck:
    i0: int
    i1: int
    baseline_rpm: float
    drops: list[MagDrop] = field(default_factory=list)
    pattern: str = ""
    note: str = ""


def detect_mag(tr: Trace, lim: Limits, before: int | None = None) -> MagCheck | None:
    """Find one runup before ``before`` (a sample index). None if it is not there."""
    n = tr.n
    if n < 8:
        return None
    end = n if before is None else max(0, min(n, before))
    if end < 8:
        return None
    rpm = tr.rpm
    # The whole runup, including the dips, sits below cruise RPM and on the ground.
    # The baseline (upper part of the window) still has to be near 1700.
    in_band = (rpm >= lim.mag_rpm_lo - 250.0) & (rpm <= lim.mag_rpm_hi + 50.0)
    if tr.gs_kt is not None:
        slow = ~np.isfinite(tr.gs_kt) | (tr.gs_kt < 25.0)
        in_band = in_band & slow
    runs: list[tuple[int, int]] = []
    i = 0
    while i < end:
        if not bool(in_band[i]):
            i += 1
            continue
        j = i + 1
        while j < end and bool(in_band[j]):
            j += 1
        if 12.0 <= tr.t[j - 1] - tr.t[i] <= 120.0:
            runs.append((i, j))
        i = j
    best: MagCheck | None = None
    for run_a, run_b in runs:
        pad = max(2, int(round(8.0 / max(tr.dt_s, 0.5))))
        i0 = max(0, run_a - pad)
        i1 = min(end, run_b + pad)
        found = _score_window(tr, lim, i0, i1)
        if found is None:
            continue
        if best is None or abs(found.baseline_rpm - 1700.0) < abs(best.baseline_rpm - 1700.0):
            best = found
    return best


def _score_window(tr: Trace, lim: Limits, i0: int, i1: int) -> MagCheck | None:
    rpm = tr.rpm
    window_rpm = rpm[i0:i1]
    baseline = float(np.nanpercentile(window_rpm[np.isfinite(window_rpm)], 75)) if np.isfinite(window_rpm).any() else float("nan")
    if not np.isfinite(baseline) or not (lim.mag_rpm_lo <= baseline <= lim.mag_rpm_hi + 30):
        return None
    dips = _dips(rpm, tr.t, i0, i1, baseline, lim)
    # A prop cycle is one deep drop. Mag drops are the shallower ones.
    mag_dips = [d for d in dips if lim.mag_drop_min_rpm <= d[2] <= lim.mag_drop_max_rpm + 40]
    if len(mag_dips) < 2:
        return None
    # Two deepest dips, then put them back in time order (L then R).
    mag_dips = sorted(mag_dips, key=lambda d: -d[2])[:2]
    mag_dips = sorted(mag_dips, key=lambda d: d[0])
    egt_mean_base = _egt_baseline(tr, i0, mag_dips[0][0])
    drops: list[MagDrop] = []
    for k, (a, b, depth) in enumerate(mag_dips):
        rise = _egt_rise(tr, a, b, egt_mean_base)
        drops.append(MagDrop(side="L" if k == 0 else "R", i0=a, i1=b, drop_rpm=depth, egt_rise=rise))
    pattern = "B-L-R-B" if len(drops) >= 2 else "partial"
    return MagCheck(
        i0=i0,
        i1=i1,
        baseline_rpm=baseline,
        drops=drops,
        pattern=pattern,
        note="",
    )


def _dips(rpm, t, i0, i1, baseline: float, lim: Limits) -> list[tuple[int, int, float]]:
    """Return (i_start, i_end, depth_rpm) for each recovery."""
    out = []
    i = i0
    while i < i1:
        if not np.isfinite(rpm[i]) or baseline - rpm[i] < lim.mag_drop_min_rpm:
            i += 1
            continue
        j = i
        deepest = baseline - rpm[i]
        while j + 1 < i1 and np.isfinite(rpm[j + 1]) and baseline - rpm[j + 1] >= lim.mag_drop_min_rpm * 0.6:
            j += 1
            deepest = max(deepest, baseline - float(rpm[j]))
        dur = float(t[j] - t[i]) if j > i else 0.0
        if 1.0 <= dur <= 20.0 and deepest >= lim.mag_drop_min_rpm:
            out.append((i, j + 1, float(deepest)))
        i = j + 1
    # Merge dips that are really one dip split by a single sample.
    merged = []
    for dip in out:
        if merged and dip[0] - merged[-1][1] <= 2:
            prev = merged[-1]
            merged[-1] = (prev[0], dip[1], max(prev[2], dip[2]))
        else:
            merged.append(dip)
    return merged


def _egt_baseline(tr: Trace, i0: int, dip_start: int) -> np.ndarray:
    a = i0
    b = max(i0 + 1, dip_start)
    block = tr.egt[a:b]
    if block.size == 0:
        return np.full(6, np.nan)
    return np.array([nan_median(block[:, c]) for c in range(6)])


def _egt_rise(tr: Trace, a: int, b: int, base: np.ndarray) -> np.ndarray:
    block = tr.egt[a:b]
    if block.size == 0:
        return np.full(6, np.nan)
    med = np.array([nan_median(block[:, c]) for c in range(6)])
    return med - base
