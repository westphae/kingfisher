"""Synthetic checks for taxi mag calibration and the IMU mount fit."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from analysis.cal_mag import analyze_mag, find_transients, fit_ellipsoid
from analysis.cal_mount import analyze_mount
from analysis.epochs import load_epochs
from analysis.geom import G0, R_from_rpy, kabsch, rpy_from_R


class GeomTest(unittest.TestCase):
    def test_rpy_roundtrip(self) -> None:
        rpy = np.deg2rad([12.0, -7.0, 40.0])
        R = R_from_rpy(*rpy)
        got = np.array(rpy_from_R(R))
        np.testing.assert_allclose(got, rpy, atol=1e-10)

    def test_kabsch_recovers_rotation(self) -> None:
        R = R_from_rpy(*np.deg2rad([5.0, -3.0, 20.0]))
        rng = np.random.default_rng(1)
        src = rng.normal(size=(30, 3))
        dst = src @ R.T
        got = kabsch(src, dst)
        np.testing.assert_allclose(got, R, atol=1e-8)


class MagTest(unittest.TestCase):
    def test_swing_recovers_hard_iron_and_heading(self) -> None:
        H, Z = 23.0, 41.0
        b = np.array([4.0, -2.5, 7.0])
        s = 1.15
        R = R_from_rpy(*np.deg2rad([10.0, -20.0, 35.0]))
        psi = np.linspace(0.0, 350.0, 80)
        rad = np.deg2rad(psi)
        B = np.column_stack([H * np.cos(rad), -H * np.sin(rad), np.full_like(rad, Z)])
        mag = s * (B @ R.T) + b
        ep = np.arange(len(psi))
        df = pd.DataFrame(
            {
                "ep": ep,
                "gs": np.full(len(psi), 10.0),
                "track": psi,  # declination 0 in meta below
                "track_rate": np.zeros(len(psi)),
                "mag_x_ut": mag[:, 0],
                "mag_y_ut": mag[:, 1],
                "mag_z_ut": mag[:, 2],
                "anglvel_x": 0.0,
                "anglvel_y": 0.0,
                "anglvel_z": 0.0,
            }
        )
        meta = {
            "declination_deg": 0.0,
            "wmm_h_ut": H,
            "wmm_z_ut": Z,
            "wmm_f_ut": float(np.hypot(H, Z)),
        }
        out = analyze_mag(df, meta)
        self.assertTrue(out["ok"], out.get("reason"))
        self.assertLess(out["in_sample_heading_rms_deg"], 0.5)
        self.assertLess(out["heldout_heading_rms_deg"], 0.5)
        np.testing.assert_allclose(out["hard_iron_ut"], b, atol=0.5)
        self.assertAlmostEqual(out["scale_x"], s, delta=0.05)
        self.assertAlmostEqual(out["scale_y"], s, delta=0.05)
        self.assertEqual(out["quality"], "good")

    def test_blip_and_step(self) -> None:
        n = 200
        ep = np.arange(n)
        mag = np.tile([20.0, 5.0, 40.0], (n, 1))
        mag[80:88, 0] += 5.0
        mag[140:, 1] += 3.0
        gyro = np.zeros((n, 3))
        gs = np.full(n, 1.0)
        rate = np.zeros(n)
        events = find_transients(ep, mag, gyro, gs, rate, field_ut=50.0)
        kinds = " ".join(e["kind"] for e in events)
        self.assertTrue(any(e["peak_ut"] > 4.0 for e in events), events)
        self.assertIn("blip", kinds)
        self.assertIn("step", kinds)
        self.assertNotIn("large", kinds)

    def test_taxi_turn_is_not_a_step_and_a_big_blip_is_large(self) -> None:
        n = 200
        ep = np.arange(n)
        mag = np.tile([20.0, 5.0, 40.0], (n, 1))
        mag[80:88, 0] += 4.0
        gyro = np.zeros((n, 3))
        # Straight taxi: a 4 µT blip is the motor shape.
        events = find_transients(
            ep, mag, gyro, np.full(n, 8.0), np.zeros(n), field_ut=50.0
        )
        self.assertTrue(any("blip" in e["kind"] and "large" not in e["kind"] for e in events), events)
        # Same blip during a turn is the nose moving, not a motor.
        turning = find_transients(
            ep, mag, gyro, np.full(n, 8.0), np.full(n, 8.0), field_ut=50.0
        )
        self.assertFalse(any("blip" in e["kind"] or "step" in e["kind"] for e in turning), turning)
        # 12 µT while parked is real, and too big to call a flap motor.
        mag2 = np.tile([20.0, 5.0, 40.0], (n, 1))
        mag2[80:88, 0] += 12.0
        big = find_transients(ep, mag2, gyro, np.full(n, 1.0), np.zeros(n), field_ut=50.0)
        self.assertTrue(any("large" in e["kind"] and e["peak_ut"] > 10.0 for e in big), big)

    def test_rotation_is_not_a_transient(self) -> None:
        n = 120
        ep = np.arange(n, dtype=float)
        w = 0.2  # rad/s about z
        field = 50.0
        mag = np.column_stack(
            [field * np.cos(w * ep), field * np.sin(w * ep), np.zeros(n)]
        )
        gyro = np.column_stack([np.zeros(n), np.zeros(n), np.full(n, w)])
        events = find_transients(
            ep.astype(np.int64), mag, gyro, np.full(n, 12.0), np.full(n, np.rad2deg(w)), field_ut=field
        )
        self.assertEqual(events, [])

    def test_ellipsoid(self) -> None:
        rng = np.random.default_rng(2)
        v = rng.normal(size=(3000, 3))
        v /= np.linalg.norm(v, axis=1, keepdims=True)
        radii = np.array([40.0, 30.0, 25.0])
        center = np.array([8.0, -4.0, 6.0])
        R = R_from_rpy(0.2, -0.3, 0.5)
        x = (v * radii) @ R.T + center
        fit = fit_ellipsoid(x)
        self.assertIsNotNone(fit)
        assert fit is not None
        np.testing.assert_allclose(fit["center_ut"], center, atol=0.5)
        np.testing.assert_allclose(sorted(fit["radii_ut"]), sorted(radii), atol=0.5)
        self.assertLess(fit["residual_rms"], 0.05)


class MountTest(unittest.TestCase):
    def test_recovers_mount(self) -> None:
        rpy = np.deg2rad([5.0, -3.0, 20.0])
        R = R_from_rpy(*rpy)
        n_park, n_turn = 40, 40
        n = n_park + n_turn
        ep = np.arange(n)
        w = np.zeros(n)
        w[n_park:] = np.where(np.arange(n_turn) % 2 == 0, 0.20, -0.18)
        v = np.zeros(n)
        v[n_park:] = 12.0
        gs = np.full(n, 0.3)
        gs[n_park:] = 12.0
        # z up, y right. Right turn: specific force out the right wing, gyro
        # about up is negative (right-hand about up is a left turn).
        f_level = np.column_stack([np.zeros(n), v * w, np.full(n, G0)])
        g_level = np.column_stack([np.zeros(n), np.zeros(n), -w])
        accel = f_level @ R
        gyro = g_level @ R
        # v_imu = R.T @ v_level, and R maps imu -> level, so v_level = R @ v_imu.
        # accel rows are imu vectors: f_imu.T = R.T @ f_level.T => f_imu = f_level @ R.
        # Yes: (R.T @ f)^T = f.T @ R.
        df = pd.DataFrame(
            {
                "ep": ep,
                "gs": gs,
                "track": np.zeros(n),
                "track_rate": np.rad2deg(w),
                "accel_x": accel[:, 0],
                "accel_y": accel[:, 1],
                "accel_z": accel[:, 2],
                "anglvel_x": gyro[:, 0],
                "anglvel_y": gyro[:, 1],
                "anglvel_z": gyro[:, 2],
            }
        )
        out = analyze_mount(df, {})
        self.assertTrue(out["ok"], out.get("reason"))
        self.assertTrue(out["yaw_observable"], out.get("notes"))
        self.assertAlmostEqual(out["roll_deg"], 5.0, delta=1.0)
        self.assertAlmostEqual(out["pitch_deg"], -3.0, delta=1.0)
        self.assertAlmostEqual(out["yaw_deg"], 20.0, delta=1.5)
        self.assertLess(out["gyro_rms_deg_s"], 1.0)


class EpochTest(unittest.TestCase):
    def test_track_average_crosses_north(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "t.db"
            conn = sqlite3.connect(path)
            conn.execute(
                "CREATE TABLE gps (ts_ns INTEGER, gs REAL, track REAL, lat REAL, lon REAL, vs REAL)"
            )
            # Two samples in the same second, either side of north.
            base = 1_700_000_000_000_000_000
            rows = [
                (base + 100, 10.0, 350.0, 32.0, -83.0, 0.0),
                (base + 200, 10.0, 10.0, 32.0, -83.0, 0.0),
            ]
            # A later steady turn, separated by a gap so it does not join the first second.
            for i in range(6):
                rows.append((base + (20 + i) * 1_000_000_000, 10.0, i * 18.0, 32.0, -83.0, 0.0))
            conn.executemany("INSERT INTO gps VALUES (?,?,?,?,?,?)", rows)
            conn.commit()
            df, _meta = load_epochs(conn)
            conn.close()
        self.assertEqual(len(df), 7)
        first = float(df["track"].iloc[0])
        # 350° and 10° average to 0°, not 180°.
        self.assertLess(min(first, 360.0 - first), 2.0)
        # Interior of a 18°/s turn. The samples that touch the gap are dropped.
        mid = float(df["track_rate"].iloc[4])
        self.assertLess(abs(mid - 18.0), 1.0)


if __name__ == "__main__":
    unittest.main()
