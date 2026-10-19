"""Taxi magnetometer calibration and motor-like transient detection.

On the ground the GPS track is the aircraft heading (no wind correction).
With pitch and roll nearly fixed, yawing the airplane swings the Earth field
through a circle in the sensor frame. That identifies:

- a 3×3 map ``m ≈ A @ [cos ψ, sin ψ, 1]`` and, from it, a heading
- horizontal scale along the two swing axes (soft iron / gain)
- a hard-iron offset, once the World Magnetic Model vertical component is
  taken out along the circle normal

It does not identify a general 3D soft-iron matrix. A circle is not an
ellipsoid. Vertical scale is assumed equal to the mean horizontal scale, and
that assumption is checked by asking whether ``|m − b|`` stays constant in
flight, where attitude is no longer level.

``ψ`` is magnetic heading: true track minus ``geo.declination`` (east-positive).
"""

from __future__ import annotations

import sqlite3
from typing import Any

import numpy as np
import pandas as pd

from analysis.geom import MPS_TO_KT, circ_rms_deg, occupied_span_deg, wrap_deg

# gps.gs is m/s. 4–23 m/s is about 8–45 kt: track is usable, and the
# airplane is still on the ground, so wind correction is negligible.
TAXI_GS_LO = 4.0
TAXI_GS_HI = 23.0
# Blip and step (a field that leaves and comes back, or a new offset) are
# only meaningful when the nose is not swinging. Above a walk, gpsd's missing
# track must not count as "steady": a few degrees of heading moves the field
# by more than STEP_UT. Airborne attitude changes are not motors either.
PARKED_GS = 2.5
AIR_GS = 30.0
STRAIGHT_RATE_DEG_S = 1.0
# Unexplained |dm/dt| above this, after subtracting a pure rotation of the
# Earth field, is treated as the field itself changing.
# Above the 1 Hz noise floor. On the two reference flights the median excess
# over a pure rotation is under 1 µT/s and flight p99 is about 2.6, so 0.8
# was flagging ordinary taxi. 4 µT/s is a clear departure.
RATE_UT_S = 4.0
# 1 Hz samples wander by about 1.5 µT while the airplane is parked. Below
# this, a "blip" is that wander. A flap or gear motor is a few µT clear of it.
BLIP_UT = 2.5
STEP_UT = 2.5
# A flap or gear motor is a few µT. Larger than this is hangar steel, the
# pod being moved, or a cluster of bad samples.
LARGE_UT = 8.0
# A single MMC5983 sample this far from the Earth-scale field is a glitch.
MAG_ABS_MAX = 200.0
MOTOR_PEAK_MAX = 15.0


def _col(df: pd.DataFrame, name: str) -> np.ndarray:
    if name not in df.columns:
        return np.full(len(df), np.nan)
    return df[name].to_numpy(dtype=float)


def _mag(df: pd.DataFrame) -> np.ndarray:
    return np.column_stack([_col(df, "mag_x_ut"), _col(df, "mag_y_ut"), _col(df, "mag_z_ut")])


def _gyro(df: pd.DataFrame) -> np.ndarray:
    return np.column_stack(
        [_col(df, "anglvel_x"), _col(df, "anglvel_y"), _col(df, "anglvel_z")]
    )


def heading_from_A(mag: np.ndarray, A: np.ndarray) -> np.ndarray:
    """Magnetic heading (degrees) implied by ``m ≈ A @ [cos ψ, sin ψ, 1]``."""
    c1 = A[:, 0]
    c2 = A[:, 1]
    c0 = A[:, 2]
    p = mag - c0
    G = np.column_stack([c1, c2])
    GtG = G.T @ G
    try:
        inv = np.linalg.inv(GtG)
    except np.linalg.LinAlgError:
        return np.full(len(mag), np.nan)
    cs = (p @ G) @ inv.T
    return np.rad2deg(np.arctan2(cs[:, 1], cs[:, 0])) % 360.0


def fit_swing(mag: np.ndarray, psi_deg: np.ndarray) -> dict[str, Any] | None:
    """Least squares ``m ≈ A @ [cos ψ, sin ψ, 1]``. ``A`` is 3×3."""
    m = np.isfinite(mag).all(axis=1) & np.isfinite(psi_deg)
    mag = mag[m]
    psi = np.asarray(psi_deg, dtype=float)[m]
    if len(psi) < 12:
        return None
    rad = np.deg2rad(psi)
    X = np.column_stack([np.cos(rad), np.sin(rad), np.ones(len(psi))])
    coef, _, _, sv = np.linalg.lstsq(X, mag, rcond=None)
    A = coef.T
    pred = X @ coef
    resid = mag - pred
    rms = float(np.sqrt(np.mean(np.sum(resid * resid, axis=1))))
    cond = float(sv[0] / sv[-1]) if len(sv) and sv[-1] > 0 else float("inf")
    return {"A": A, "n": int(len(psi)), "residual_ut_rms": rms, "condition": cond}


def _held_out_heading(mag: np.ndarray, psi: np.ndarray) -> float | None:
    """RMS of heading error on 45° sectors that were left out of the fit."""
    bins = np.floor((psi % 360.0) / 45.0).astype(int) % 8
    err: list[np.ndarray] = []
    for b in range(8):
        test = bins == b
        train = ~test
        if int(test.sum()) < 8 or int(train.sum()) < 24:
            continue
        fit = fit_swing(mag[train], psi[train])
        if fit is None:
            continue
        pred = heading_from_A(mag[test], fit["A"])
        err.append(wrap_deg(pred - psi[test]))
    if not err:
        return None
    return circ_rms_deg(np.concatenate(err))


def _hard_iron(
    A: np.ndarray,
    H: float,
    Z: float,
    mag: np.ndarray,
    psi: np.ndarray,
) -> dict[str, Any]:
    """Separate offset from the vertical Earth field.

    Model, aircraft FRD, magnetic heading ψ, then a scaled rotation into the
    sensor::

        B = [H cos ψ, −H sin ψ, Z]
        m = s R B + b

    so the regression columns are ``s H R[:,0]``, ``−s H R[:,1]`` and
    ``s Z R[:,2] + b``. Horizontal scales that differ, or axes that are not
    orthogonal, are soft iron the yaw swing can see. Vertical scale is taken
    to be the mean of the two horizontal scales.
    """
    c1, c2, c0 = A[:, 0], A[:, 1], A[:, 2]
    M = np.column_stack([c1 / H, -c2 / H])
    _U, S, Vt = np.linalg.svd(M, full_matrices=True)
    # Nearest rotation of the two swing axes. Singular values are the scales.
    R2 = _U[:, :2] @ Vt
    r3 = np.cross(R2[:, 0], R2[:, 1])
    sx = float(S[0])
    sy = float(S[1]) if len(S) > 1 else sx
    s = 0.5 * (sx + sy)
    b = c0 - r3 * (s * Z)
    R = np.column_stack([R2, r3])
    if np.linalg.det(R) < 0:
        r3 = -r3
        R = np.column_stack([R2, r3])
        b = c0 - r3 * (s * Z)

    def _sphere_rms(samples: np.ndarray) -> float | None:
        ok = np.isfinite(samples).all(axis=1)
        if int(ok.sum()) < 8 or s <= 0:
            return None
        rad = np.linalg.norm(samples[ok] - b, axis=1)
        target = s * float(np.hypot(H, Z))
        return float(np.sqrt(np.mean((rad - target) ** 2)))

    axis_dot = float(np.dot(c1, c2) / (np.linalg.norm(c1) * np.linalg.norm(c2) + 1e-12))
    axis_angle = float(np.rad2deg(np.arccos(np.clip(axis_dot, -1.0, 1.0))))
    return {
        "scale_x": sx,
        "scale_y": sy,
        "scale_iso": float(s),
        "axis_angle_deg": axis_angle,
        "swing_radius_ut": float(0.5 * (np.linalg.norm(c1) + np.linalg.norm(c2))),
        "circle_center_ut": [float(v) for v in c0],
        "hard_iron_ut": [float(v) for v in b],
        "R_sensor_from_aircraft": R.tolist(),
        "sphere_rms_taxi_ut": _sphere_rms(mag),
        "field_sF_ut": float(s * np.hypot(H, Z)),
        "_b": b,
        "_s": float(s),
    }


def _plausible(mag: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(mag, axis=1)
    return np.isfinite(mag).all(axis=1) & (n > 5.0) & (n < MAG_ABS_MAX)


def _best_lag(
    mag: np.ndarray, psi: np.ndarray, mask: np.ndarray
) -> tuple[int, dict[str, Any] | None, np.ndarray, np.ndarray]:
    """Integer-second shift of track relative to mag that tightens the swing.

    A positive lag compares each mag sample with the track that many seconds
    later (the track timestamp is behind the magnetometer).
    """
    cands = []
    for lag in range(-2, 3):
        idx = np.flatnonzero(mask)
        if lag > 0:
            idx = idx[idx + lag < len(psi)]
        elif lag < 0:
            idx = idx[idx + lag >= 0]
        if len(idx) < 12:
            continue
        fit = fit_swing(mag[idx], psi[idx + lag])
        if fit is None:
            continue
        cands.append((lag, fit, idx))
    if not cands:
        return 0, None, np.array([], dtype=int), np.array([])
    best = min(cands, key=lambda c: c[1]["residual_ut_rms"])
    zero = next((c for c in cands if c[0] == 0), None)
    # Don't advertise a lag that barely matters.
    if zero is not None and zero[1]["residual_ut_rms"] <= best[1]["residual_ut_rms"] * 1.02:
        best = zero
    lag, fit, idx = best
    return lag, fit, idx, psi[idx + lag]


def analyze_mag(df: pd.DataFrame, meta: dict[str, Any]) -> dict[str, Any]:
    """Taxi swing fit plus transients on the whole 1 Hz series."""
    empty: dict[str, Any] = {"ok": False, "reason": "no magnetometer"}
    if "mag_x_ut" not in df.columns or len(df) == 0:
        return empty
    mag = _mag(df)
    gs = _col(df, "gs")
    track = _col(df, "track")
    decl = meta.get("declination_deg")
    if decl is None:
        psi = track.copy()
        decl_note = "no geo declination; heading compared to true track"
    else:
        psi = (track - float(decl)) % 360.0
        decl_note = None

    plausible = _plausible(mag)
    taxi = (
        np.isfinite(gs)
        & (gs >= TAXI_GS_LO)
        & (gs <= TAXI_GS_HI)
        & np.isfinite(track)
        & plausible
    )
    n_taxi = int(taxi.sum())
    span = occupied_span_deg(psi[taxi]) if n_taxi else 0.0
    out: dict[str, Any] = {
        "ok": False,
        "taxi_s": n_taxi,
        "heading_span_deg": span,
        "gs_band_mps": [TAXI_GS_LO, TAXI_GS_HI],
        "gs_band_kt": [TAXI_GS_LO * MPS_TO_KT, TAXI_GS_HI * MPS_TO_KT],
        "notes": [],
    }
    if decl_note:
        out["notes"].append(decl_note)
    if n_taxi < 12:
        out["reason"] = "not enough taxi with a valid track"
        out["transients"] = _transients_only(df, mag, None)
        return out

    lag, fit, idx, psi_fit = _best_lag(mag, psi, taxi)
    if fit is None:
        out["reason"] = "swing fit failed"
        out["transients"] = _transients_only(df, mag, None)
        return out
    A = fit["A"]
    pred = heading_from_A(mag[idx], A)
    in_rms = circ_rms_deg(wrap_deg(pred - psi_fit))
    held = _held_out_heading(mag[idx], psi_fit)
    out["gps_lag_s"] = int(lag)
    out.update(
        {
            "ok": True,
            "n_fit": fit["n"],
            "residual_ut_rms": fit["residual_ut_rms"],
            "condition": fit["condition"],
            "in_sample_heading_rms_deg": in_rms,
            "heldout_heading_rms_deg": held,
            "A": A.tolist(),
        }
    )
    H = meta.get("wmm_h_ut")
    Z = meta.get("wmm_z_ut")
    bvec = None
    s_iso = None
    field_ut = meta.get("wmm_f_ut") or 50.0
    if H and Z and H > 5.0 and abs(Z) > 5.0:
        hard = _hard_iron(A, float(H), float(Z), mag[idx], psi_fit)
        bvec = hard.pop("_b")
        s_iso = hard.pop("_s")
        # Flight (and the rest of the recording) should lie on the same sphere
        # if the offset really is hard iron and the scale is isotropic.
        flight = np.isfinite(gs) & (gs >= 36.0) & np.isfinite(mag).all(axis=1)
        hard["sphere_rms_flight_ut"] = None
        if int(flight.sum()) >= 30 and s_iso and s_iso > 0:
            rad = np.linalg.norm(mag[flight] - bvec, axis=1)
            target = s_iso * float(np.hypot(H, Z))
            hard["sphere_rms_flight_ut"] = float(np.sqrt(np.mean((rad - target) ** 2)))
            field_ut = abs(target)
        out.update(hard)
    else:
        out["notes"].append("no WMM field in geo; hard iron left mixed with dip")

    hdg_rms = held if held is not None else in_rms
    quality = "weak"
    if (
        n_taxi >= 60
        and span >= 180.0
        and hdg_rms is not None
        and hdg_rms <= 5.0
        and fit["condition"] < 20.0
    ):
        quality = "good"
    elif n_taxi >= 30 and span >= 90.0 and hdg_rms is not None and hdg_rms <= 12.0:
        quality = "fair"
    out["quality"] = quality

    # Downsampled taxi heading residual for the browser sparkline.
    ep = df["ep"].to_numpy(dtype=np.int64)
    err = np.full(len(df), np.nan)
    err[idx] = wrap_deg(pred - psi_fit)
    keep = np.flatnonzero(np.isfinite(err))
    if len(keep) > 1500:
        keep = keep[:: int(np.ceil(len(keep) / 1500))]
    out["taxi_residual"] = {
        "t_ns": [int(ep[i]) * 1_000_000_000 for i in keep],
        "err_deg": [float(err[i]) for i in keep],
    }

    out["transients"] = find_transients(
        ep, mag, _gyro(df), gs, _col(df, "track_rate"), field_ut=float(field_ut)
    )
    if bvec is not None:
        out["hard_iron_mag_ut"] = float(np.linalg.norm(bvec))
    return out


def _transients_only(df: pd.DataFrame, mag: np.ndarray, field_ut: float | None) -> list[dict[str, Any]]:
    if len(df) == 0 or not np.isfinite(mag).any():
        return []
    return find_transients(
        df["ep"].to_numpy(dtype=np.int64),
        mag,
        _gyro(df),
        _col(df, "gs"),
        _col(df, "track_rate"),
        field_ut=float(field_ut or 50.0),
    )


def _cluster(ep: np.ndarray, mask: np.ndarray, max_gap_s: float) -> list[tuple[int, int]]:
    idx = np.flatnonzero(mask)
    if len(idx) == 0:
        return []
    spans: list[tuple[int, int]] = []
    a = prev = int(idx[0])
    for i in idx[1:]:
        i = int(i)
        if ep[i] - ep[prev] <= max_gap_s:
            prev = i
            continue
        spans.append((a, prev))
        a = prev = i
    spans.append((a, prev))
    return spans


def _edge_medians(mag: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Median of a ~20 s window ending 5 s before each sample, and the mirror."""
    s = pd.DataFrame(mag)
    pre = s.rolling(20, min_periods=8).median().shift(5)
    rev = s.iloc[::-1]
    post = rev.rolling(20, min_periods=8).median().shift(5).iloc[::-1]
    return pre.to_numpy(dtype=float), post.to_numpy(dtype=float)


def find_transients(
    ep: np.ndarray,
    mag: np.ndarray,
    gyro: np.ndarray,
    gs: np.ndarray,
    track_rate: np.ndarray,
    *,
    field_ut: float,
) -> list[dict[str, Any]]:
    """Mag changes that are not the Earth field rotating with the airplane.

    A flap, gear, or trim motor kicks the field and then lets it go (a blip)
    or leaves a new offset (a step). Nothing in the log names which motor it
    was. Events are classified by shape only.
    """
    n = len(ep)
    if n < 30:
        return []
    finite = _plausible(mag)
    w = np.linalg.norm(np.nan_to_num(gyro, nan=0.0), axis=1)
    w[~np.isfinite(gyro).all(axis=1)] = np.nan
    dm = np.zeros(n)
    dt = np.diff(ep.astype(float))
    dm[1:] = np.linalg.norm(np.diff(mag, axis=0), axis=1) / np.clip(dt, 1.0, None)
    dm[1:][(dt > 2.0) | ~finite[1:] | ~finite[:-1]] = 0.0
    expected = np.where(np.isfinite(w), field_ut * w, np.nan)
    rate = finite & np.isfinite(expected) & ((dm - expected) > RATE_UT_S)

    pre, post = _edge_medians(mag)
    both = np.isfinite(pre).all(axis=1) & np.isfinite(post).all(axis=1) & finite
    agree = np.linalg.norm(post - pre, axis=1)
    base = 0.5 * (pre + post)
    dev = np.linalg.norm(mag - base, axis=1)
    # Parked, or taxiing straight. A heading change of a few degrees, or a
    # missing track while the airplane is moving, is not a motor.
    parked = np.isfinite(gs) & (gs < PARKED_GS)
    not_airborne = ~np.isfinite(gs) | (gs < AIR_GS)
    tr_abs = pd.Series(np.abs(track_rate))
    roll_max = tr_abs.rolling(41, center=True, min_periods=5).max().to_numpy()
    roll_count = np.isfinite(track_rate).astype(float)
    roll_count = pd.Series(roll_count).rolling(41, center=True, min_periods=1).sum().to_numpy()
    straight_track = np.isfinite(roll_max) & (roll_max < STRAIGHT_RATE_DEG_S) & (roll_count >= 15)
    # gpsd omits track below ~0.5 m/s, so a parked airplane often has no rate.
    steady = both & not_airborne & (np.nan_to_num(w, nan=99.0) < 0.08)
    steady &= parked | straight_track
    blip = steady & (agree < 0.7) & (dev > BLIP_UT)
    step = steady & (agree > STEP_UT)

    events: list[dict[str, Any]] = []
    events += _events_from(ep, mag, gs, rate, "rate", baseline=None)
    events += _events_from(ep, mag, gs, blip, "blip", baseline=base)
    events += _events_from(ep, mag, gs, step, "step", baseline=pre)
    return _merge(events)


def _events_from(
    ep: np.ndarray,
    mag: np.ndarray,
    gs: np.ndarray,
    mask: np.ndarray,
    kind: str,
    baseline: np.ndarray | None,
) -> list[dict[str, Any]]:
    out = []
    for a, b in _cluster(ep, mask, 3.0):
        dur = float(ep[b] - ep[a])
        if kind != "step" and dur < 2.0 and (b - a) < 2:
            continue
        sl = slice(a, b + 1)
        if baseline is not None and np.isfinite(baseline[a]).all():
            ref = baseline[a]
        else:
            pre_i = max(0, a - 15)
            chunk = mag[pre_i:a]
            ref = np.nanmedian(chunk, axis=0) if len(chunk) >= 3 else mag[a]
        dev = np.linalg.norm(mag[sl] - ref, axis=1)
        if not np.isfinite(dev).any():
            continue
        k = int(np.nanargmax(dev))
        peak = float(dev[k])
        if peak < (STEP_UT if kind == "step" else 0.5):
            continue
        # Tens of µT on the rate detector is a broken sample. Anything past
        # LARGE_UT is kept, but it is too big to call a flap or gear motor.
        if kind == "rate" and peak > MOTOR_PEAK_MAX:
            ekind = "glitch"
        elif peak >= LARGE_UT:
            ekind = f"large+{kind}"
        else:
            ekind = kind
        i_peak = a + k
        g = gs[i_peak] if np.isfinite(gs[i_peak]) else float(np.nanmedian(gs[sl]))
        phase = "unknown"
        if np.isfinite(g):
            if g >= 36.0:
                phase = "flight"
            elif g >= TAXI_GS_LO:
                phase = "taxi"
            else:
                phase = "ground"
        out.append(
            {
                "kind": ekind,
                "t0": int(ep[a]),
                "t1": int(ep[b]),
                "t_peak": int(ep[i_peak]),
                "t0_ns": int(ep[a]) * 1_000_000_000,
                "t1_ns": int(ep[b]) * 1_000_000_000,
                "duration_s": max(dur, 1.0),
                "peak_ut": peak,
                "gs_mps": None if not np.isfinite(g) else float(g),
                "gs_kt": None if not np.isfinite(g) else float(g * MPS_TO_KT),
                "phase": phase,
            }
        )
    return out


def _merge(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events = sorted(events, key=lambda e: (e["t0"], -e["peak_ut"]))
    out: list[dict[str, Any]] = []
    for e in events:
        if out and e["t0"] <= out[-1]["t1"] + 4:
            prev = out[-1]
            prev["t1"] = max(prev["t1"], e["t1"])
            prev["t1_ns"] = prev["t1"] * 1_000_000_000
            prev["duration_s"] = float(prev["t1"] - prev["t0"])
            kinds = set(prev["kind"].split("+")) | set(e["kind"].split("+"))
            prev["kind"] = "+".join(sorted(kinds))
            if e["peak_ut"] > prev["peak_ut"]:
                prev["peak_ut"] = e["peak_ut"]
                prev["t_peak"] = e["t_peak"]
                prev["phase"] = e["phase"]
                prev["gs_kt"] = e["gs_kt"]
                prev["gs_mps"] = e["gs_mps"]
            continue
        out.append(dict(e))
    out.sort(key=lambda e: -e["peak_ut"])
    return out[:40]


def soak_quick(conn: sqlite3.Connection) -> dict[str, Any]:
    """One-pass stability of a desktop session that never changed heading.

    Reports how far the vector wandered. A session whose axes each move by
    tens of µT was rotated and can support an ellipsoid fit; a session that
    sits still only tells us the noise and any slow drift.
    """
    row = conn.execute(
        """
        SELECT MIN(mag_x_ut), MAX(mag_x_ut), MIN(mag_y_ut), MAX(mag_y_ut),
               MIN(mag_z_ut), MAX(mag_z_ut),
               AVG(mag_x_ut), AVG(mag_y_ut), AVG(mag_z_ut),
               AVG(mag_x_ut*mag_x_ut + mag_y_ut*mag_y_ut + mag_z_ut*mag_z_ut),
               COUNT(*)
        FROM mmc5983
        WHERE mag_x_ut*mag_x_ut + mag_y_ut*mag_y_ut + mag_z_ut*mag_z_ut
              BETWEEN 25 AND 40000
        """
    ).fetchone()
    if not row or not row[10]:
        return {"ok": False, "reason": "empty mmc5983"}
    ranges = [float(row[1] - row[0]), float(row[3] - row[2]), float(row[5] - row[4])]
    mean = np.array([row[6], row[7], row[8]], dtype=float)
    var = float(row[9]) - float(np.dot(mean, mean))
    wander = float(np.sqrt(max(0.0, var)))
    spread = float(max(ranges))
    return {
        "ok": True,
        "n": int(row[10]),
        "range_ut": ranges,
        "spread_ut": spread,
        "mean_ut": [float(v) for v in mean],
        "wander_rms_ut": wander,
        "moved": spread >= 20.0,
    }


def fit_ellipsoid(samples: np.ndarray) -> dict[str, Any] | None:
    """Algebraic ellipsoid. Use only when the cloud is genuinely 3D.

    Solves ``vᵀ A v + 2 g·v = 1`` and converts to a center and three radii.
    A patch of a sphere (a taxi circle) makes this ill-conditioned; callers
    check the radii and the residual before trusting it.
    """
    x = np.asarray(samples, dtype=float)
    x = x[np.isfinite(x).all(axis=1)]
    if len(x) < 50:
        return None
    X = np.column_stack(
        [
            x[:, 0] ** 2,
            x[:, 1] ** 2,
            x[:, 2] ** 2,
            2 * x[:, 0] * x[:, 1],
            2 * x[:, 0] * x[:, 2],
            2 * x[:, 1] * x[:, 2],
            2 * x[:, 0],
            2 * x[:, 1],
            2 * x[:, 2],
        ]
    )
    coef, _, _, _ = np.linalg.lstsq(X, np.ones(len(x)), rcond=None)
    A = np.array(
        [
            [coef[0], coef[3], coef[4]],
            [coef[3], coef[1], coef[5]],
            [coef[4], coef[5], coef[2]],
        ]
    )
    g = coef[6:9]
    try:
        center = -np.linalg.solve(A, g)
    except np.linalg.LinAlgError:
        return None
    scale = 1.0 + float(center @ A @ center)
    if scale <= 0:
        return None
    evals, evecs = np.linalg.eigh(A / scale)
    if np.any(evals <= 0):
        return None
    radii = 1.0 / np.sqrt(evals)
    pred = np.einsum("ij,ni,nj->n", A / scale, x - center, x - center)
    rms = float(np.sqrt(np.mean((pred - 1.0) ** 2)))
    return {
        "center_ut": [float(v) for v in center],
        "radii_ut": [float(v) for v in radii],
        "axes": evecs.tolist(),
        "residual_rms": rms,
    }
