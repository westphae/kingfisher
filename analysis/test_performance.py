"""Equation checks for analysis.performance. No flight results are asserted."""

from __future__ import annotations

import math
import sqlite3
import unittest

from analysis.performance import (
    FLIGHT_GS_KT,
    ISA_P0_PA,
    MPS_PER_KT,
    cas_kt,
    density_alt_ft,
    specific_excess_power_mps,
    summarize,
    tas_kt,
    wind_ned_kt,
)


def _qc_for_cas(cas: float) -> float:
    v = cas * MPS_PER_KT
    a = math.sqrt(1.4 * 287.053 * 288.15)
    mach = v / a
    return ISA_P0_PA * ((1.0 + 0.2 * mach * mach) ** 3.5 - 1.0)


class EquationTests(unittest.TestCase):
    def test_cas_roundtrip_100kt(self):
        got = cas_kt(_qc_for_cas(100.0))
        self.assertIsNotNone(got)
        self.assertAlmostEqual(got, 100.0, delta=0.05)

    def test_tas_matches_cas_at_sea_level_isa(self):
        qc = _qc_for_cas(150.0)
        self.assertAlmostEqual(tas_kt(qc, ISA_P0_PA, 15.0), cas_kt(qc), delta=0.05)

    def test_hot_day_raises_tas(self):
        qc = _qc_for_cas(100.0)
        cold = tas_kt(qc, ISA_P0_PA, 15.0)
        hot = tas_kt(qc, ISA_P0_PA, 30.0)
        self.assertGreater(hot, cold)

    def test_density_altitude_isa_and_hot(self):
        self.assertAlmostEqual(density_alt_ft(ISA_P0_PA, 15.0), 0.0, delta=5.0)
        hot = density_alt_ft(ISA_P0_PA, 30.0)
        self.assertGreater(hot, 1600)
        self.assertLess(hot, 1850)

    def test_specific_excess_power_steady(self):
        self.assertAlmostEqual(specific_excess_power_mps(2.0, 70.0, 0.0), 2.0)

    def test_wind_zero_and_crosswind(self):
        vn, ve = wind_ned_kt(100.0, 0.0, 100.0, 0.0)
        self.assertAlmostEqual(vn, 0.0, delta=1e-6)
        self.assertAlmostEqual(ve, 0.0, delta=1e-6)
        # Air velocity north 100 kt, wind from the west 10 kt (east component +10).
        # Ground velocity = (100 north, 10 east). Track = atan2(10, 100).
        gs = math.hypot(100.0, 10.0)
        track = math.degrees(math.atan2(10.0, 100.0))
        vn, ve = wind_ned_kt(100.0, 0.0, gs, track)
        self.assertAlmostEqual(vn, 0.0, delta=1e-6)
        self.assertAlmostEqual(ve, 10.0, delta=1e-6)


class SummaryTests(unittest.TestCase):
    def test_recomputes_from_raw_columns(self):
        conn = sqlite3.connect(":memory:")
        conn.executescript(
            """
            CREATE TABLE gps (ts_ns INTEGER, gs REAL, track REAL);
            CREATE TABLE ms4525 (ts_ns INTEGER, airspeed_dp_pa REAL);
            CREATE TABLE bmp581 (
              ts_ns INTEGER, static_pressure_pa REAL, static_temp_c REAL
            );
            CREATE TABLE press_alt (ts_ns INTEGER, vs_ms REAL, pressure_alt_ft REAL);
            CREATE TABLE compass (ts_ns INTEGER, heading_true_deg REAL);
            """
        )
        qc = _qc_for_cas(120.0)
        # 60 m/s ≈ 116.6 kt, above the flight threshold, wings-level vs.
        ts0 = 1_700_000_000_000_000_000
        for i in range(5):
            ts = ts0 + i * 1_000_000_000
            conn.execute("INSERT INTO gps VALUES (?,?,?)", (ts, 60.0, 0.0))
            conn.execute("INSERT INTO ms4525 VALUES (?,?)", (ts, qc))
            conn.execute(
                "INSERT INTO bmp581 VALUES (?,?,?)", (ts, ISA_P0_PA, 15.0)
            )
            conn.execute("INSERT INTO press_alt VALUES (?,?,?)", (ts, 0.0, 0.0))
            conn.execute("INSERT INTO compass VALUES (?,?)", (ts, 0.0))
        s = summarize(conn, dp_zero_pa=0.0)
        self.assertGreaterEqual(s.max_gs_kt, FLIGHT_GS_KT)
        self.assertEqual(s.n_cruise, 5)
        self.assertAlmostEqual(s.cruise_cas_kt, 120.0, delta=0.1)
        self.assertAlmostEqual(s.cruise_tas_kt, 120.0, delta=0.1)
        self.assertAlmostEqual(s.cruise_da_ft, 0.0, delta=5.0)
        self.assertEqual(s.n_wind, 5)
        self.assertAlmostEqual(s.wind_n_kt, 60.0 / MPS_PER_KT - 120.0, delta=0.2)
        self.assertAlmostEqual(s.wind_e_kt, 0.0, delta=0.2)

    def test_missing_pitot_is_reported(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE gps (ts_ns INTEGER, gs REAL, track REAL)")
        conn.execute(
            "INSERT INTO gps VALUES (?,?,?)",
            (1_700_000_000_000_000_000, 5.0, None),
        )
        text = summarize(conn).text()
        self.assertIn("No pitot", text)
        self.assertIn("no airborne", text)


if __name__ == "__main__":
    unittest.main()
