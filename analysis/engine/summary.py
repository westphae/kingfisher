"""Cross-flight engine summary.

One row per flight: CHT3 in climb and cruise, peak CHT, time above 380°F,
cruise fuel flow / TIT / lean-of-peak, vapor and boost counts, the mag
check, and oil. CHT3 is correlated with inferred boost and fuel-flow
anomalies when there are enough flights to say something.
"""

from __future__ import annotations

from analysis.engine.stats import pearson

import numpy as np


def summarize(reviews: list[dict], flags: list[dict] | None = None) -> dict:
    rows = [_row(r) for r in reviews if r.get("overall") != "skip" or r.get("phases")]
    rows.sort(key=lambda r: r.get("t0_unix") or 0)
    return {
        "flights": rows,
        "n": len(rows),
        "correlation": _correlate(rows),
        "flags": flags or [],
    }


def _row(review: dict) -> dict:
    m = review.get("metrics") or {}
    mag = _check(review, "mag_check")
    return {
        "session": review.get("session"),
        "source": review.get("source"),
        "t0_unix": review.get("t0_unix"),
        "overall": review.get("overall"),
        "alt_source": review.get("alt_source"),
        "counts": review.get("counts"),
        "cht3_offset_climb_f": m.get("cht3_offset_climb_f"),
        "cht3_offset_cruise_f": m.get("cht3_offset_cruise_f"),
        "cht3_wiggle_f": m.get("cht3_wiggle_f"),
        "peak_cht_f": m.get("peak_cht_f"),
        "time_above_380_s": m.get("time_above_380_s"),
        "cruise_ff_gph": m.get("cruise_ff_gph"),
        "cruise_rpm": m.get("cruise_rpm"),
        "cruise_tit_f": m.get("cruise_tit_f"),
        "lop_depth_f": m.get("lop_depth_f"),
        "peak_tit_f": m.get("peak_tit_f"),
        "mixture_side": m.get("mixture_side"),
        "hp_est": m.get("hp_est"),
        "vapor_events": m.get("vapor_events") or 0,
        "boost_lo": m.get("boost_lo") or 0,
        "boost_hi": m.get("boost_hi") or 0,
        "boost_lo_ff_drop": m.get("boost_lo_ff_drop") or 0,
        "mag_status": mag.get("status") if mag else None,
        "mag_observed": mag.get("observed") if mag else None,
        "oil_press_min": m.get("oil_press_min"),
        "oil_temp_max_f": m.get("oil_temp_max_f"),
    }


def _check(review: dict, id_: str) -> dict | None:
    for c in review.get("checks") or []:
        if c.get("id") == id_:
            return c
    return None


def _correlate(rows: list[dict]) -> dict:
    """CHT3 cruise offset against boost events and fuel-flow anomalies."""
    ys = []
    boost = []
    ff_drop = []
    for row in rows:
        y = row.get("cht3_offset_cruise_f")
        if y is None:
            y = row.get("cht3_offset_climb_f")
        if y is None:
            continue
        b = (row.get("boost_lo") or 0) + (row.get("boost_hi") or 0) + (row.get("boost_lo_ff_drop") or 0)
        ys.append(y)
        boost.append(b)
        ff_drop.append(row.get("boost_lo_ff_drop") or 0)
    out = {
        "n": len(ys),
        "cht3_vs_boost_r": None,
        "cht3_vs_lo_ff_drop_r": None,
        "note": "",
    }
    if len(ys) < 3:
        out["note"] = (
            f"{len(ys)} flight(s) with a CHT3 offset. "
            "A correlation wants at least 3."
        )
        return out
    out["cht3_vs_boost_r"] = _r3(pearson(np.array(ys), np.array(boost, dtype=float)))
    out["cht3_vs_lo_ff_drop_r"] = _r3(pearson(np.array(ys), np.array(ff_drop, dtype=float)))
    out["note"] = (
        "Pearson r of the cruise CHT3 offset (climb offset when cruise is missing) "
        "against inferred boost events, and against the LO-lowers-fuel-flow anomaly. "
        "Boost is inferred from fuel-flow steps, not from a logged switch."
    )
    return out


def _r3(v) -> float | None:
    if v is None:
        return None
    return round(float(v), 3)
