"""Per-flight scorecard. Each item is pass, caution, fail, or skip.

Skip means the measurement is not in this recording (no IAS, no altitude,
no runup). It is not a pass. The review does not assume one mixture
procedure: rich-of-peak and lean-of-peak are both scored, against the
AFMS rule that applies to the side the fuel flow actually landed on.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

import numpy as np

from analysis.engine.boost import find_boost_steps, find_vapor
from analysis.engine.limits import Limits, takeoff_ff_target
from analysis.engine.mag import MagCheck, detect_mag
from analysis.engine.phases import Span, label_phases, spans_from_labels
from analysis.engine.stats import (
    fmt_num,
    nan_max,
    nan_median,
    nan_min,
    rolling_median,
    rolling_median_blocked,
    rolling_std,
)
from analysis.engine.trace import Trace

_RANK = {"skip": 0, "pass": 1, "caution": 2, "fail": 3}


def review_trace(tr: Trace, lim: Limits | None = None) -> dict:
    lim = lim or Limits()
    if tr.n < 5:
        return _empty(tr, "fewer than 5 engine samples")
    labels = label_phases(tr, lim)
    spans = spans_from_labels(tr, labels)
    checks: list[dict] = []
    events: list[dict] = []
    metrics: dict = {"phases": [s.name for s in spans]}
    notes = _notes(tr, spans)
    _taxi(tr, lim, spans, checks, events)
    mag = detect_mag(tr, lim, before=_first(spans, "takeoff"))
    _mag(tr, lim, mag, spans, checks, events)
    _takeoff(tr, lim, spans, checks, events, metrics)
    _climb(tr, lim, spans, checks, events, metrics)
    _level(tr, lim, spans, checks, events, metrics)
    peak = _peak_tit(tr, lim)
    _cruise(tr, lim, spans, peak, checks, events, metrics)
    _tit_redline(tr, lim, checks, events)
    _descent(tr, lim, spans, peak, checks, events)
    _landing(tr, lim, spans, checks, events, metrics)
    _shutdown(tr, lim, spans, checks, events)
    _oil(tr, lim, labels, checks, events, metrics)
    _cylinders(tr, lim, labels, checks, events, metrics)
    overall = _overall(checks)
    return {
        "session": tr.session,
        "source": tr.source,
        "jpi_flight_id": tr.jpi_flight_id,
        "alt_source": tr.alt_source,
        "ias": tr.ias_note,
        "gs_source": tr.gs_source,
        "overall": overall,
        "counts": _counts(checks),
        "phases": [_span_json(tr, s) for s in spans],
        "checks": checks,
        "events": events,
        "metrics": metrics,
        "notes": notes,
        "n_samples": tr.n,
        "t0_unix": float(tr.t[0]),
        "t1_unix": float(tr.t[-1]),
    }


def _empty(tr: Trace, reason: str) -> dict:
    return {
        "session": tr.session,
        "source": tr.source,
        "jpi_flight_id": tr.jpi_flight_id,
        "alt_source": tr.alt_source,
        "ias": tr.ias_note,
        "gs_source": tr.gs_source,
        "overall": "skip",
        "counts": {"pass": 0, "caution": 0, "fail": 0, "skip": 1},
        "phases": [],
        "checks": [
            {
                "id": "data",
                "phase": "flight",
                "title": "Engine data",
                "status": "skip",
                "observed": reason,
                "limit": "a parsed EDM-930 flight",
                "detail": "",
            }
        ],
        "events": [],
        "metrics": {},
        "notes": [reason],
        "n_samples": tr.n,
        "t0_unix": None,
        "t1_unix": None,
    }


def _notes(tr: Trace, spans: list[Span]) -> list[str]:
    notes = []
    if tr.alt_source:
        notes.append(f"Altitude source: {tr.alt_source}.")
    else:
        notes.append("No altitude. Climb/descent vertical speed and density altitude are skipped.")
    if tr.ias_note == "ok":
        notes.append("IAS source: airspeed.ias_kt.")
    elif tr.ias_note == "missing":
        notes.append("No usable IAS. The climb speed check is skipped.")
    else:
        notes.append(f"IAS not used ({tr.ias_note}). The climb speed check is skipped.")
    if tr.gs_source:
        notes.append(f"Groundspeed source: {tr.gs_source}.")
    elif tr.alt_source is None:
        notes.append("No groundspeed. Airborne phases are inferred from RPM, MAP, and fuel flow.")
    names = [s.name for s in spans]
    if "descent" not in names and tr.alt_source is None:
        notes.append("Without altitude, a descent at cruise power stays in cruise.")
    if tr.segment is not None and int(np.max(tr.segment)) > 0:
        ncap = int(np.max(tr.segment)) + 1
        notes.append(
            f"{ncap} engine captures. Gaps between them are not filled in, "
            "and each start is phased on its own."
        )
    return notes


def _taxi(tr, lim, spans, checks, events) -> None:
    idx = _idx(spans, "taxi")
    # Pre-takeoff taxi is the taxi before the first takeoff.
    takeoff = _first(spans, "takeoff")
    if takeoff is not None:
        pre = idx[idx < takeoff]
    else:
        pre = idx
    pre = pre[(tr.rpm[pre] >= lim.taxi_rpm_lo) & (tr.rpm[pre] <= lim.taxi_rpm_hi)] if pre.size else pre
    if pre.size < 5:
        _add(checks, "taxi_lean", "taxi", "Taxi leaning", "skip", "no taxi RPM samples", f"fuel flow under {lim.taxi_lean_ff_gph:.0f} gph")
        return
    med = nan_median(tr.ff[pre])
    status = "pass" if med <= lim.taxi_lean_ff_gph else "caution"
    _add(
        checks,
        "taxi_lean",
        "taxi",
        "Taxi leaning",
        status,
        f"median {fmt_num(med, 1, 'gph')} at {fmt_num(nan_median(tr.rpm[pre]), 0, 'RPM')}",
        f"at or under {lim.taxi_lean_ff_gph:.0f} gph while taxiing",
        "Low fuel flow at taxi RPM is the leaned-taxi practice. Mixture position is inferred from fuel flow.",
    )
    if status != "pass":
        _ev(events, tr, int(pre[0]), "caution", "taxi_lean", f"Taxi fuel flow {med:.1f} gph", ["fuel_flow_gph", "rpm"])


def _mag(tr, lim, mag: MagCheck | None, spans, checks, events) -> None:
    if mag is None:
        had_takeoff = _first(spans, "takeoff") is not None
        _add(
            checks,
            "mag_check",
            "mag_check",
            "Mag check",
            "caution" if had_takeoff else "skip",
            "not detected",
            "B-L-R-B near 1700 RPM, drop ≤ 150 RPM, L/R within 50 RPM, EGT rises",
            "No runup with two mag drops was found. Eric usually does B-L-R-B; a flight with no runup is noted, not scored as a failed drop.",
        )
        return
    left = mag.drops[0]
    right = mag.drops[1] if len(mag.drops) > 1 else None
    parts = [f"{d.side} drop {d.drop_rpm:.0f} RPM" for d in mag.drops]
    status = "pass"
    detail_bits = [f"baseline {mag.baseline_rpm:.0f} RPM", mag.pattern]
    if any(d.drop_rpm > lim.mag_drop_max_rpm for d in mag.drops):
        status = "fail"
        detail_bits.append(f"a drop exceeds {lim.mag_drop_max_rpm:.0f} RPM")
    if right is not None and abs(left.drop_rpm - right.drop_rpm) > lim.mag_asym_rpm:
        status = _worse(status, "caution")
        detail_bits.append(
            f"L/R drop differs by {abs(left.drop_rpm - right.drop_rpm):.0f} RPM (AFMS variance {lim.mag_asym_rpm:.0f})"
        )
    for drop in mag.drops:
        fell = [i + 1 for i in range(6) if np.isfinite(drop.egt_rise[i]) and drop.egt_rise[i] < -lim.mag_egt_fall_f]
        if fell:
            status = "fail"
            detail_bits.append(f"{drop.side} EGT fell on cylinder {', '.join(str(c) for c in fell)}")
            _ev(
                events,
                tr,
                drop.i0,
                "fail",
                "mag_egt_fall",
                f"Mag {drop.side}: EGT fell on cylinder {', '.join(str(c) for c in fell)}",
                [f"egt{c}_f" for c in fell[:3]] + ["rpm"],
            )
    if right is not None:
        mismatch = _egt_mismatch(left.egt_rise, right.egt_rise, lim.mag_egt_mismatch_f)
        if mismatch:
            status = _worse(status, "caution")
            detail_bits.append(mismatch)
    rises = []
    for drop in mag.drops:
        good = drop.egt_rise[np.isfinite(drop.egt_rise)]
        if good.size:
            rises.append(f"{drop.side} mean EGT rise {float(np.mean(good)):.0f}°F")
    _add(
        checks,
        "mag_check",
        "mag_check",
        "Mag check",
        status,
        ", ".join(parts + rises),
        "drop ≤ 150 RPM, L and R within 50 RPM, every cylinder's EGT rises",
        ". ".join(detail_bits),
    )
    if status != "pass":
        _ev(events, tr, mag.i0, status, "mag_check", "Mag check " + ", ".join(parts), ["rpm", "egt1_f", "egt2_f"])


def _egt_mismatch(left: np.ndarray, right: np.ndarray, limit: float) -> str:
    both = np.isfinite(left) & np.isfinite(right)
    if int(both.sum()) < 4:
        return ""
    # A real L/R split lights opposite cylinders. Identical rises are fine.
    # Flag when one side's mean rise is far from the other's, or a cylinder
    # rises on one side and falls on the other by more than the limit.
    if abs(float(np.mean(left[both]) - np.mean(right[both]))) > limit:
        return (
            f"L mean EGT rise {float(np.mean(left[both])):.0f}°F vs "
            f"R {float(np.mean(right[both])):.0f}°F"
        )
    gap = np.abs(left - right)
    if float(np.nanmax(gap)) > limit * 2 and float(np.nanmin(left[both])) < 0:
        return "per-cylinder L/R EGT rises do not match"
    return ""


def _takeoff(tr, lim, spans, checks, events, metrics) -> None:
    segs = [s for s in spans if s.name == "takeoff"]
    if not segs:
        for item, title in (
            ("takeoff_ff", "Takeoff fuel flow"),
            ("takeoff_tit", "Takeoff TIT"),
            ("takeoff_map", "Takeoff MAP"),
            ("takeoff_oil", "Takeoff oil temperature"),
            ("takeoff_boost", "Takeoff boost"),
        ):
            _add(checks, item, "takeoff", title, "skip", "no takeoff detected", "AFMS takeoff")
        return
    seg = segs[0]
    sl = slice(seg.i0, seg.i1)
    ff = tr.ff[sl]
    oat_c = nan_median(tr.oat_c[sl])
    oat_f = oat_c * 9.0 / 5.0 + 32.0 if np.isfinite(oat_c) else float("nan")
    target = takeoff_ff_target(oat_f if np.isfinite(oat_f) else None, lim)
    # Median of the whole takeoff window. The first seconds are still accelerating the flow.
    med_ff = nan_median(ff)
    if target is None:
        target = lim.takeoff_ff_below_80f
        target_txt = f"{target:.0f} gph (OAT missing, full-rich column)"
    else:
        target_txt = f"{target:.0f} gph at OAT {oat_f:.0f}°F"
    if not np.isfinite(med_ff):
        ff_status = "skip"
    elif med_ff > lim.takeoff_ff_hi_boost_gph:
        ff_status = "fail"
    elif med_ff < target - lim.takeoff_ff_tolerance_gph:
        ff_status = "fail"
    elif med_ff <= max(target, lim.takeoff_ff_below_80f) + 0.8:
        ff_status = "pass"
    else:
        ff_status = "caution"
    _add(
        checks,
        "takeoff_ff",
        "takeoff",
        "Takeoff fuel flow",
        ff_status,
        f"median {fmt_num(med_ff, 1, 'gph')}",
        target_txt + f", or up to {lim.takeoff_ff_below_80f:.0f} once clear of obstacles",
        "AFMS table: 35 gph below 80°F, 34 at 81–90°F, 33 above 90°F. Return to 35 gph clear of obstacles.",
    )
    if ff_status in {"fail", "caution"}:
        _ev(events, tr, seg.i0, ff_status, "takeoff_ff", f"Takeoff fuel flow {med_ff:.1f} gph vs {target_txt}", ["fuel_flow_gph", "rpm", "map_inhg"])

    tit = tr.tit[sl]
    # Ignore the first 10 s; the turbine is still warming.
    warm = tr.t[sl] >= tr.t[seg.i0] + 10.0
    tit_obs = tit[warm] if warm.any() else tit
    tit_med = nan_median(tit_obs)
    tit_max = nan_max(tit_obs)
    if not np.isfinite(tit_med):
        tit_status = "skip"
    elif tit_max > lim.takeoff_tit_hi_f:
        tit_status = "fail"
    elif tit_med < lim.takeoff_tit_lo_f - 40 and (seg.duration_s >= 30):
        tit_status = "caution"
    elif lim.takeoff_tit_lo_f - 40 <= tit_med <= lim.takeoff_tit_hi_f:
        tit_status = "pass" if tit_med >= lim.takeoff_tit_lo_f - 15 else "caution"
    else:
        tit_status = "caution"
    _add(
        checks,
        "takeoff_tit",
        "takeoff",
        "Takeoff TIT",
        tit_status,
        f"median {fmt_num(tit_med, 0, '°F')}, max {fmt_num(tit_max, 0, '°F')}",
        f"{lim.takeoff_tit_lo_f:.0f}–{lim.takeoff_tit_hi_f:.0f} °F after the turbine warms",
    )
    if tit_status in {"fail", "caution"} and np.isfinite(tit_max):
        _ev(events, tr, seg.i0, tit_status, "takeoff_tit", f"Takeoff TIT median {tit_med:.0f}°F", ["tit1_f", "fuel_flow_gph"])

    mp = tr.map_inhg[sl]
    oil_t = nan_median(tr.oil_temp_f[sl])
    mp_max = nan_max(mp)
    above = mp[np.isfinite(mp) & (mp > lim.takeoff_map_normal_inhg + 0.15)]
    seconds_high = float(above.size) * tr.dt_s
    cold = np.isfinite(oil_t) and oil_t < lim.takeoff_cold_oil_f
    if not np.isfinite(mp_max):
        map_status = "skip"
    elif mp_max > lim.takeoff_map_red_inhg + 0.05:
        map_status = "fail"
    elif mp_max <= lim.takeoff_map_normal_inhg + 0.2:
        map_status = "pass"
    elif cold and seconds_high <= lim.takeoff_cold_map_max_s and mp_max <= lim.takeoff_map_yellow_inhg + 0.05:
        map_status = "pass"
    elif mp_max <= lim.takeoff_map_yellow_inhg + 0.05:
        map_status = "caution"
    else:
        map_status = "fail"
    cold_txt = f", oil {oil_t:.0f}°F" if np.isfinite(oil_t) else ""
    _add(
        checks,
        "takeoff_map",
        "takeoff",
        "Takeoff MAP",
        map_status,
        f"max {fmt_num(mp_max, 1, 'in Hg')}{cold_txt}, {seconds_high:.0f}s above 29.6",
        f"normal full throttle {lim.takeoff_map_normal_inhg:.1f}; yellow to {lim.takeoff_map_yellow_inhg:.1f}; red {lim.takeoff_map_red_inhg:.1f}",
        "30–32 in Hg is acceptable briefly with cold oil. With the EDM as primary, 29.6–32.0 is yellow and 32.0 is red.",
    )
    if map_status in {"fail", "caution"}:
        _ev(events, tr, seg.i0, map_status, "takeoff_map", f"Takeoff MAP {mp_max:.1f} in Hg", ["map_inhg", "oil_temp_f", "rpm"])
    metrics["takeoff_map"] = None if not np.isfinite(mp_max) else round(mp_max, 2)

    if not np.isfinite(oil_t):
        oil_status = "skip"
    elif oil_t < lim.takeoff_oil_min_f:
        oil_status = "fail"
    elif oil_t < lim.oil_t_green_lo_f:
        oil_status = "caution"
    else:
        oil_status = "pass"
    _add(
        checks,
        "takeoff_oil",
        "takeoff",
        "Takeoff oil temperature",
        oil_status,
        fmt_num(oil_t, 0, "°F"),
        f"at least {lim.takeoff_oil_min_f:.0f}°F (yellow arc to {lim.oil_t_green_lo_f:.0f}°F)",
    )
    if oil_status in {"fail", "caution"}:
        _ev(events, tr, seg.i0, oil_status, "takeoff_oil", f"Takeoff oil {oil_t:.0f}°F", ["oil_temp_f", "oil_press_psi"])

    # HI boost on the takeoff roll: a ~3.5 gph step, or a median already over 36.5.
    mask = np.zeros(tr.n, dtype=bool)
    mask[sl] = True
    steps = find_boost_steps(tr, mask, lim)
    hi = [s for s in steps if s.kind == "hi"]
    if hi or (np.isfinite(med_ff) and med_ff > lim.takeoff_ff_hi_boost_gph):
        boost_status = "fail"
        if hi:
            obs = f"HI-sized step {hi[0].d_ff:+.1f} gph, EGT {hi[0].d_egt:+.0f}°F"
            at = hi[0].i
        else:
            obs = f"median {med_ff:.1f} gph"
            at = seg.i0
    else:
        boost_status = "pass"
        obs = "no HI-sized fuel-flow step"
        at = seg.i0
    _add(
        checks,
        "takeoff_boost",
        "takeoff",
        "Takeoff boost",
        boost_status,
        obs,
        "HI boost is forbidden on takeoff; LO is for density altitude above 5,000 ft",
        "Inferred from fuel flow. TAT: HI on takeoff can flood the engine.",
    )
    if boost_status == "fail":
        _ev(events, tr, at, "fail", "takeoff_hi_boost", "Inferred HI boost on takeoff", ["fuel_flow_gph", "egt1_f", "map_inhg"])
    metrics["takeoff_ff"] = None if not np.isfinite(med_ff) else round(float(med_ff), 2)
    metrics["takeoff_oat_f"] = None if not np.isfinite(oat_f) else round(float(oat_f), 1)


def _climb(tr, lim, spans, checks, events, metrics) -> None:
    segs = [s for s in spans if s.name == "climb"]
    if not segs:
        for item, title in (
            ("climb_ff", "Climb fuel flow"),
            ("climb_rpm", "Climb RPM"),
            ("climb_map", "Climb throttle"),
            ("climb_tit", "Climb TIT"),
            ("climb_cht", "Climb CHT"),
            ("climb_ias", "Climb IAS"),
            ("climb_vapor", "Climb vapor"),
            ("climb_boost", "Climb boost"),
        ):
            _add(checks, item, "climb", title, "skip", "no climb detected", "AFMS cruise climb")
        return
    _climb_power(tr, lim, segs, checks, events, metrics)
    _climb_temps(tr, lim, segs, checks, events, metrics)
    _climb_ias(tr, lim, segs, checks, events)
    _climb_boost_vapor(tr, lim, segs, checks, events, metrics)


def _climb_power(tr, lim, segs, checks, events, metrics) -> None:
    bits = []
    ff_status = "pass"
    rpm_status = "pass"
    map_status = "pass"
    for n, seg in enumerate(segs, 1):
        sl = slice(seg.i0, seg.i1)
        med_ff = nan_median(tr.ff[sl])
        med_rpm = nan_median(tr.rpm[sl])
        bits.append(f"climb {n}: {med_ff:.1f} gph at {med_rpm:.0f} RPM" if np.isfinite(med_ff) else f"climb {n}: n/a")
        if np.isfinite(med_ff) and med_ff < lim.climb_ff_lo_gph and seg.duration_s > 45:
            ff_status = "fail"
            _ev(events, tr, seg.i0, "fail", "climb_ff", f"Climb {n} fuel flow {med_ff:.1f} gph", ["fuel_flow_gph", "rpm", "tit1_f"])
        if np.isfinite(med_rpm) and not (lim.climb_rpm_lo <= med_rpm <= lim.climb_rpm_hi) and seg.duration_s > 45:
            rpm_status = _worse(rpm_status, "caution" if abs(med_rpm - lim.climb_rpm_target) < 150 else "fail")
        # Partial-throttle step: MAP falls more than 1.2 in Hg inside 6 s and stays down, RPM still high.
        if _map_steps(tr, seg, lim):
            map_status = "caution"
            _ev(events, tr, seg.i0, "caution", "climb_throttle", f"Climb {n} MAP step at high RPM", ["map_inhg", "rpm"])
    _add(checks, "climb_ff", "climb", "Climb fuel flow", ff_status, "; ".join(bits), f"full rich, about {lim.climb_ff_target_gph:.0f} gph (under {lim.climb_ff_lo_gph:.0f} fails)")
    _add(
        checks,
        "climb_rpm",
        "climb",
        "Climb RPM",
        rpm_status,
        "; ".join(bits),
        f"{lim.climb_rpm_target:.0f} RPM ({lim.climb_rpm_lo:.0f}–{lim.climb_rpm_hi:.0f}, 2650 is the noise-abatement set)",
    )
    _add(
        checks,
        "climb_map",
        "climb",
        "Climb throttle",
        map_status,
        "no partial-throttle MAP step" if map_status == "pass" else "MAP step while RPM stayed high",
        "wide-open throttle; MAP should not be stepped back in the climb",
    )
    metrics["climb_ff"] = [round(float(nan_median(tr.ff[s.i0 : s.i1])), 2) for s in segs]


def _same_capture(tr: Trace, i: int, j: int) -> bool:
    if tr.segment is None:
        return True
    if j < 0 or j >= tr.n:
        return False
    return int(tr.segment[i]) == int(tr.segment[j])


def _map_steps(tr: Trace, seg: Span, lim: Limits) -> bool:
    mp = rolling_median_blocked(tr.map_inhg, 3, tr.segment)
    rpm = tr.rpm
    hop = max(2, int(round(6.0 / max(tr.dt_s, 0.5))))
    i = seg.i0
    while i < seg.i1 - hop:
        if rpm[i] >= lim.climb_rpm_min and np.isfinite(mp[i]) and np.isfinite(mp[i + hop]):
            if mp[i] - mp[i + hop] >= 1.2 and mp[i] >= lim.climb_map_min:
                return True
        i += 1
    return False


def _climb_temps(tr, lim, segs, checks, events, metrics) -> None:
    tit_status = "pass"
    cht_status = "pass"
    tit_bits = []
    time_yellow = 0.0
    time_high = 0.0
    peak_cht = float("nan")
    for n, seg in enumerate(segs, 1):
        sl = slice(seg.i0, seg.i1)
        # TIT after 20s so the turbine has caught the mixture.
        warm = tr.t[sl] >= tr.t[seg.i0] + 20.0
        tit = tr.tit[sl][warm] if int(warm.sum()) > 3 else tr.tit[sl]
        med = nan_median(tit)
        mx = nan_max(tit)
        tit_bits.append(f"climb {n}: median {med:.0f} max {mx:.0f}°F" if np.isfinite(med) else f"climb {n}: n/a")
        if np.isfinite(mx) and mx > lim.climb_tit_hi_f:
            over = float(np.sum(tit > lim.climb_tit_hi_f)) * tr.dt_s
            if over >= 10.0:
                tit_status = "fail"
                hot = seg.i0 + int(np.nanargmax(np.where(np.isfinite(tr.tit[sl]), tr.tit[sl], -np.inf)))
                _ev(events, tr, hot, "fail", "climb_tit", f"Climb {n} TIT {mx:.0f}°F", ["tit1_f", "fuel_flow_gph", "rpm"])
        elif np.isfinite(med) and not (lim.climb_tit_lo_f - 20 <= med <= lim.climb_tit_hi_f):
            tit_status = _worse(tit_status, "caution")
        hottest = _hottest(tr.cht[sl])
        peak_cht = np.nanmax([peak_cht, nan_max(hottest)])
        time_yellow += float(np.sum((hottest > lim.cht_yellow_f) & (hottest <= lim.cht_high_f))) * tr.dt_s
        time_high += float(np.sum(hottest > lim.cht_high_f)) * tr.dt_s
        seg_status = "pass"
        seg_peak = nan_max(hottest)
        if np.isfinite(seg_peak) and seg_peak >= lim.cht_red_f:
            seg_status = "fail"
        elif float(np.sum(hottest > lim.cht_high_f)) * tr.dt_s > 0:
            seg_status = "fail"
        elif float(np.sum(hottest > lim.cht_yellow_f)) * tr.dt_s > 0:
            seg_status = "caution"
        cht_status = _worse(cht_status, seg_status)
        if seg_status in {"fail", "caution"}:
            hot_i = seg.i0 + int(np.nanargmax(np.where(np.isfinite(hottest), hottest, -np.inf)))
            _ev(events, tr, hot_i, seg_status, "climb_cht", f"Climb {n} CHT {seg_peak:.0f}°F", ["cht3_f", "cht1_f", "fuel_flow_gph"])
    _add(
        checks,
        "climb_tit",
        "climb",
        "Climb TIT",
        tit_status,
        "; ".join(tit_bits),
        f"{lim.climb_tit_lo_f:.0f}–{lim.climb_tit_hi_f:.0f} °F, not to exceed {lim.climb_tit_hi_f:.0f}",
    )
    _add(
        checks,
        "climb_cht",
        "climb",
        "Climb CHT",
        cht_status,
        f"peak {fmt_num(peak_cht, 0, '°F')}, {time_yellow:.0f}s in 380–410, {time_high:.0f}s above 410",
        f"hottest under {lim.cht_yellow_f:.0f}°F; 380–410 is yellow; {lim.cht_red_f:.0f} is red",
        "Above 380°F the AFMS says confirm full-rich fuel flow, boost as required, cowl flaps open, and a lower nose. Cowl flaps are not logged.",
    )
    metrics["peak_cht_f"] = None if not np.isfinite(peak_cht) else round(float(peak_cht), 1)
    metrics["climb_time_380_410_s"] = round(time_yellow, 0)
    metrics["climb_time_above_410_s"] = round(time_high, 0)


def _climb_ias(tr, lim, segs, checks, events) -> None:
    if not tr.ias_usable:
        why = "no IAS column" if tr.ias_note == "missing" else tr.ias_note
        _add(checks, "climb_ias", "climb", "Climb IAS", "skip", why, f"{lim.climb_ias_lo_kt:.0f}–{lim.climb_ias_hi_kt:.0f} KIAS")
        return
    bits = []
    status = "pass"
    for n, seg in enumerate(segs, 1):
        ias = tr.ias_kt[seg.i0 : seg.i1]
        # Only the part that is actually indicating, and not the takeoff rotation.
        good = ias[np.isfinite(ias) & (ias > 60) & (ias < 180)]
        if good.size < 5:
            bits.append(f"climb {n}: no IAS")
            continue
        med = float(np.median(good))
        bits.append(f"climb {n}: {med:.0f} kt")
        if med < lim.climb_ias_lo_kt - lim.climb_ias_tolerance_kt or med > lim.climb_ias_hi_kt + lim.climb_ias_tolerance_kt:
            status = "caution"
            _ev(events, tr, seg.i0, "caution", "climb_ias", f"Climb {n} IAS {med:.0f} kt", ["ias_kt"])
    _add(
        checks,
        "climb_ias",
        "climb",
        "Climb IAS",
        status,
        "; ".join(bits) or "n/a",
        f"{lim.climb_ias_lo_kt:.0f}–{lim.climb_ias_hi_kt:.0f} KIAS",
    )


def _climb_boost_vapor(tr, lim, segs, checks, events, metrics) -> None:
    mask = np.zeros(tr.n, dtype=bool)
    for seg in segs:
        mask[seg.i0 : seg.i1] = True
    steps = find_boost_steps(tr, mask, lim)
    vapor = find_vapor(tr, mask, lim)
    for step in steps:
        _ev(
            events,
            tr,
            step.i,
            "fail" if step.kind == "lo_ff_drop" else "info",
            f"boost_{step.kind}",
            _boost_text(step),
            ["fuel_flow_gph", "egt1_f", "map_inhg"],
        )
    for vap in vapor:
        _ev(events, tr, vap.i, "caution", f"vapor_{vap.kind}", f"Vapor signature: {vap.detail}", ["fuel_flow_gph", "tit1_f", "rpm", "map_inhg"])
    da_note = _boost_vs_da(tr, lim, segs, steps)
    kinds = [s.kind for s in steps]
    if "lo_ff_drop" in kinds:
        status = "fail"
    elif vapor:
        status = "caution"
    else:
        status = "pass"
    obs = _boost_obs(steps, vapor)
    _add(
        checks,
        "climb_vapor",
        "climb",
        "Climb vapor",
        "caution" if vapor else "pass",
        obs if vapor else "no fuel-flow wiggle, sag, or unexplained TIT rise",
        "steady fuel flow at steady RPM and MAP",
        "Wiggle is the residual around a 30 s median, so a smooth lean is not a wiggle.",
    )
    boost_status = "fail" if "lo_ff_drop" in kinds else "pass"
    if da_note.startswith("caution"):
        boost_status = _worse(boost_status, "caution")
    _add(
        checks,
        "climb_boost",
        "climb",
        "Climb boost",
        boost_status,
        obs,
        f"LO above {lim.boost_da_lo_ft:.0f} ft DA, HI above {lim.boost_da_hi_ft:.0f} ft DA; LO should not reduce fuel flow",
        da_note,
    )
    metrics["boost_lo"] = kinds.count("lo")
    metrics["boost_hi"] = kinds.count("hi")
    metrics["boost_lo_ff_drop"] = kinds.count("lo_ff_drop")
    metrics["vapor_events"] = len(vapor)


def _boost_text(step) -> str:
    name = {"lo": "LO boost", "hi": "HI boost", "lo_ff_drop": "LO selected, fuel flow fell"}[step.kind]
    return f"Inferred {name}: fuel flow {step.d_ff:+.1f} gph, EGT {step.d_egt:+.0f}°F, MAP {step.d_map:+.2f} in Hg"


def _boost_obs(steps, vapor) -> str:
    if not steps and not vapor:
        return "no boost step inferred"
    parts = [_boost_text(s) for s in steps]
    parts.extend(v.detail for v in vapor)
    return "; ".join(parts)


def _boost_vs_da(tr, lim, segs, steps) -> str:
    if tr.alt_ft is None:
        return "Density altitude not estimated (no altitude)."
    notes = []
    for step in steps:
        alt = tr.alt_ft[step.i]
        oat = tr.oat_c[step.i]
        if not (np.isfinite(alt) and np.isfinite(oat)):
            continue
        da = _density_alt(float(alt), float(oat))
        if step.kind == "hi" and da < lim.boost_da_hi_ft:
            notes.append(f"caution: HI boost inferred near {da:.0f} ft DA (AFMS HI is above {lim.boost_da_hi_ft:.0f})")
        if step.kind == "lo" and da < lim.boost_da_lo_ft - 500:
            notes.append(f"caution: LO boost inferred near {da:.0f} ft DA (AFMS LO is above {lim.boost_da_lo_ft:.0f})")
        if step.kind in {"lo", "hi"} and da >= (lim.boost_da_hi_ft if step.kind == "hi" else lim.boost_da_lo_ft):
            notes.append(f"{step.kind.upper()} boost near {da:.0f} ft DA, consistent with the AFMS")
    # Time spent in climb above the LO altitude with no LO/HI step is not itself a failure.
    above = 0.0
    for seg in segs:
        sl = slice(seg.i0, seg.i1)
        if tr.alt_ft is None:
            continue
        da = _density_alt(tr.alt_ft[sl], tr.oat_c[sl])
        above += float(np.sum(np.isfinite(da) & (da >= lim.boost_da_lo_ft))) * tr.dt_s
    if above > 120 and not any(s.kind in {"lo", "hi"} for s in steps):
        notes.append(
            f"{above/60:.0f} min of climb above {lim.boost_da_lo_ft:.0f} ft DA with no LO/HI step inferred. "
            "The pump may already have been on, or the step was smaller than the detector."
        )
    return " ".join(notes) if notes else "No boost step to compare with density altitude."


def _density_alt(alt_ft, oat_c):
    alt = np.asarray(alt_ft, dtype=np.float64)
    oat = np.asarray(oat_c, dtype=np.float64)
    isa = 15.0 - 1.98 * (alt / 1000.0)
    return alt + 118.8 * (oat - isa)


def _level(tr, lim, spans, checks, events, metrics) -> None:
    segs = [s for s in spans if s.name == "level_off"]
    if not segs:
        _add(checks, "level_off", "level_off", "Level-off before leaning", "skip", "no full-rich pull detected", "1–2 min at WOT, 2700 RPM, full rich, then 4–6 s lean")
        _add(checks, "lean_rate", "level_off", "Lean rate", "skip", "no mixture pull detected", "4–6 seconds")
        return
    hold_status = "pass"
    rate_status = "pass"
    hold_bits = []
    rate_bits = []
    for n, seg in enumerate(segs, 1):
        hold = _rich_hold_s(tr, lim, seg)
        hold_bits.append(f"segment {n}: {hold:.0f}s at 2700 / full rich")
        if hold < lim.level_off_min_s - 15:
            hold_status = "fail" if hold < 20 else "caution"
            _ev(events, tr, seg.i0, hold_status, "level_off", f"Full-rich level-off held {hold:.0f}s", ["rpm", "fuel_flow_gph", "map_inhg"])
        elif hold > lim.level_off_max_s + 30:
            hold_status = _worse(hold_status, "caution")
        slew = _lean_slew_s(tr, seg)
        if slew is None:
            rate_bits.append(f"segment {n}: lean not measured")
        else:
            rate_bits.append(f"segment {n}: {slew:.0f}s")
            if slew > lim.lean_max_s + 4:
                rate_status = "caution"
                _ev(events, tr, seg.i0, "caution", "lean_rate", f"Mixture pull took {slew:.0f}s", ["fuel_flow_gph", "tit1_f"])
            elif slew < lim.lean_min_s:
                rate_status = _worse(rate_status, "caution")
    _add(
        checks,
        "level_off",
        "level_off",
        "Level-off before leaning",
        hold_status,
        "; ".join(hold_bits),
        f"{lim.level_off_min_s:.0f}–{lim.level_off_max_s:.0f} s at WOT, 2700 RPM, full rich",
    )
    _add(
        checks,
        "lean_rate",
        "level_off",
        "Lean rate",
        rate_status,
        "; ".join(rate_bits),
        f"{lim.lean_min_s:.0f}–{lim.lean_max_s:.0f} s from full rich down through peak",
    )
    metrics["level_off_s"] = [round(_rich_hold_s(tr, lim, s), 0) for s in segs]


def _rich_hold_s(tr, lim, seg: Span) -> float:
    """Seconds of 2700 RPM and full-rich fuel flow at the end of the climb, before the pull."""
    i = seg.i0
    # Walk back into the climb as well. The painted level-off may start at the pull.
    # Stop at the previous capture: that climb belongs to another engine run.
    while i > 0 and tr.t[seg.i0] - tr.t[i] < lim.level_off_lookback_s + 30:
        if not _same_capture(tr, seg.i0, i - 1):
            break
        i -= 1
    sl = slice(i, seg.i1)
    rpm = tr.rpm[sl]
    ff = tr.ff[sl]
    mp = tr.map_inhg[sl]
    good = (rpm >= lim.climb_rpm_lo) & (ff >= lim.climb_ff_lo_gph) & (mp >= lim.climb_map_min)
    return float(np.sum(good)) * tr.dt_s


def _lean_slew_s(tr, seg: Span) -> float | None:
    ff = rolling_median_blocked(tr.ff, 3, tr.segment)
    # From the last sample above 28 gph to the first sample under 18, inside or just after the segment.
    i0 = seg.i0
    i1 = min(tr.n - 1, seg.i1 + int(30 / max(tr.dt_s, 0.5)))
    while i1 > i0 and not _same_capture(tr, i0, i1):
        i1 -= 1
    hi = None
    for i in range(i0, i1):
        if np.isfinite(ff[i]) and ff[i] >= 28.0:
            hi = i
    if hi is None:
        return None
    for j in range(hi, i1 + 1):
        if np.isfinite(ff[j]) and ff[j] <= 18.0:
            return float(tr.t[j] - tr.t[hi])
    return None


def _peak_tit(tr: Trace, lim: Limits) -> dict | None:
    tit = rolling_median_blocked(tr.tit, 5, tr.segment)
    if not np.isfinite(tit).any():
        return None
    finds = []
    i = 2
    n = tr.n
    while i < n - 2:
        if not _same_capture(tr, i, i - 1) or not _same_capture(tr, i, i + 1):
            i += 1
            continue
        if not np.isfinite(tit[i]) or tit[i] < tit[i - 1] or tit[i] < tit[i + 1] or tit[i] < 1500:
            i += 1
            continue
        left = i
        while (
            left > 0
            and _same_capture(tr, i, left - 1)
            and tr.t[i] - tr.t[left] < 180
            and (not np.isfinite(tit[left]) or tit[left] > tit[i] - 40)
        ):
            left -= 1
        right = i
        while (
            right < n - 1
            and _same_capture(tr, i, right + 1)
            and tr.t[right] - tr.t[i] < 180
            and (not np.isfinite(tit[right]) or tit[right] > tit[i] - 40)
        ):
            right += 1
        dur = float(tr.t[min(right, n - 1)] - tr.t[max(left, 0)])
        block = tr.ff[left : right + 1]
        if np.isfinite(block).any() and dur <= lim.lean_find_max_s + 30 and (nan_max(block) - nan_min(block)) >= 0.4:
            finds.append({"i": i, "tit_f": float(tit[i]), "duration_s": dur, "kind": "lean_find", "ff": float(tr.ff[i]) if np.isfinite(tr.ff[i]) else None})
        i += 3
    if finds:
        return max(finds, key=lambda p: p["tit_f"])
    i_max = int(np.nanargmax(tit))
    if not np.isfinite(tit[i_max]) or tit[i_max] < 1450:
        return None
    return {
        "i": i_max,
        "tit_f": float(tit[i_max]),
        "duration_s": None,
        "kind": "max_observed",
        "ff": float(tr.ff[i_max]) if np.isfinite(tr.ff[i_max]) else None,
    }


def _cruise(tr, lim, spans, peak, checks, events, metrics) -> None:
    segs = [s for s in spans if s.name == "cruise" and s.duration_s >= 60]
    if not segs:
        for item, title, limit in (
            ("cruise_power", "Cruise power", "WOT, 2500 or 2300 RPM, not below 2300"),
            ("cruise_mixture", "Cruise mixture", "≥ 60°F LOP, or ≥ 125°F ROP at ≤ 25 in Hg / 2500"),
            ("cruise_redbox", "Red box", "outside 50°F LOP to 125°F ROP when above 65%"),
            ("cruise_cht", "Cruise CHT", "hottest under 380°F once stable"),
            ("cruise_ff_stable", "Cruise fuel-flow stability", "stable for the first 30 min"),
            ("lean_find", "Lean find", "a peak sweep of 2 min or less"),
        ):
            _add(checks, item, "cruise", title, "skip", "no cruise segment", limit)
        return
    _cruise_power(tr, lim, segs, checks, events, metrics)
    _cruise_mixture(tr, lim, segs, peak, checks, events, metrics)
    _cruise_cht(tr, lim, segs, checks, events, metrics)
    _cruise_stable(tr, lim, segs, checks, events, metrics)


def _cruise_power(tr, lim, segs, checks, events, metrics) -> None:
    # The longest segment is the one the summary quotes. Every segment is scored.
    status = "pass"
    bits = []
    ff_obs = []
    rpm_obs = []
    for n, seg in enumerate(segs, 1):
        sl = slice(seg.i0, seg.i1)
        # Skip the first 20s so the lean is not still in the median.
        start = seg.i0 + int(20 / max(tr.dt_s, 0.5))
        if start >= seg.i1:
            start = seg.i0
        med_rpm = nan_median(tr.rpm[start:seg.i1])
        med_ff = nan_median(tr.ff[start:seg.i1])
        med_map = nan_median(tr.map_inhg[start:seg.i1])
        bits.append(f"segment {n}: {med_rpm:.0f} RPM, {med_map:.1f} in Hg, {med_ff:.1f} gph" if np.isfinite(med_ff) else f"segment {n}: n/a")
        ff_obs.append(med_ff)
        rpm_obs.append(med_rpm)
        if np.isfinite(med_rpm) and med_rpm < lim.cruise_rpm_floor - lim.cruise_rpm_floor_slack:
            low = float(np.sum(tr.rpm[start:seg.i1] < lim.cruise_rpm_floor - 50)) * tr.dt_s
            if low > 30:
                status = "fail"
                _ev(events, tr, start, "fail", "cruise_rpm", f"Cruise RPM {med_rpm:.0f}", ["rpm", "map_inhg", "fuel_flow_gph"])
        band, band_name = _ff_band(med_rpm, lim)
        if band and np.isfinite(med_ff):
            lo, hi = band
            if med_ff < lo - 1.0 or med_ff > hi + 1.5:
                status = _worse(status, "fail")
                _ev(events, tr, start, "fail", "cruise_ff", f"Cruise fuel flow {med_ff:.1f} gph ({band_name})", ["fuel_flow_gph", "rpm", "tit1_f"])
            elif med_ff < lo or med_ff > hi:
                status = _worse(status, "caution")
                _ev(events, tr, start, "caution", "cruise_ff", f"Cruise fuel flow {med_ff:.1f} gph, {band_name} band is {lo:.1f}–{hi:.1f}", ["fuel_flow_gph", "rpm"])
        wot_ref = nan_max(tr.map_inhg[tr.rpm > 2500]) if np.isfinite(tr.rpm).any() else float("nan")
        if np.isfinite(med_map) and np.isfinite(wot_ref) and med_map < wot_ref - lim.cruise_wot_map_slack_inhg - 1.0:
            status = _worse(status, "caution")
    _add(
        checks,
        "cruise_power",
        "cruise",
        "Cruise power",
        status,
        "; ".join(bits),
        "WOT at 2500 RPM / 15.5–17.5 gph, or 2300 RPM / 14.0–16.0 gph; not below 2300",
    )
    # Summary uses the longest cruise.
    longest = max(segs, key=lambda s: s.duration_s)
    start = longest.i0 + int(30 / max(tr.dt_s, 0.5))
    metrics["cruise_ff_gph"] = _r(nan_median(tr.ff[start:longest.i1]))
    metrics["cruise_rpm"] = _r(nan_median(tr.rpm[start:longest.i1]), 0)
    metrics["cruise_map"] = _r(nan_median(tr.map_inhg[start:longest.i1]))
    metrics["cruise_tit_f"] = _r(nan_median(tr.tit[start:longest.i1]), 0)


def _ff_band(rpm: float, lim: Limits) -> tuple[tuple[float, float] | None, str]:
    if not np.isfinite(rpm):
        return None, "RPM unknown"
    if rpm >= 2400:
        return tuple(lim.cruise_ff_2500), "2500 RPM"
    if rpm >= lim.cruise_rpm_floor - lim.cruise_rpm_floor_slack:
        return tuple(lim.cruise_ff_2300), "2300 RPM"
    return None, "below 2300 RPM"


def _cruise_mixture(tr, lim, segs, peak, checks, events, metrics) -> None:
    longest = max(segs, key=lambda s: s.duration_s)
    start = min(longest.i1 - 1, longest.i0 + int(45 / max(tr.dt_s, 0.5)))
    tit = nan_median(tr.tit[start:longest.i1])
    ff = nan_median(tr.ff[start:longest.i1])
    if peak is None or not np.isfinite(tit):
        _add(checks, "lean_find", "cruise", "Lean find", "skip", "no TIT peak", f"typically {lim.peak_tit_typical_lo_f:.0f}–{lim.peak_tit_typical_hi_f:.0f} °F, sweep ≤ {lim.lean_find_max_s:.0f}s")
        _add(checks, "cruise_mixture", "cruise", "Cruise mixture", "skip", "no TIT peak to measure against", "≥ 60°F LOP or ≥ 125°F ROP")
        _add(checks, "cruise_redbox", "cruise", "Red box", "skip", "no TIT peak", "not between 50°F LOP and 125°F ROP above 65% power")
        return
    depth = float(peak["tit_f"] - tit)  # positive when cruise TIT is under the peak
    side = _mixture_side(ff, peak.get("ff"), depth)
    kind = peak["kind"]
    find_status = "pass"
    if kind == "lean_find":
        dur = peak.get("duration_s") or 0.0
        find_obs = f"peak {peak['tit_f']:.0f}°F in {dur:.0f}s"
        if dur > lim.lean_find_max_s:
            find_status = "caution"
            _ev(events, tr, peak["i"], "caution", "lean_find", f"Lean find took {dur:.0f}s", ["tit1_f", "fuel_flow_gph"])
        if not (lim.peak_tit_typical_lo_f - 30 <= peak["tit_f"] <= lim.peak_tit_typical_hi_f + 20):
            find_status = _worse(find_status, "caution")
    else:
        find_status = "caution"
        find_obs = f"no sweep; maximum TIT {peak['tit_f']:.0f}°F used as the peak"
    _add(
        checks,
        "lean_find",
        "cruise",
        "Lean find",
        find_status,
        find_obs,
        f"peak typically {lim.peak_tit_typical_lo_f:.0f}–{lim.peak_tit_typical_hi_f:.0f} °F, sweep ≤ {lim.lean_find_max_s:.0f}s",
    )
    if side == "LOP":
        if depth >= lim.lop_min_f and depth <= lim.lop_ideal_hi_f:
            mix_status = "pass"
        elif depth >= lim.lop_ideal_lo_f:
            mix_status = "caution"
        else:
            mix_status = "fail" if depth < lim.redbox_lop_f else "caution"
        limit = f"at least {lim.lop_min_f:.0f}°F LOP, ideally {lim.lop_ideal_lo_f:.0f}–{lim.lop_ideal_hi_f:.0f}"
        obs = f"{depth:.0f}°F LOP (cruise TIT {tit:.0f}, peak {peak['tit_f']:.0f})"
    elif side == "ROP":
        rpm = nan_median(tr.rpm[start:longest.i1])
        mp = nan_median(tr.map_inhg[start:longest.i1])
        mix_status = "pass"
        if depth < lim.redbox_rop_f:
            mix_status = "fail"
        if (np.isfinite(mp) and mp > lim.rop_map_max_inhg + 0.3) or (np.isfinite(rpm) and rpm > lim.rop_rpm_max + 40):
            mix_status = "fail"
        limit = f"at least {lim.redbox_rop_f:.0f}°F ROP, and no more than {lim.rop_map_max_inhg:.0f} in Hg / {lim.rop_rpm_max:.0f} RPM"
        obs = f"{depth:.0f}°F ROP at {fmt_num(mp, 1, 'in Hg')} / {fmt_num(rpm, 0, 'RPM')}"
    else:
        mix_status = "caution"
        limit = "need a fuel flow at the peak to tell LOP from ROP"
        obs = f"cruise TIT {tit:.0f}°F, peak {peak['tit_f']:.0f}°F, side unknown"
    if kind == "max_observed":
        obs += " (peak is the maximum TIT, not a lean-find sweep)"
        mix_status = _worse(mix_status, "caution") if mix_status == "pass" else mix_status
    _add(checks, "cruise_mixture", "cruise", "Cruise mixture", mix_status, obs, limit)
    if mix_status in {"fail", "caution"}:
        _ev(events, tr, longest.i0, mix_status, "cruise_mixture", obs, ["tit1_f", "fuel_flow_gph"])

    power, power_src = _power_pct(tr, start, longest.i1, side, ff, lim)
    in_box = False
    if side == "LOP":
        in_box = depth < lim.redbox_lop_f
    elif side == "ROP":
        in_box = depth < lim.redbox_rop_f
    high_power = False
    if power is not None and power >= lim.redbox_power_pct:
        high_power = True
    elif side == "ROP":
        mp = nan_median(tr.map_inhg[start:longest.i1])
        high_power = bool(np.isfinite(mp) and mp >= lim.rop_map_max_inhg)
    if power is None and side != "ROP":
        box_status = "skip"
        box_obs = "power unknown"
    elif in_box and high_power:
        box_status = "fail"
        box_obs = f"inside the red box at {power:.0f}% " if power is not None else "inside the red box at high MAP"
        box_obs = box_obs.strip()
    elif in_box:
        box_status = "pass"
        box_obs = "inside 50 LOP–125 ROP but power is under 65%"
    else:
        box_status = "pass"
        box_obs = "outside the red box"
    if power is not None:
        box_obs += f" ({power:.0f}% via {power_src})"
    _add(
        checks,
        "cruise_redbox",
        "cruise",
        "Red box",
        box_status,
        box_obs,
        f"not between {lim.redbox_lop_f:.0f}°F LOP and {lim.redbox_rop_f:.0f}°F ROP above {lim.redbox_power_pct:.0f}% power",
    )
    if box_status == "fail":
        _ev(events, tr, longest.i0, "fail", "redbox", box_obs, ["tit1_f", "fuel_flow_gph", "map_inhg"])
    hp = None
    if side == "LOP" and np.isfinite(ff):
        hp = float(ff) * lim.lop_hp_per_gph
    metrics["peak_tit_f"] = round(float(peak["tit_f"]), 1)
    metrics["peak_tit_kind"] = kind
    metrics["lop_depth_f"] = round(depth, 1) if side == "LOP" else None
    metrics["rop_depth_f"] = round(depth, 1) if side == "ROP" else None
    metrics["mixture_side"] = side
    metrics["hp_est"] = None if hp is None else round(hp, 1)
    metrics["power_pct"] = None if power is None else round(float(power), 1)


def _mixture_side(cruise_ff: float, peak_ff: float | None, depth: float) -> str:
    if peak_ff is not None and np.isfinite(cruise_ff) and np.isfinite(peak_ff):
        if cruise_ff < peak_ff - 0.4:
            return "LOP"
        if cruise_ff > peak_ff + 0.4:
            return "ROP"
    if depth > 15:
        return "LOP"
    return "unknown"


def _power_pct(tr, i0, i1, side, ff, lim) -> tuple[float | None, str]:
    if tr.hp_pct is not None:
        med = nan_median(tr.hp_pct[i0:i1])
        if np.isfinite(med) and 20.0 < med <= 100.0:
            return float(med), "JPI percent power"
    if side == "LOP" and np.isfinite(ff):
        hp = float(ff) * lim.lop_hp_per_gph
        return hp / lim.rated_hp * 100.0, "fuel flow × 14.9"
    return None, ""


def _cruise_cht(tr, lim, segs, checks, events, metrics) -> None:
    status = "pass"
    bits = []
    offsets = []
    for n, seg in enumerate(segs, 1):
        start = min(seg.i1 - 1, seg.i0 + int(60 / max(tr.dt_s, 0.5)))
        hottest = _hottest(tr.cht[start:seg.i1])
        med = nan_median(hottest)
        bits.append(f"segment {n}: {med:.0f}°F" if np.isfinite(med) else f"segment {n}: n/a")
        if np.isfinite(med) and med > lim.cht_red_f:
            status = "fail"
        elif np.isfinite(med) and med > lim.cht_yellow_f:
            status = _worse(status, "fail" if med > lim.cht_high_f else "caution")
            _ev(events, tr, start, status, "cruise_cht", f"Cruise CHT {med:.0f}°F", ["cht3_f", "fuel_flow_gph"])
        off = _cht3_offset(tr.cht[start:seg.i1])
        if np.isfinite(off):
            offsets.append(off)
    _add(
        checks,
        "cruise_cht",
        "cruise",
        "Cruise CHT",
        status,
        "; ".join(bits),
        f"hottest under {lim.cht_yellow_f:.0f}°F once stable; AFMS remedy is lean another 0.25 gph",
    )
    metrics["cruise_cht_f"] = None if not bits else _r(nan_median(_hottest(tr.cht[_stable_cruise_slice(segs, tr)])), 0)
    metrics["cht3_offset_cruise_f"] = None if not offsets else round(float(np.median(offsets)), 1)


def _stable_cruise_slice(segs, tr):
    longest = max(segs, key=lambda s: s.duration_s)
    start = min(longest.i1 - 1, longest.i0 + int(60 / max(tr.dt_s, 0.5)))
    return slice(start, longest.i1)


def _cruise_stable(tr, lim, segs, checks, events, metrics) -> None:
    status = "pass"
    bits = []
    for n, seg in enumerate(segs, 1):
        window = int(lim.cruise_ff_stable_min * 60 / max(tr.dt_s, 0.5))
        i1 = min(seg.i1, seg.i0 + window)
        if i1 - seg.i0 < 10:
            continue
        resid = tr.ff[seg.i0:i1] - rolling_median(tr.ff[seg.i0:i1], max(5, int(30 / tr.dt_s)))
        sigma = float(np.nanstd(resid)) if np.isfinite(resid).any() else float("nan")
        bits.append(f"segment {n}: σ {sigma:.2f} gph" if np.isfinite(sigma) else f"segment {n}: n/a")
        if np.isfinite(sigma) and sigma >= lim.vapor_ff_std_gph:
            status = "caution"
            _ev(events, tr, seg.i0, "caution", "cruise_ff_stable", f"Cruise fuel flow σ {sigma:.2f} gph in the first 30 min", ["fuel_flow_gph", "rpm"])
    _add(
        checks,
        "cruise_ff_stable",
        "cruise",
        "Cruise fuel-flow stability",
        status if bits else "skip",
        "; ".join(bits) or "cruise shorter than the window",
        f"residual σ under {lim.vapor_ff_std_gph:.2f} gph for the first {lim.cruise_ff_stable_min:.0f} min (LO boost / vapor if it wanders)",
    )


def _tit_redline(tr, lim, checks, events) -> None:
    mx = nan_max(tr.tit)
    if not np.isfinite(mx):
        _add(checks, "tit_redline", "flight", "TIT redline", "skip", "no TIT", f"{lim.tit_red_f:.0f} °F")
        return
    status = "fail" if mx >= lim.tit_red_f else "pass"
    _add(checks, "tit_redline", "flight", "TIT redline", status, f"max {mx:.0f}°F", f"{lim.tit_red_f:.0f} °F")
    if status == "fail":
        i = int(np.nanargmax(tr.tit))
        _ev(events, tr, i, "fail", "tit_redline", f"TIT {mx:.0f}°F", ["tit1_f"])


def _descent(tr, lim, spans, peak, checks, events) -> None:
    segs = [s for s in spans if s.name == "descent" and s.duration_s >= 30]
    if not segs:
        _add(checks, "descent", "descent", "Descent", "skip", "no descent detected", "smooth power reduction, TIT near peak, CHT ≥ 240°F, no long idle")
        return
    status = "pass"
    bits = []
    for n, seg in enumerate(segs, 1):
        sl = slice(seg.i0, seg.i1)
        cht_min = nan_min(np.nanmin(tr.cht[sl], axis=1))
        # 10th percentile so one bad sample does not define the descent.
        cool = np.nanmin(tr.cht[sl], axis=1)
        cool = cool[np.isfinite(cool)]
        cht_p10 = float(np.percentile(cool, 10)) if cool.size else float("nan")
        rate = _cool_rate(tr, sl)
        idle_s = float(np.sum((tr.rpm[sl] < lim.descent_idle_rpm) & (tr.map_inhg[sl] < 15))) * tr.dt_s
        step = _max_map_drop(tr, seg)
        bit = f"segment {n}: CHT p10 {cht_p10:.0f}°F, cooling {rate:.0f}°F/min, idle {idle_s:.0f}s, MAP step {step:.1f}"
        bits.append(bit)
        if np.isfinite(cht_p10) and cht_p10 < lim.descent_cht_min_f:
            status = _worse(status, "caution")
            _ev(events, tr, seg.i0, "caution", "descent_cht", f"Descent CHT {cht_p10:.0f}°F", ["cht1_f", "cht3_f"])
        if np.isfinite(rate) and rate < -lim.cht_cool_caution_f_per_min:
            status = _worse(status, "caution")
            _ev(events, tr, seg.i0, "caution", "descent_cool", f"CHT cooling {rate:.0f}°F/min", ["cht3_f"])
        if idle_s > lim.descent_idle_max_s:
            status = _worse(status, "caution")
            _ev(events, tr, seg.i0, "caution", "descent_idle", f"Idle for {idle_s:.0f}s in the descent", ["rpm", "map_inhg"])
        if step >= lim.descent_map_step_inhg:
            status = _worse(status, "caution")
            _ev(events, tr, seg.i0, "caution", "descent_map", f"MAP step {step:.1f} in Hg", ["map_inhg", "rpm"])
        if peak is not None:
            med_tit = nan_median(tr.tit[sl])
            if np.isfinite(med_tit) and abs(peak["tit_f"] - med_tit) > lim.descent_tit_near_peak_f:
                status = _worse(status, "caution")
                bits[-1] += f", TIT {med_tit:.0f}°F ({abs(peak['tit_f'] - med_tit):.0f}°F from peak)"
    _add(
        checks,
        "descent",
        "descent",
        "Descent",
        status,
        "; ".join(bits),
        f"TIT within {lim.descent_tit_near_peak_f:.0f}°F of peak, CHT ≥ {lim.descent_cht_min_f:.0f}°F, cooling slower than {lim.cht_cool_caution_f_per_min:.0f}°F/min, no idle over {lim.descent_idle_max_s:.0f}s",
    )


def _cool_rate(tr, sl) -> float:
    hottest = _hottest(tr.cht[sl])
    t = tr.t[sl]
    if hottest.size < 5:
        return float("nan")
    sm = rolling_median(hottest, 5)
    span = 60.0
    worst = 0.0
    j = 0
    for i in range(sm.size):
        while j < i and t[i] - t[j] > span:
            j += 1
        if t[i] - t[j] < 30 or not np.isfinite(sm[i]) or not np.isfinite(sm[j]):
            continue
        rate = (float(sm[i]) - float(sm[j])) / float(t[i] - t[j]) * 60.0
        worst = min(worst, rate)
    return worst


def _max_map_drop(tr, seg: Span) -> float:
    mp = rolling_median_blocked(tr.map_inhg, 3, tr.segment)
    hop = max(1, int(round(4.0 / max(tr.dt_s, 0.5))))
    worst = 0.0
    for i in range(seg.i0, max(seg.i0, seg.i1 - hop)):
        if np.isfinite(mp[i]) and np.isfinite(mp[i + hop]):
            worst = max(worst, float(mp[i] - mp[i + hop]))
    return worst


def _landing(tr, lim, spans, checks, events, metrics) -> None:
    land = [s for s in spans if s.name == "landing"]
    cruise = [s for s in spans if s.name == "cruise" and s.duration_s >= 60]
    if not land:
        _add(checks, "landing_ff", "landing", "Approach fuel flow", "skip", "no landing segment", "1–2 gph above cruise")
        _add(checks, "taxi_after", "taxi", "Taxi leaning after landing", "skip", "no landing", f"under {lim.taxi_lean_ff_gph:.0f} gph")
        return
    seg = land[-1]
    cruise_ff = None
    if cruise:
        longest = max(cruise, key=lambda s: s.duration_s)
        cruise_ff = nan_median(tr.ff[longest.i0:longest.i1])
    # Approach is the airborne part of the landing segment.
    airborne = np.ones(seg.i1 - seg.i0, dtype=bool)
    if tr.gs_kt is not None:
        gs = tr.gs_kt[seg.i0:seg.i1]
        airborne = ~np.isfinite(gs) | (gs >= 40.0)
    ff = tr.ff[seg.i0:seg.i1][airborne]
    med = nan_median(ff)
    if cruise_ff is None or not np.isfinite(med):
        status = "skip"
        obs = "no cruise fuel flow to compare" if cruise_ff is None else "no approach fuel flow"
        limit = "1–2 gph above the cruise fuel flow"
    else:
        target_lo = cruise_ff + lim.approach_ff_above_cruise_lo
        target_hi = cruise_ff + lim.approach_ff_above_cruise_hi
        obs = f"{med:.1f} gph, cruise was {cruise_ff:.1f} (target {target_lo:.1f}–{target_hi:.1f})"
        limit = "1–2 gph above cruise"
        if target_lo - 1.0 <= med <= target_hi + 1.0:
            status = "pass"
        else:
            status = "caution"
            _ev(events, tr, seg.i0, "caution", "landing_ff", f"Approach fuel flow {med:.1f} gph vs cruise {cruise_ff:.1f}", ["fuel_flow_gph", "rpm"])
    _add(checks, "landing_ff", "landing", "Approach fuel flow", status, obs, limit)
    # After touchdown: ground samples to the end, or the following taxi span.
    after = np.arange(seg.i1, tr.n)
    if after.size < 5:
        after = np.arange(seg.i0, seg.i1)
        if tr.gs_kt is not None:
            gs = tr.gs_kt[after]
            after = after[np.isfinite(gs) & (gs < 30)]
    if after.size < 5:
        _add(checks, "taxi_after", "taxi", "Taxi leaning after landing", "skip", "recording ends at touchdown", f"under {lim.taxi_lean_ff_gph:.0f} gph")
        return
    med_after = nan_median(tr.ff[after])
    # On the ground a leaned mixture is a low fuel flow regardless of the taxi RPM band.
    if np.isfinite(med_after) and med_after <= lim.taxi_lean_ff_gph + 2.0:
        after_status = "pass"
    elif np.isfinite(med_after):
        after_status = "caution"
    else:
        after_status = "skip"
    _add(
        checks,
        "taxi_after",
        "taxi",
        "Taxi leaning after landing",
        after_status,
        f"median {fmt_num(med_after, 1, 'gph')} at {fmt_num(nan_median(tr.rpm[after]), 0, 'RPM')}",
        f"leaned, around {lim.taxi_lean_ff_gph:.0f} gph or below",
    )
    metrics["approach_ff_gph"] = None if not np.isfinite(med) else round(float(med), 2)


def _shutdown(tr, lim, spans, checks, events) -> None:
    # High-power taxi: ground RPM above the threshold after the last landing, or any late taxi.
    ground = np.zeros(tr.n, dtype=bool)
    for seg in spans:
        if seg.name in {"taxi", "ground", "shutdown", "landing"}:
            ground[seg.i0:seg.i1] = True
    if tr.gs_kt is not None:
        ground = ground & (np.isnan(tr.gs_kt) | (tr.gs_kt < 30))
    high = ground & (tr.rpm >= lim.high_power_ground_rpm)
    if not high.any():
        _add(checks, "cooldown", "shutdown", "Turbo cooldown", "skip", "no high-power taxi", f"{lim.cooldown_min_s:.0f}–{lim.cooldown_max_s:.0f}s at idle afterward")
        return
    # Find an idle stretch after the last high-power ground sample.
    last_high = int(np.flatnonzero(high)[-1])
    idle = (
        (np.arange(tr.n) > last_high)
        & (tr.rpm >= lim.cooldown_idle_lo_rpm)
        & (tr.rpm <= lim.cooldown_idle_hi_rpm)
    )
    idle_s = _longest_true(tr.t, idle)
    end_rpm = nan_median(tr.rpm[-5:])
    if idle_s >= lim.cooldown_min_s:
        status = "pass" if idle_s <= lim.cooldown_max_s + 15 else "caution"
        obs = f"{idle_s:.0f}s at idle after the high-RPM taxi"
    else:
        status = "caution"
        obs = f"no {lim.cooldown_min_s:.0f}s idle; recording ends near {end_rpm:.0f} RPM" if np.isfinite(end_rpm) else "no idle cooldown"
        _ev(events, tr, last_high, "caution", "cooldown", obs, ["rpm", "oil_temp_f"])
    _add(
        checks,
        "cooldown",
        "shutdown",
        "Turbo cooldown",
        status,
        obs,
        f"{lim.cooldown_min_s:.0f}–{lim.cooldown_max_s:.0f} s at {lim.cooldown_idle_lo_rpm:.0f}–{lim.cooldown_idle_hi_rpm:.0f} RPM after a high-power taxi",
    )


def _longest_true(t: np.ndarray, mask: np.ndarray) -> float:
    best = 0.0
    i = 0
    n = mask.size
    while i < n:
        if not mask[i]:
            i += 1
            continue
        j = i
        while j < n and mask[j]:
            j += 1
        best = max(best, float(t[j - 1] - t[i]))
        i = j
    return best


def _oil(tr, lim, labels, checks, events, metrics) -> None:
    status = "pass"
    bits = []
    worst_p = float("inf")
    worst_t = float("-inf")
    for name in ("takeoff", "climb", "cruise", "descent", "landing", "taxi"):
        idx = np.flatnonzero(labels == name)
        if idx.size < 3:
            continue
        p = tr.oil_press[idx]
        temp = tr.oil_temp_f[idx]
        pmin = nan_min(p)
        tmax = nan_max(temp)
        if np.isfinite(pmin):
            worst_p = min(worst_p, pmin)
        if np.isfinite(tmax):
            worst_t = max(worst_t, tmax)
        bits.append(f"{name}: P min {pmin:.0f} / T max {tmax:.0f}" if np.isfinite(pmin) and np.isfinite(tmax) else name)
        in_flight = name in {"takeoff", "climb", "cruise", "descent"}
        if np.isfinite(pmin) and (pmin < lim.oil_p_red_min or nan_max(p) > lim.oil_p_red_max):
            status = "fail"
            _ev(events, tr, int(idx[int(np.nanargmin(p))]), "fail", "oil_press", f"{name} oil pressure {pmin:.0f} psi", ["oil_press_psi", "rpm"])
        elif in_flight and np.isfinite(pmin) and pmin < lim.oil_p_yellow_hi:
            # A one-sample dip into the yellow arc is a caution, not the whole flight.
            low = float(np.sum(p < lim.oil_p_yellow_hi)) * tr.dt_s
            if low >= 8.0:
                status = _worse(status, "caution")
                _ev(events, tr, int(idx[0]), "caution", "oil_press", f"{name} oil pressure down to {pmin:.0f} psi", ["oil_press_psi", "rpm"])
        if np.isfinite(tmax) and tmax > lim.oil_t_red_f:
            status = "fail"
            _ev(events, tr, int(idx[int(np.nanargmax(temp))]), "fail", "oil_temp", f"{name} oil temp {tmax:.0f}°F", ["oil_temp_f"])
        elif np.isfinite(tmax) and tmax >= lim.oil_t_caution_f:
            status = _worse(status, "caution")
            _ev(events, tr, int(idx[int(np.nanargmax(temp))]), "caution", "oil_temp", f"{name} oil temp {tmax:.0f}°F", ["oil_temp_f", "rpm"])
        # High RPM with yellow-arc oil pressure, including on the ground.
        if name in {"taxi", "landing"}:
            both = (tr.rpm[idx] >= lim.high_power_ground_rpm) & (p < lim.oil_p_yellow_hi)
            if int(np.nansum(both)) * tr.dt_s >= 8.0:
                status = _worse(status, "caution")
                _ev(events, tr, int(idx[np.flatnonzero(both)[0]]), "caution", "oil_press_high_rpm", f"Oil pressure {nan_min(p[both]):.0f} psi at high RPM on the ground", ["oil_press_psi", "rpm"])
    _add(
        checks,
        "oil",
        "flight",
        "Oil",
        status,
        "; ".join(bits) if bits else "no samples",
        f"pressure red < {lim.oil_p_red_min:.0f} and > {lim.oil_p_red_max:.0f}, yellow {lim.oil_p_red_min:.0f}–{lim.oil_p_yellow_hi:.0f}, green to {lim.oil_p_green_hi:.0f}; temp red {lim.oil_t_red_f:.0f}°F, green {lim.oil_t_green_lo_f:.0f}–{lim.oil_t_red_f:.0f}",
    )
    # Wastegate: rated MAP wants about 45 psi once above the altitude gate, at climb RPM.
    sag = None
    if tr.alt_ft is not None:
        gate = (
            (tr.alt_ft >= lim.wastegate_alt_ft)
            & (tr.rpm >= lim.climb_rpm_min)
            & (tr.map_inhg < lim.wastegate_map_min_inhg)
            & (tr.oil_press < lim.wastegate_oil_psi)
        )
        if gate.any():
            i = int(np.flatnonzero(gate)[0])
            sag = i
    if sag is not None:
        wg_status = "caution"
        wg_obs = (
            f"MAP {tr.map_inhg[sag]:.1f} in Hg with oil {tr.oil_press[sag]:.0f} psi "
            f"at {tr.alt_ft[sag]:.0f} ft"
        )
        _ev(events, tr, sag, "caution", "wastegate", wg_obs, ["map_inhg", "oil_press_psi", "rpm"])
    else:
        wg_status = "pass" if tr.alt_ft is not None else "skip"
        wg_obs = "no MAP sag with oil pressure under 45 psi" if tr.alt_ft is not None else "no altitude"
    _add(
        checks,
        "wastegate",
        "climb",
        "Wastegate oil pressure",
        wg_status,
        wg_obs,
        f"about {lim.wastegate_oil_psi:.0f} psi to hold MAP at or above {lim.wastegate_map_min_inhg:.0f} in Hg above {lim.wastegate_alt_ft:.0f} ft",
    )
    metrics["oil_press_min"] = None if worst_p == float("inf") else round(float(worst_p), 1)
    metrics["oil_temp_max_f"] = None if worst_t == float("-inf") else round(float(worst_t), 1)


def _cylinders(tr, lim, labels, checks, events, metrics) -> None:
    bits = []
    status = "pass"
    climb_off = []
    cruise_off = []
    for name in ("climb", "cruise", "takeoff", "descent"):
        idx = np.flatnonzero(labels == name)
        if idx.size < 8:
            continue
        cht_med = np.array([nan_median(tr.cht[idx, c]) for c in range(6)])
        egt_med = np.array([nan_median(tr.egt[idx, c]) for c in range(6)])
        cht_spread = nan_max(cht_med) - nan_min(cht_med) if np.isfinite(cht_med).sum() >= 3 else float("nan")
        egt_spread = nan_max(egt_med) - nan_min(egt_med) if np.isfinite(egt_med).sum() >= 3 else float("nan")
        off = _cht3_offset(tr.cht[idx])
        bits.append(
            f"{name}: CHT spread {cht_spread:.0f}°F, EGT spread {egt_spread:.0f}°F, CHT3 {off:+.0f}°F"
            if np.isfinite(cht_spread) and np.isfinite(off)
            else name
        )
        if np.isfinite(cht_spread) and cht_spread > lim.cht_spread_caution_f:
            status = _worse(status, "caution")
        if np.isfinite(egt_spread) and egt_spread > lim.egt_spread_caution_f:
            status = _worse(status, "caution")
        if name == "climb" and np.isfinite(off):
            climb_off.append(off)
        if name == "cruise" and np.isfinite(off):
            cruise_off.append(off)
    _add(
        checks,
        "cyl_spread",
        "flight",
        "Cylinder spread",
        status if bits else "skip",
        "; ".join(bits) or "not enough samples",
        f"CHT spread under {lim.cht_spread_caution_f:.0f}°F, EGT spread under {lim.egt_spread_caution_f:.0f}°F",
    )
    off_c = float(np.median(climb_off)) if climb_off else float("nan")
    off_r = float(np.median(cruise_off)) if cruise_off else float("nan")
    wiggle = _cht3_wiggle(tr, labels)
    cht3_status = "pass"
    if (np.isfinite(off_c) and abs(off_c) > lim.cht3_offset_caution_f) or (
        np.isfinite(off_r) and abs(off_r) > lim.cht3_offset_caution_f
    ):
        cht3_status = "caution"
    if np.isfinite(wiggle) and wiggle > lim.cht3_wiggle_caution_f:
        cht3_status = _worse(cht3_status, "caution")
    usual = ""
    if np.isfinite(off_r) and 10 <= off_r <= 22:
        usual = " Cruise offset is in the usual +14 to +18°F neighborhood."
    _add(
        checks,
        "cht3",
        "flight",
        "CHT3 offset",
        cht3_status if np.isfinite(off_c) or np.isfinite(off_r) else "skip",
        f"climb {fmt_num(off_c, 1, '°F')}, cruise {fmt_num(off_r, 1, '°F')}, wiggle {fmt_num(wiggle, 1, '°F')}",
        f"report the offset (typically +14 to +18°F, wiggle 3–6°F); caution above {lim.cht3_offset_caution_f:.0f}°F or wiggle above {lim.cht3_wiggle_caution_f:.0f}°F",
        "CHT3 minus the mean of the other cylinders." + usual,
    )
    noisy = _noisy_probes(tr, lim)
    _add(
        checks,
        "probe_noise",
        "flight",
        "Probe noise",
        "caution" if noisy else "pass",
        ", ".join(noisy) if noisy else "no channel is several times noisier than the others",
        f"wiggle under {lim.probe_wiggle_ratio:.0f}× the family median and under {lim.probe_wiggle_min_f:.0f}°F",
    )
    if noisy:
        _ev(events, tr, tr.n // 2, "caution", "probe_noise", "Noisy probe: " + ", ".join(noisy), ["cht3_f", "egt1_f"])
    metrics["cht3_offset_climb_f"] = None if not np.isfinite(off_c) else round(off_c, 1)
    metrics["cht3_offset_cruise_f"] = None if not np.isfinite(off_r) else round(off_r, 1)
    metrics["cht3_wiggle_f"] = None if not np.isfinite(wiggle) else round(float(wiggle), 2)
    # Whole-flight time above 380, for the summary.
    hottest = _hottest(tr.cht)
    metrics["time_above_380_s"] = round(float(np.sum(hottest > lim.cht_yellow_f)) * tr.dt_s, 0)
    if "peak_cht_f" not in metrics or metrics.get("peak_cht_f") is None:
        metrics["peak_cht_f"] = _r(nan_max(hottest), 1)


def _cht3_offset(cht: np.ndarray) -> float:
    if cht.ndim != 2 or cht.shape[1] < 6:
        return float("nan")
    others = np.delete(cht, 2, axis=1)
    with np.errstate(all="ignore"):
        base = np.nanmean(others, axis=1)
    delta = cht[:, 2] - base
    return nan_median(delta)


def _cht3_wiggle(tr: Trace, labels) -> float:
    idx = np.flatnonzero((labels == "cruise") | (labels == "climb"))
    if idx.size < 20:
        idx = np.arange(tr.n)

    def one(series: np.ndarray) -> float:
        if series.size < 11 or not np.isfinite(series).any():
            return float("nan")
        resid = series - rolling_median(series, 11)
        return float(np.nanstd(resid)) if np.isfinite(resid).sum() > 10 else float("nan")

    if tr.segment is None or int(np.max(tr.segment)) == 0:
        return one(tr.cht[idx, 2])
    scores = []
    for seg_id in range(int(np.max(tr.segment)) + 1):
        sub = idx[tr.segment[idx] == seg_id]
        score = one(tr.cht[sub, 2]) if sub.size else float("nan")
        if np.isfinite(score):
            scores.append(score)
    return float(max(scores)) if scores else float("nan")


def _noisy_probes(tr: Trace, lim: Limits) -> list[str]:
    found = []
    for stem, block in (("cht", tr.cht), ("egt", tr.egt)):
        wig = []
        for c in range(6):
            resid = block[:, c] - rolling_median_blocked(block[:, c], 11, tr.segment)
            wig.append(float(np.nanstd(resid)) if np.isfinite(resid).sum() > 10 else float("nan"))
        med = nan_median(wig)
        for c, w in enumerate(wig, 1):
            if np.isfinite(w) and np.isfinite(med) and w >= lim.probe_wiggle_min_f and w >= lim.probe_wiggle_ratio * max(med, 0.5):
                found.append(f"{stem}{c} σ {w:.1f}°F")
    return found


def _hottest(cht: np.ndarray) -> np.ndarray:
    if cht.size == 0:
        return cht
    with np.errstate(all="ignore"):
        return np.nanmax(cht, axis=1)


def _idx(spans: list[Span], name: str) -> np.ndarray:
    parts = [np.arange(s.i0, s.i1) for s in spans if s.name == name]
    if not parts:
        return np.array([], dtype=int)
    return np.concatenate(parts)


def _first(spans: list[Span], name: str) -> int | None:
    for s in spans:
        if s.name == name:
            return s.i0
    return None


def _span_json(tr: Trace, s: Span) -> dict:
    return {
        "name": s.name,
        "t0_ns": int(tr.t_ns[s.i0]),
        "t1_ns": int(tr.t_ns[min(s.i1, tr.n) - 1] if s.i1 > s.i0 else tr.t_ns[s.i0]),
        "t0_iso": _iso(s.t0),
        "t1_iso": _iso(s.t1),
        "duration_s": round(s.duration_s, 1),
    }


def _add(checks, id_, phase, title, status, observed, limit, detail="") -> None:
    checks.append(
        {
            "id": id_,
            "phase": phase,
            "title": title,
            "status": status,
            "observed": observed,
            "limit": limit,
            "detail": detail,
        }
    )


def _ev(events, tr: Trace, i: int, severity: str, code: str, message: str, columns: list[str]) -> None:
    i = int(max(0, min(tr.n - 1, i)))
    # Collapse repeats of the same code within 2 minutes.
    t = float(tr.t[i])
    for prev in events:
        if prev["code"] == code and abs(prev["t_unix"] - t) < 120:
            if _RANK.get(severity, 0) > _RANK.get(prev["severity"], 0):
                prev["severity"] = severity
                prev["message"] = message
            return
    pad = 60.0
    events.append(
        {
            "t_ns": int(tr.t_ns[i]),
            "t_unix": t,
            "t_iso": _iso(t),
            "severity": severity,
            "code": code,
            "message": message,
            "t0_ns": int((t - pad) * 1e9),
            "t1_ns": int((t + pad) * 1e9),
            "series": [{"table": tr.series_table, "column": c} for c in columns],
        }
    )


def _iso(t: float) -> str:
    return datetime.fromtimestamp(float(t), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _worse(a: str, b: str) -> str:
    return a if _RANK.get(a, 0) >= _RANK.get(b, 0) else b


def _overall(checks: list[dict]) -> str:
    statuses = [c["status"] for c in checks if c["status"] != "skip"]
    if not statuses:
        return "skip"
    if "fail" in statuses:
        return "fail"
    if "caution" in statuses:
        return "caution"
    return "pass"


def _counts(checks: list[dict]) -> dict:
    out = {"pass": 0, "caution": 0, "fail": 0, "skip": 0}
    for c in checks:
        out[c["status"]] = out.get(c["status"], 0) + 1
    return out


def _r(v, digits: int = 2):
    if v is None or not isinstance(v, (int, float)) or not math.isfinite(float(v)):
        return None
    return round(float(v), digits)
