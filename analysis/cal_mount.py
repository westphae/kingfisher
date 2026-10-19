"""Per-flight cabin IMU orientation, and a check of the live AHRS.

Kingfisher feeds the ICM-45686 axes to the AHRS as the aircraft axes. The
box is pulled out between flights, so that assumption is a new small rotation
every time it goes back in.

What a recording can actually observe:

- Roll and pitch of the IMU relative to gravity, from parked and straight-taxi
  specific force. The Bonanza sits nose-up on its gear, so the pitch number
  includes that deck angle. Compare flights with each other. The absolute
  pitch is not the error from the waterline.
- Yaw of the IMU about that vertical, from taxi turns: the lateral specific
  force should match ``v · dψ/dt`` out the right wing. Roll and pitch come
  from gravity only, so a bad turn cannot tilt the horizon. The gyro about
  the up axis should match the opposite of the GPS track rate (right-hand
  about up is a left turn); once the up axis is right, that check does not
  depend on yaw. Straight taxi does not observe yaw.
- Whether the live AHRS horizon tracks a coordinated turn. A constant bias
  against that bank angle is the mount (plus the deck). The scatter after the
  bias is removed is the dynamic error.

The live AHRS consumes the raw IMU axes, and this accelerometer reads about
+1 g on +z when the box is upright. ``R`` is therefore the small correction
in that same z-up, y-right, x-forward frame: ``v_level = R @ v_imu``. Angles
near zero mean the box already matches what the AHRS assumes. Aircraft FRD
(z down) is a further 180° roll, not part of the number reported here.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from analysis.geom import (
    G0,
    MPS_TO_KT,
    R_from_rpy,
    circ_mean_deg,
    circ_rms_deg,
    quat_wxyz_from_R,
    wrap_deg,
)

# Radians and m/s. See cal_mag for the knot equivalents.
TAXI_LO = 4.0
TAXI_HI = 23.0
PARK_GS = 2.0


def _col(df: pd.DataFrame, name: str) -> np.ndarray:
    if name not in df.columns:
        return np.full(len(df), np.nan)
    return df[name].to_numpy(dtype=float)


def _finite_rows(*cols: np.ndarray) -> np.ndarray:
    m = np.ones(len(cols[0]), dtype=bool)
    for c in cols:
        if c.ndim == 1:
            m &= np.isfinite(c)
        else:
            m &= np.isfinite(c).all(axis=1)
    return m


def analyze_mount(df: pd.DataFrame, meta: dict[str, Any]) -> dict[str, Any]:
    del meta  # WMM is a mag concern; mount uses gravity, gyro, and track.
    if "accel_x" not in df.columns or len(df) == 0:
        return {"ok": False, "reason": "no cabin accelerometer"}
    accel = np.column_stack([_col(df, "accel_x"), _col(df, "accel_y"), _col(df, "accel_z")])
    gyro = np.column_stack([_col(df, "anglvel_x"), _col(df, "anglvel_y"), _col(df, "anglvel_z")])
    gs = _col(df, "gs")
    rate = _col(df, "track_rate")
    an = np.linalg.norm(accel, axis=1)
    gn = np.linalg.norm(gyro, axis=1)

    parked = _finite_rows(accel, gyro, gs) & (gs < PARK_GS) & (gn < 0.08) & (np.abs(an - G0) < 1.2)
    straight = (
        _finite_rows(accel, gyro, gs, rate)
        & (gs >= TAXI_LO)
        & (gs <= 18.0)
        & (np.abs(rate) < 2.0)
        & (gn < 0.12)
        & (np.abs(an - G0) < 1.5)
    )
    # A desk or car pose before the airplane points a different way than the
    # deck. When straight taxi shows the up axis, drop parked samples that
    # disagree with it by more than 12°.
    dropped = 0
    if int(straight.sum()) >= 15 and int(parked.sum()) > 0:
        up_s = accel[straight].mean(axis=0)
        up_s = up_s / np.linalg.norm(up_s)
        idx = np.flatnonzero(parked)
        d = accel[idx]
        d = d / np.linalg.norm(d, axis=1, keepdims=True)
        ang = np.arccos(np.clip(d @ up_s, -1.0, 1.0))
        drop = ang > np.deg2rad(12.0)
        dropped = int(drop.sum())
        if dropped:
            parked = parked.copy()
            parked[idx[drop]] = False
    level = parked | straight
    turns = (
        _finite_rows(accel, gyro, gs, rate)
        & (gs >= TAXI_LO)
        & (gs <= TAXI_HI)
        & (np.abs(rate) >= 4.0)
        & (np.abs(rate) <= 40.0)
    )
    out: dict[str, Any] = {
        "ok": False,
        "parked_s": int(parked.sum()),
        "parked_dropped_s": dropped,
        "straight_s": int(straight.sum()),
        "turn_s": int(turns.sum()),
        "notes": [],
    }
    if dropped:
        out["notes"].append(
            f"{dropped} s of parked accel point more than 12° off the straight-taxi "
            "up axis and were left out of the horizon (a desk or car pose before the airplane)"
        )
    if int(level.sum()) < 15:
        out["reason"] = "not enough still or straight-taxi time to see gravity"
        _ahrs(out, df, gs, rate)
        return out

    # IMU +z reads about +g when the box is upright. Roll and pitch are the
    # unique yaw-free rotation that takes the mean specific force to +z.
    # Turns are not in this fit, so a bad lateral model cannot tilt the horizon.
    up = accel[level].mean(axis=0)
    up_n = float(np.linalg.norm(up))
    hyp = float(np.hypot(up[1], up[2]))
    roll = float(np.arctan2(up[1], up[2]))
    pitch = float(np.arctan2(-up[0], hyp if hyp > 0.0 else 1e-9))
    out["specific_force_mean_mps2"] = [float(v) for v in up]
    out["up_is_plus_z"] = bool(up[2] > 0.0)
    out["raw_tilt_roll_deg"] = float(np.rad2deg(roll))
    out["raw_tilt_pitch_deg"] = float(np.rad2deg(pitch))
    if abs(np.rad2deg(roll)) > 60.0 or abs(np.rad2deg(pitch)) > 60.0:
        out["notes"].append(
            "the box is far from upright; use the quaternion, not the small-angle story"
        )

    yaw_deg: float | None = None
    yaw_ok = False
    gyro_rms = None
    accel_rms = None
    yaw_split = None
    if int(turns.sum()) >= 12:
        v = gs[turns]
        w = np.deg2rad(rate[turns])  # positive: nose right
        ep = df["ep"].to_numpy(dtype=float)
        a_long = np.clip(np.gradient(v, ep[turns]), -3.0, 3.0)
        a_turn = accel[turns]
        grid = np.arange(-180.0, 181.0, 1.0)

        def _yaw_cost(yaw: float, sl: slice) -> float:
            # Lateral specific force sees the nose. Along-track is a weak check
            # because GPS speed at 1 Hz is noisy. Gyro about up does not see yaw
            # once roll and pitch are fixed.
            R = R_from_rpy(roll, pitch, np.deg2rad(yaw))
            a_hat = (R @ a_turn[sl].T).T
            e_lat = a_hat[:, 1] - v[sl] * w[sl]
            e_lon = a_hat[:, 0] - a_long[sl]
            return float(np.mean(e_lat * e_lat) + 0.15 * np.mean(e_lon * e_lon))

        costs = np.array([_yaw_cost(y, slice(None)) for y in grid])
        ibest = int(np.argmin(costs))
        best = float(grid[ibest])
        yaw_deg = best
        yaw_ok = True

        def _cost_near(yaw: float) -> float:
            y = (yaw + 180.0) % 360.0 - 180.0
            return float(costs[int(np.argmin(np.abs(grid - y)))])

        orth = min(_cost_near(best + 90.0), _cost_near(best - 90.0))
        out["yaw_cost_ratio"] = float(orth / costs[ibest]) if costs[ibest] > 1e-9 else None
        # A flat cost means the lateral accel never showed the nose.
        if costs[ibest] > 0.35 * orth:
            yaw_ok = False
            yaw_deg = None
            out["notes"].append(
                "taxi turns do not pick a clear yaw; the lateral-accel minimum is shallow"
            )
        mid = len(v) // 2
        if yaw_ok and mid >= 8:
            c1 = np.array([_yaw_cost(y, slice(0, mid)) for y in grid])
            c2 = np.array([_yaw_cost(y, slice(mid, None)) for y in grid])
            y1 = float(grid[int(np.argmin(c1))])
            y2 = float(grid[int(np.argmin(c2))])
            yaw_split = float(wrap_deg(y1 - y2))
            if abs(yaw_split) > 8.0:
                yaw_ok = False
                yaw_deg = None
                out["notes"].append(
                    f"the two halves of the taxi disagree on yaw by {yaw_split:.0f}°; yaw not trusted"
                )
        R_use = R_from_rpy(roll, pitch, np.deg2rad(best))
        w_hat = (R_use @ gyro[turns].T).T
        w_ac = np.column_stack([np.zeros(len(v)), np.zeros(len(v)), -w])
        gyro_rms = float(np.rad2deg(np.sqrt(np.mean(np.sum((w_hat - w_ac) ** 2, axis=1)))))
        out["gyro_horiz_rms_deg_s"] = float(
            np.rad2deg(np.sqrt(np.mean(w_hat[:, 0] ** 2 + w_hat[:, 1] ** 2)))
        )
        if np.std(w_hat[:, 2]) > 1e-4 and np.std(w) > 1e-4:
            out["gyro_track_corr"] = float(np.corrcoef(w_hat[:, 2], -w)[0, 1])
        if yaw_ok:
            a_hat = (R_use @ a_turn.T).T
            f_ac = np.column_stack([a_long, v * w, np.full(len(v), G0)])
            accel_rms = float(np.sqrt(np.mean(np.sum((a_hat - f_ac) ** 2, axis=1))))

    # Reported rotation uses yaw only when the split-half agreed. Otherwise it
    # is the gravity rotation, which is what the horizon needs.
    yaw_for_R = yaw_deg if yaw_deg is not None else 0.0
    R = R_from_rpy(roll, pitch, np.deg2rad(yaw_for_R))
    out.update(
        {
            "ok": True,
            "yaw_observable": yaw_ok,
            "roll_deg": float(np.rad2deg(roll)),
            "pitch_deg": float(np.rad2deg(pitch)),
            "yaw_deg": float(yaw_deg) if yaw_deg is not None else None,
            "R_level_from_imu": R.tolist(),
            "quat_wxyz": quat_wxyz_from_R(R),
            "gravity_mps2": up_n,
        }
    )
    if not yaw_ok and int(turns.sum()) < 12:
        out["notes"].append(
            "yaw is not observable: need a taxi turn so the lateral accel can see the nose"
        )
    if gyro_rms is not None:
        out["gyro_rms_deg_s"] = gyro_rms
        out["accel_rms_mps2"] = accel_rms
        out["yaw_half_split_deg"] = yaw_split

    # Gravity fixes roll and pitch. The gyro-versus-track residual is a few
    # deg/s of 1 Hz GPS noise even when the up axis is right, so the gate is
    # the horizontal residual and the correlation, not that total RMS.
    quality = "weak"
    horiz = out.get("gyro_horiz_rms_deg_s")
    corr = out.get("gyro_track_corr")
    if int(level.sum()) >= 30:
        quality = "fair"
        if (
            out["yaw_observable"]
            and horiz is not None
            and horiz < 1.5
            and corr is not None
            and corr > 0.75
        ):
            quality = "good"
    out["quality"] = quality
    _ahrs(out, df, gs, rate)
    _cruise_pitch(out, df, gs, rate)
    return out


def _ahrs(out: dict[str, Any], df: pd.DataFrame, gs: np.ndarray, rate: np.ndarray) -> None:
    """Live AHRS against straight-taxi level and against coordinated bank."""
    if "roll" not in df.columns:
        return
    roll = _col(df, "roll")
    pitch = _col(df, "pitch")
    yaw = _col(df, "yaw")
    track = _col(df, "track")
    straight = (
        np.isfinite(roll)
        & np.isfinite(gs)
        & np.isfinite(rate)
        & (gs >= TAXI_LO)
        & (gs <= 18.0)
        & (np.abs(rate) < 2.0)
    )
    if int(straight.sum()) >= 15:
        out["ahrs_roll_taxi_deg"] = float(np.nanmean(roll[straight]))
        out["ahrs_pitch_taxi_deg"] = float(np.nanmean(pitch[straight]))
        mp = out.get("pitch_deg")
        ap = out["ahrs_pitch_taxi_deg"]
        if (
            mp is not None
            and abs(float(mp)) > 3.0
            and abs(ap) > 3.0
            and abs(float(mp) + ap) < 2.5
        ):
            out["notes"].append(
                "live AHRS straight-taxi pitch is about the opposite of the accelerometer tilt. "
                "The filter treats the IMU axes as the aircraft axes, so a box yawed away from "
                "the nose shows that tilt as pitch."
            )
        both = straight & np.isfinite(yaw) & np.isfinite(track)
        if int(both.sum()) >= 15:
            d = wrap_deg(yaw[both] - track[both])
            out["ahrs_yaw_minus_track_bias_deg"] = float(circ_mean_deg(d))
            out["ahrs_yaw_minus_track_std_deg"] = circ_rms_deg(d - circ_mean_deg(d))

    flying = (
        np.isfinite(roll)
        & np.isfinite(gs)
        & np.isfinite(rate)
        & (gs >= 36.0)
        & (np.abs(rate) >= 3.0)
        & (np.abs(rate) <= 25.0)
    )
    if int(flying.sum()) < 20:
        return
    v = gs[flying]
    w = np.deg2rad(rate[flying])
    bank = np.rad2deg(np.arctan2(v * w, G0))
    r = roll[flying]
    # AHRS sign is whatever goflying published. Flip to match track rate.
    corr = np.corrcoef(r, bank)[0, 1] if np.std(r) > 0.2 and np.std(bank) > 0.2 else 1.0
    sign = 1.0
    if np.isfinite(corr) and corr < 0:
        sign = -1.0
        bank = -bank
    bias = float(np.nanmean(wrap_deg(r - bank)))
    scatter = circ_rms_deg(wrap_deg(r - bank) - bias)
    out["ahrs_bank_sign"] = sign
    out["ahrs_bank_bias_deg"] = bias
    out["ahrs_bank_scatter_deg"] = scatter
    out["ahrs_bank_n"] = int(flying.sum())


def _cruise_pitch(out: dict[str, Any], df: pd.DataFrame, gs: np.ndarray, rate: np.ndarray) -> None:
    if "pitch" not in df.columns:
        return
    vs = _col(df, "vs")
    steady = np.isfinite(_col(df, "pitch")) & np.isfinite(gs) & (gs >= 60.0)
    if np.isfinite(rate).any():
        steady &= np.isfinite(rate) & (np.abs(rate) < 1.5)
    if np.isfinite(vs).any():
        steady &= np.isfinite(vs) & (np.abs(vs) < 1.5)
    if int(steady.sum()) < 30:
        return
    out["ahrs_pitch_cruise_deg"] = float(np.nanmean(_col(df, "pitch")[steady]))
    out["cruise_s"] = int(steady.sum())
    out["cruise_gs_kt"] = float(np.nanmean(gs[steady]) * MPS_TO_KT)


def compass_vs_track(df: pd.DataFrame) -> dict[str, Any] | None:
    """Live compass true heading against GPS track, straight taxi only."""
    if "heading_true_deg" not in df.columns or "track" not in df.columns:
        return None
    gs = _col(df, "gs")
    rate = _col(df, "track_rate")
    h = _col(df, "heading_true_deg")
    tr = _col(df, "track")
    m = (
        np.isfinite(h)
        & np.isfinite(tr)
        & np.isfinite(gs)
        & np.isfinite(rate)
        & (gs >= TAXI_LO)
        & (gs <= TAXI_HI)
        & (np.abs(rate) < 2.0)
    )
    if int(m.sum()) < 15:
        return {"ok": False, "n": int(m.sum())}
    d = wrap_deg(h[m] - tr[m])
    bias = float(circ_mean_deg(d))
    err = np.abs(wrap_deg(d - bias))
    return {
        "ok": True,
        "n": int(m.sum()),
        "bias_deg": bias,
        "std_deg": circ_rms_deg(d - bias),
        # Around the bias: a reversed compass that holds still scores high.
        "within_15_deg": float(np.mean(err < 15.0)),
        # Against the track itself.
        "within_15_of_track": float(np.mean(np.abs(d) < 15.0)),
    }
