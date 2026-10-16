"""Alignment: known offset and drift come back; an uncorrelated trace is refused."""

from __future__ import annotations

import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

import numpy as np

from analysis.jpi.align import align_flight
from analysis.jpi.parse import Flight, Sample


def _unix(when: datetime) -> float:
    return when.replace(tzinfo=timezone.utc).timestamp()


def _flight(start: datetime, times_s: np.ndarray, channels: dict[str, np.ndarray]) -> Flight:
    samples = []
    names = []
    for name, arr in channels.items():
        if np.isfinite(arr).any():
            names.append(name)
    for i, t in enumerate(times_s):
        values = {}
        for name in names:
            val = channels[name][i]
            values[name] = None if not np.isfinite(val) else float(val)
        samples.append(Sample(t=start + timedelta(seconds=float(t)), values=values))
    return Flight(
        flight_id=1,
        interval_s=6,
        samples=samples,
        channels=names,
        temp_unit="F",
        oat_unit="F",
    )


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE gps (ts_ns INTEGER, lat REAL, lon REAL, alt_msl REAL)")
    conn.execute(
        "CREATE TABLE press_alt (ts_ns INTEGER, pressure_alt_ft REAL, indicated_alt_ft REAL)"
    )
    conn.execute("CREATE TABLE bmp581 (ts_ns INTEGER, static_temp_c REAL)")
    return conn


class AltitudeAlignTests(unittest.TestCase):
    def test_recovers_offset_and_drift_and_ignores_oat(self):
        rng_start = datetime(2024, 6, 1, 18, 0, 0)
        duration = 1200.0
        jpi_t = np.arange(0.0, duration, 6.0)
        t_ref = float(np.mean(jpi_t))
        offset = 137.0
        drift = 0.0012
        # Kingfisher clock covers the search window around the JPI span.
        kf_t = np.arange(-200.0, duration + 400.0, 1.0)
        kf_alt = 2000.0 + 900.0 * np.sin(2 * np.pi * kf_t / duration) + 1.5 * kf_t
        true_kf = jpi_t + offset + drift * (jpi_t - t_ref)
        jpi_alt = np.interp(true_kf, kf_t, kf_alt) + 180.0  # altimeter bias

        # OAT correlates at a different lag on purpose.
        kf_temp = 15.0 + 8.0 * np.sin(2 * np.pi * kf_t / duration)
        oat_lag = 420.0
        jpi_oat_c = np.interp(jpi_t + oat_lag, kf_t, kf_temp)
        jpi_oat_f = jpi_oat_c * 9.0 / 5.0 + 32.0

        base = _unix(rng_start)
        conn = _conn()
        conn.executemany(
            "INSERT INTO press_alt(ts_ns, pressure_alt_ft) VALUES (?, ?)",
            [((base + t) * 1e9, float(a)) for t, a in zip(kf_t, kf_alt)],
        )
        conn.executemany(
            "INSERT INTO bmp581(ts_ns, static_temp_c) VALUES (?, ?)",
            [((base + t) * 1e9, float(c)) for t, c in zip(kf_t, kf_temp)],
        )
        flight = _flight(rng_start, jpi_t, {"alt_ft": jpi_alt, "oat_f": jpi_oat_f})
        result = align_flight(flight, conn, window_s=900.0)
        self.assertTrue(result.ok, result.summary())
        self.assertEqual(result.method, "altitude_roc")
        self.assertAlmostEqual(result.offset_s, offset, delta=1.0)
        self.assertAlmostEqual(result.drift_s_per_s, drift, delta=2.5e-4)
        self.assertIsNotNone(result.oat_pearson)
        self.assertEqual(result.oat_source, "bmp581.static_temp_c")
        self.assertGreater(abs(result.oat_best_offset_s - result.offset_s), 60.0)

    def test_uncorrelated_altitude_is_refused(self):
        start = datetime(2024, 6, 1, 18, 0, 0)
        duration = 1200.0
        jpi_t = np.arange(0.0, duration, 6.0)
        kf_t = np.arange(-100.0, duration + 200.0, 1.0)
        kf_alt = 3000.0 + 1200.0 * np.sin(2 * np.pi * kf_t / 400.0)
        rng = np.random.default_rng(1)
        jpi_alt = 5000.0 + np.cumsum(rng.normal(0.0, 30.0, size=jpi_t.shape))
        base = _unix(start)
        conn = _conn()
        conn.executemany(
            "INSERT INTO press_alt(ts_ns, pressure_alt_ft) VALUES (?, ?)",
            [((base + t) * 1e9, float(a)) for t, a in zip(kf_t, kf_alt)],
        )
        flight = _flight(start, jpi_t, {"alt_ft": jpi_alt})
        result = align_flight(flight, conn, window_s=900.0)
        self.assertFalse(result.ok, result.summary())
        self.assertEqual(result.method, "altitude_roc")
        self.assertIn("correlation", result.reason)


class GpsAlignTests(unittest.TestCase):
    def test_track_beats_a_decoy_altitude(self):
        start = datetime(2024, 6, 1, 18, 0, 0)
        duration = 1800.0
        jpi_t = np.arange(0.0, duration, 6.0)
        t_ref = float(np.mean(jpi_t))
        offset = -240.0
        drift = -0.0008
        true_kf = jpi_t + offset + drift * (jpi_t - t_ref)
        # A dogleg, so a pure time shift is the only good match.
        lat = 33.0 + 0.02 * (jpi_t / duration)
        lon = np.where(jpi_t < duration / 2, -112.0, -112.0 - 0.03 * (jpi_t - duration / 2) / duration)
        kf_t = np.arange(-400.0, duration + 200.0, 1.0)
        # Decoy altitude that would prefer offset 0.
        kf_alt = 1000.0 + 2.0 * kf_t
        jpi_alt = 1000.0 + 2.0 * jpi_t
        base = _unix(start)
        conn = _conn()
        # GPS samples on the Kingfisher clock along the same dogleg.
        kf_lat = np.interp(kf_t, true_kf, lat, left=np.nan, right=np.nan)
        kf_lon = np.interp(kf_t, true_kf, lon, left=np.nan, right=np.nan)
        ok = np.isfinite(kf_lat)
        conn.executemany(
            "INSERT INTO gps(ts_ns, lat, lon) VALUES (?, ?, ?)",
            [
                ((base + t) * 1e9, float(la), float(lo))
                for t, la, lo in zip(kf_t[ok], kf_lat[ok], kf_lon[ok])
            ],
        )
        conn.executemany(
            "INSERT INTO press_alt(ts_ns, pressure_alt_ft) VALUES (?, ?)",
            [((base + t) * 1e9, float(a)) for t, a in zip(kf_t, kf_alt)],
        )
        flight = _flight(start, jpi_t, {"lat_deg": lat, "lon_deg": lon, "alt_ft": jpi_alt})
        result = align_flight(flight, conn, window_s=900.0)
        self.assertTrue(result.ok, result.summary())
        self.assertEqual(result.method, "gps_track")
        self.assertAlmostEqual(result.offset_s, offset, delta=1.5)
        self.assertAlmostEqual(result.drift_s_per_s, drift, delta=3e-4)

    def test_held_fixes_do_not_set_the_residual(self):
        # Cruise north at 150 kt. JPI repeats each fix for 12 s. Scoring the
        # holds would leave a median staleness of several hundred meters.
        start = datetime(2024, 6, 1, 18, 0, 0)
        duration = 900.0
        step = 2.0
        jpi_t = np.arange(0.0, duration, step)
        t_ref = float(np.mean(jpi_t))
        offset = 80.0
        drift = 0.0005
        speed = 77.0  # m/s
        deg_per_s = speed / 111_320.0
        # Hold each 12 s fix across the following 2 s samples.
        hold = np.floor(jpi_t / 12.0) * 12.0
        lat = 35.0 + deg_per_s * hold
        lon = np.full_like(lat, -110.0)
        kf_t = np.arange(-200.0, duration + 200.0, 1.0)
        # Continuous track on the Kingfisher clock: invert the warp so a
        # fix taken at JPI time τ sits on the KF sample at τ+offset+drift.
        t_j_of_kf = (kf_t - offset + drift * t_ref) / (1.0 + drift)
        kf_lat = 35.0 + deg_per_s * t_j_of_kf
        base = _unix(start)
        conn = _conn()
        conn.executemany(
            "INSERT INTO gps(ts_ns, lat, lon) VALUES (?, ?, ?)",
            [((base + t) * 1e9, float(la), -110.0) for t, la in zip(kf_t, kf_lat)],
        )
        flight = _flight(start, jpi_t, {"lat_deg": lat, "lon_deg": lon})
        result = align_flight(flight, conn, window_s=900.0)
        self.assertTrue(result.ok, result.summary())
        self.assertEqual(result.method, "gps_track")
        self.assertAlmostEqual(result.offset_s, offset, delta=1.5)
        self.assertAlmostEqual(result.drift_s_per_s, drift, delta=3e-4)
        self.assertLess(result.residual, 50.0)

    def test_disjoint_track_is_refused(self):
        start = datetime(2024, 6, 1, 18, 0, 0)
        duration = 900.0
        jpi_t = np.arange(0.0, duration, 6.0)
        lat = 33.0 + 0.2 * (jpi_t / duration)
        lon = np.full_like(lat, -112.0)
        kf_t = np.arange(-100.0, duration + 100.0, 1.0)
        kf_lat = np.full_like(kf_t, 34.2)
        kf_lon = -112.0 + 0.2 * (kf_t / duration)
        base = _unix(start)
        conn = _conn()
        conn.executemany(
            "INSERT INTO gps(ts_ns, lat, lon) VALUES (?, ?, ?)",
            [((base + t) * 1e9, float(la), float(lo)) for t, la, lo in zip(kf_t, kf_lat, kf_lon)],
        )
        flight = _flight(start, jpi_t, {"lat_deg": lat, "lon_deg": lon})
        result = align_flight(flight, conn, window_s=900.0)
        self.assertFalse(result.ok, result.summary())
        self.assertIn("750", result.reason)


if __name__ == "__main__":
    unittest.main()
