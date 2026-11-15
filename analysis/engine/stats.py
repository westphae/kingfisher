"""Small numeric helpers for the engine review. No SciPy."""

from __future__ import annotations

import math

import numpy as np


def finite(x) -> np.ndarray:
    a = np.asarray(x, dtype=np.float64)
    return np.isfinite(a)


def nan_median(x) -> float:
    a = np.asarray(x, dtype=np.float64)
    a = a[np.isfinite(a)]
    if a.size == 0:
        return float("nan")
    return float(np.median(a))


def nan_min(x) -> float:
    a = np.asarray(x, dtype=np.float64)
    a = a[np.isfinite(a)]
    if a.size == 0:
        return float("nan")
    return float(np.min(a))


def nan_max(x) -> float:
    a = np.asarray(x, dtype=np.float64)
    a = a[np.isfinite(a)]
    if a.size == 0:
        return float("nan")
    return float(np.max(a))


def rolling_median(x: np.ndarray, win: int) -> np.ndarray:
    """Centered rolling median. ``win`` is in samples; even values are rounded up."""
    a = np.asarray(x, dtype=np.float64)
    n = a.size
    if n == 0 or win <= 1:
        return a.copy()
    win = int(win)
    if win % 2 == 0:
        win += 1
    pad = win // 2
    padded = np.pad(a, (pad, pad), constant_values=np.nan)
    out = np.empty(n, dtype=np.float64)
    for i in range(n):
        w = padded[i : i + win]
        good = w[np.isfinite(w)]
        out[i] = float(np.median(good)) if good.size else float("nan")
    return out


def rolling_std(x: np.ndarray, win: int) -> np.ndarray:
    a = np.asarray(x, dtype=np.float64)
    n = a.size
    if n == 0 or win <= 1:
        return np.zeros(n, dtype=np.float64)
    win = int(win) if int(win) % 2 == 1 else int(win) + 1
    pad = win // 2
    padded = np.pad(a, (pad, pad), constant_values=np.nan)
    out = np.empty(n, dtype=np.float64)
    for i in range(n):
        w = padded[i : i + win]
        good = w[np.isfinite(w)]
        out[i] = float(np.std(good)) if good.size >= 3 else float("nan")
    return out


def fill_short_gaps(mask: np.ndarray, t: np.ndarray, gap_s: float) -> np.ndarray:
    """Turn brief False holes inside a True run into True."""
    out = np.asarray(mask, dtype=bool).copy()
    n = out.size
    i = 0
    while i < n:
        if out[i]:
            i += 1
            continue
        j = i
        while j < n and not out[j]:
            j += 1
        if i > 0 and j < n and (t[j] - t[i]) <= gap_s:
            out[i:j] = True
        i = j
    return out


def haversine_m(lat1, lon1, lat2, lon2) -> float:
    r = 6_371_000.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dphi = p2 - p1
    dl = math.radians(lon2 - lon1)
    h = math.sin(dphi / 2.0) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2.0) ** 2
    return 2.0 * r * math.asin(min(1.0, math.sqrt(h)))


def pearson(a: np.ndarray, b: np.ndarray) -> float | None:
    x = np.asarray(a, dtype=np.float64)
    y = np.asarray(b, dtype=np.float64)
    ok = np.isfinite(x) & np.isfinite(y)
    if int(ok.sum()) < 3:
        return None
    x = x[ok] - x[ok].mean()
    y = y[ok] - y[ok].mean()
    denom = float(np.sqrt(np.dot(x, x) * np.dot(y, y)))
    if denom < 1e-12:
        return None
    return float(np.dot(x, y) / denom)


def fmt_num(v: float | None, digits: int, unit: str = "") -> str:
    if v is None:
        return "n/a"
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "n/a"
    if not math.isfinite(x):
        return "n/a"
    text = f"{x:.{digits}f}"
    return f"{text} {unit}".strip()
