"""Small geometry helpers shared by the offline calibrations.

Angles that come back from ``rpy_from_R`` are the ZYX composition
``R = Rz(yaw) @ Ry(pitch) @ Rx(roll)``: ``R`` maps a vector in the sensor
frame into the frame the fitter called the reference (local level / aircraft).
"""

from __future__ import annotations

import numpy as np

G0 = 9.80665
MPS_TO_KT = 1.9438444924406


def wrap_deg(a: np.ndarray | float) -> np.ndarray:
    return (np.asarray(a, dtype=float) + 180.0) % 360.0 - 180.0


def circ_mean_deg(a: np.ndarray, w: np.ndarray | None = None) -> float:
    ang = np.deg2rad(np.asarray(a, dtype=float))
    s = np.sin(ang)
    c = np.cos(ang)
    if w is None:
        sm = np.nanmean(s)
        cm = np.nanmean(c)
    else:
        ww = np.asarray(w, dtype=float)
        sm = np.nansum(ww * s) / np.nansum(ww)
        cm = np.nansum(ww * c) / np.nansum(ww)
    if not np.isfinite(sm) or not np.isfinite(cm):
        return float("nan")
    return float(np.rad2deg(np.arctan2(sm, cm)) % 360.0)


def circ_rms_deg(err: np.ndarray) -> float | None:
    e = wrap_deg(err)
    e = e[np.isfinite(e)]
    if len(e) == 0:
        return None
    return float(np.sqrt(np.mean(e * e)))


def occupied_span_deg(psi_deg: np.ndarray, bin_deg: float = 10.0) -> float:
    """How many degrees of the circle have at least one sample, in ``bin_deg`` steps."""
    psi = np.asarray(psi_deg, dtype=float)
    psi = psi[np.isfinite(psi)]
    if len(psi) == 0:
        return 0.0
    nbin = int(round(360.0 / bin_deg))
    bins = np.zeros(nbin, dtype=bool)
    k = np.floor((psi % 360.0) / bin_deg).astype(int) % nbin
    bins[k] = True
    return float(bins.sum() * bin_deg)


def R_from_rpy(roll: float, pitch: float, yaw: float) -> np.ndarray:
    """``R = Rz(yaw) @ Ry(pitch) @ Rx(roll)``, radians."""
    cr, sr = np.cos(roll), np.sin(roll)
    cp, sp = np.cos(pitch), np.sin(pitch)
    cy, sy = np.cos(yaw), np.sin(yaw)
    rx = np.array([[1.0, 0.0, 0.0], [0.0, cr, -sr], [0.0, sr, cr]])
    ry = np.array([[cp, 0.0, sp], [0.0, 1.0, 0.0], [-sp, 0.0, cp]])
    rz = np.array([[cy, -sy, 0.0], [sy, cy, 0.0], [0.0, 0.0, 1.0]])
    return rz @ ry @ rx


def rpy_from_R(R: np.ndarray) -> tuple[float, float, float]:
    """Inverse of ``R_from_rpy``. Radians. Singular when pitch is ±90°."""
    pitch = float(np.arcsin(np.clip(-R[2, 0], -1.0, 1.0)))
    roll = float(np.arctan2(R[2, 1], R[2, 2]))
    yaw = float(np.arctan2(R[1, 0], R[0, 0]))
    return roll, pitch, yaw


def quat_wxyz_from_R(R: np.ndarray) -> list[float]:
    """Unit quaternion ``[w, x, y, z]`` for ``R``."""
    t = float(np.trace(R))
    if t > 0.0:
        s = np.sqrt(t + 1.0) * 2.0
        w = 0.25 * s
        x = (R[2, 1] - R[1, 2]) / s
        y = (R[0, 2] - R[2, 0]) / s
        z = (R[1, 0] - R[0, 1]) / s
    elif R[0, 0] > R[1, 1] and R[0, 0] > R[2, 2]:
        s = np.sqrt(1.0 + R[0, 0] - R[1, 1] - R[2, 2]) * 2.0
        w = (R[2, 1] - R[1, 2]) / s
        x = 0.25 * s
        y = (R[0, 1] + R[1, 0]) / s
        z = (R[0, 2] + R[2, 0]) / s
    elif R[1, 1] > R[2, 2]:
        s = np.sqrt(1.0 + R[1, 1] - R[0, 0] - R[2, 2]) * 2.0
        w = (R[0, 2] - R[2, 0]) / s
        x = (R[0, 1] + R[1, 0]) / s
        y = 0.25 * s
        z = (R[1, 2] + R[2, 1]) / s
    else:
        s = np.sqrt(1.0 + R[2, 2] - R[0, 0] - R[1, 1]) * 2.0
        w = (R[1, 0] - R[0, 1]) / s
        x = (R[0, 2] + R[2, 0]) / s
        y = (R[1, 2] + R[2, 1]) / s
        z = 0.25 * s
    q = np.array([w, x, y, z], dtype=float)
    n = np.linalg.norm(q)
    if n == 0.0:
        return [1.0, 0.0, 0.0, 0.0]
    q /= n
    if q[0] < 0.0:
        q = -q
    return [float(v) for v in q]


def kabsch(src: np.ndarray, dst: np.ndarray, w: np.ndarray | None = None) -> np.ndarray:
    """Rotation ``R`` with ``R @ src[i] ≈ dst[i]``.

    Vectors are compared through the origin (no centroid removal): these are
    measured directions and rates, not a point cloud.
    """
    src = np.asarray(src, dtype=float)
    dst = np.asarray(dst, dtype=float)
    if w is None:
        ww = np.ones(len(src))
    else:
        ww = np.asarray(w, dtype=float)
    H = (src * ww[:, None]).T @ dst
    U, _, Vt = np.linalg.svd(H)
    R = Vt.T @ U.T
    if np.linalg.det(R) < 0.0:
        Vt = Vt.copy()
        Vt[-1] *= -1.0
        R = Vt.T @ U.T
    return R
