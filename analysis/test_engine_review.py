"""Synthetic flights for the engine review. No real JPI or flight DB."""

from __future__ import annotations

import unittest

import numpy as np

from analysis.engine.boost import find_boost_steps, find_vapor
from analysis.engine.limits import Limits
from analysis.engine.mag import detect_mag
from analysis.engine.review import review_trace
from analysis.engine.trace import Trace


def _seg(seconds, **vals):
    n = max(1, int(round(seconds / 2.0)))
    return n, vals


def _trace(segments, *, cht3_extra=16.0, hp=60.0) -> Trace:
    chunks = [(int(count), vals) for count, vals in segments]
    n = sum(c[0] for c in chunks)
    t0 = 1_700_000_000.0
    t = t0 + np.arange(n) * 2.0

    def column(key, default):
        out = np.empty(n, dtype=np.float64)
        i = 0
        for count, vals in chunks:
            v = vals.get(key, default)
            out[i : i + count] = v
            i += count
        return out

    egt = np.full((n, 6), 1400.0)
    cht = np.full((n, 6), 340.0)
    cht[:, 2] += cht3_extra
    # Small in-flight wiggle on CHT3, a couple of degrees.
    cht[:, 2] += 2.0 * np.sin(np.arange(n) / 5.0)
    i = 0
    for count, vals in chunks:
        if "egt" in vals:
            egt[i : i + count] = vals["egt"]
        if "cht" in vals:
            cht[i : i + count] = vals["cht"]
        if "egt_delta" in vals:
            egt[i : i + count] += vals["egt_delta"]
        i += count
    return Trace(
        session="synthetic",
        source="jpi",
        t=t,
        t_ns=(t * 1e9).astype(np.int64),
        rpm=column("rpm", 2300.0),
        map_inhg=column("map", 29.5),
        ff=column("ff", 16.0),
        tit=column("tit", 1500.0),
        oil_temp_f=column("oil_t", 180.0),
        oil_press=column("oil_p", 50.0),
        oat_c=column("oat", 20.0),
        egt=egt,
        cht=cht,
        alt_ft=column("alt", 3000.0),
        alt_source="gps.alt_msl",
        ias_kt=column("ias", 118.0),
        ias_note="ok",
        gs_kt=column("gs", 120.0),
        gs_source="gps.gs",
        hp_pct=np.full(n, hp),
    )


def _textbook() -> Trace:
    # OAT 20°C is 68°F, so the takeoff target is 35 gph.
    climb_alt = np.linspace(400, 3200, int(4 * 60 / 2))
    descent_alt = np.linspace(5200, 800, int(3 * 60 / 2))
    # Lean-find hump: shoulders more than 40°F under a 1600°F peak, under 2 min.
    lean_n = int(40 / 2)
    lean_tit = np.concatenate([
        np.linspace(1280, 1600, lean_n // 2),
        np.linspace(1600, 1530, lean_n - lean_n // 2),
    ])
    lean_ff = np.linspace(35, 16.5, lean_n)
    return _trace([
        _seg(180, rpm=1100, map=14, ff=4.0, tit=900, gs=6, alt=300, ias=0, oil_t=120),
        _seg(50, rpm=1700, map=16, ff=8.0, tit=1100, gs=0, alt=300, ias=0),
        _seg(40, rpm=2700, map=29.6, ff=35.0, tit=1280, gs=55, alt=450, ias=80, oil_t=160),
        (climb_alt.size, {"rpm": 2700, "map": 29.6, "ff": 35.0, "tit": 1280, "gs": 110, "alt": climb_alt, "ias": 118}),
        _seg(90, rpm=2700, map=29.6, ff=35.0, tit=1280, gs=140, alt=5200, ias=140),
        (lean_n, {"rpm": 2500, "map": 29.5, "ff": lean_ff, "tit": lean_tit, "gs": 150, "alt": 5200, "ias": 150}),
        _seg(600, rpm=2500, map=29.5, ff=16.5, tit=1530, gs=155, alt=5200, ias=155),
        (descent_alt.size, {"rpm": 2000, "map": 20.0, "ff": 12.0, "tit": 1560, "gs": 130, "alt": descent_alt, "ias": 130}),
        _seg(90, rpm=1400, map=15, ff=18.0, tit=1400, gs=70, alt=400, ias=70),
        _seg(60, rpm=1100, map=14, ff=4.0, tit=1000, gs=8, alt=300, ias=0),
        _seg(16, rpm=1000, map=12, ff=3.0, tit=900, gs=0, alt=300, ias=0),
        _seg(20, rpm=0, map=0, ff=0, tit=800, gs=0, alt=300, ias=0, oil_t=160),
    ])


def _status(review, id_):
    for check in review["checks"]:
        if check["id"] == id_:
            return check["status"]
    raise AssertionError(id_)


def _check(review, id_):
    for check in review["checks"]:
        if check["id"] == id_:
            return check
    raise AssertionError(id_)


class ReviewTests(unittest.TestCase):
    def test_textbook_phases_and_takeoff_ff(self):
        review = review_trace(_textbook(), Limits())
        names = [p["name"] for p in review["phases"]]
        for needed in ("taxi", "takeoff", "climb", "cruise", "descent"):
            self.assertIn(needed, names, names)
        self.assertEqual(_status(review, "takeoff_ff"), "pass")
        self.assertEqual(_status(review, "climb_ias"), "pass")
        self.assertIn("gps.alt_msl", review["notes"][0])
        self.assertGreater(review["metrics"]["cht3_offset_cruise_f"], 10)

    def test_takeoff_ff_follows_oat(self):
        hot = _trace([
            _seg(120, rpm=1100, map=14, ff=4, gs=5, alt=300, ias=0, oat=40),  # 104°F → 33 gph
            _seg(40, rpm=2700, map=29.6, ff=28, tit=1280, gs=60, alt=500, ias=80, oat=40, oil_t=180),
            _seg(180, rpm=2500, map=29.5, ff=16.5, tit=1500, gs=140, alt=4000, ias=140, oat=40),
        ])
        review = review_trace(hot, Limits())
        self.assertEqual(_status(review, "takeoff_ff"), "fail")
        self.assertIn("33", _check(review, "takeoff_ff")["limit"])

    def test_ias_saturated_is_a_skip(self):
        tr = _textbook()
        tr.ias_kt = np.full(tr.n, 230.0)
        tr.ias_note = "saturated"
        review = review_trace(tr, Limits())
        self.assertEqual(_status(review, "climb_ias"), "skip")

    def test_mag_asymmetry_and_egt_fall(self):
        n_base = 36
        rpm = np.full(n_base, 1700.0)
        rpm[8:12] = 1550.0  # L drop 150
        rpm[20:24] = 1620.0  # R drop 80
        egt = np.full((n_base, 6), 1400.0)
        egt[8:12, :] = 1430.0
        egt[8:12, 2] = 1380.0  # cylinder 3 falls
        egt[20:24, :] = 1430.0
        tr = _trace([
            (n_base, {"rpm": rpm, "map": 16, "ff": 8, "gs": 0, "alt": 300, "ias": 0, "egt": egt}),
            _seg(30, rpm=2700, map=29.6, ff=35, tit=1280, gs=60, alt=500, ias=80),
        ])
        mag = detect_mag(tr, Limits())
        self.assertIsNotNone(mag)
        review = review_trace(tr, Limits())
        self.assertEqual(_status(review, "mag_check"), "fail")
        self.assertIn("fell", _check(review, "mag_check")["detail"])

    def test_boost_steps_and_vapor(self):
        n = 40
        ff = np.full(n, 16.0)
        egt = np.full((n, 6), 1400.0)
        mp = np.full(n, 29.5)
        ff[20:] = 16.5
        egt[20:, :] = 1420.0
        tr = _trace([(n, {"rpm": 2500, "ff": ff, "map": 29.5, "gs": 140, "alt": 8000, "egt": egt})])
        tr.map_inhg = mp
        steps = find_boost_steps(tr, np.ones(n, dtype=bool), Limits())
        self.assertTrue(any(s.kind == "lo" for s in steps), steps)

        ff2 = np.full(n, 16.0)
        egt2 = np.full((n, 6), 1400.0)
        ff2[20:] = 15.3
        egt2[20:, :] = 1420.0
        tr2 = _trace([(n, {"rpm": 2500, "ff": ff2, "gs": 140, "alt": 8000, "egt": egt2})])
        drops = find_boost_steps(tr2, np.ones(n, dtype=bool), Limits())
        self.assertTrue(any(s.kind == "lo_ff_drop" for s in drops), drops)

        ff3 = np.full(n, 16.0)
        ff3[10:30] += np.array([0.6, -0.6] * 10)
        tr3 = _trace([(n, {"rpm": 2500, "ff": ff3, "gs": 140, "alt": 8000})])
        vapor = find_vapor(tr3, np.ones(n, dtype=bool), Limits())
        self.assertTrue(any(v.kind == "wiggle" for v in vapor), vapor)

    def test_red_box_and_cht3(self):
        # 20°F LOP at 75% power is inside the red box.
        n = int(8 * 60 / 2)
        tit = np.full(n, 1580.0)
        tit[10:18] = 1600.0
        tit[8:10] = 1540.0
        tit[18:20] = 1540.0
        ff = np.full(n, 16.0)
        ff[10:18] = 18.0
        tr = _trace([(n, {"rpm": 2500, "map": 29.5, "ff": ff, "tit": tit, "gs": 150, "alt": 8000, "ias": 150})], hp=75.0)
        review = review_trace(tr, Limits())
        self.assertEqual(_status(review, "cruise_redbox"), "fail")
        self.assertGreater(review["metrics"].get("cht3_offset_cruise_f") or 0, 10)

    def test_raw_jpi_without_ias_skips_the_speed_check(self):
        tr = _textbook()
        tr.ias_kt = None
        tr.ias_note = "missing"
        tr.source = "jpi"
        review = review_trace(tr, Limits())
        self.assertEqual(_status(review, "climb_ias"), "skip")


if __name__ == "__main__":
    unittest.main()
