"""Round-trip the aligned table, including replace-on-second-run."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from analysis.jpi.align import align_flight
from analysis.jpi.cli import cmd_jpi
from analysis.jpi.parse import Flight, Sample
from analysis.jpi.store import TABLE, write_flight


def _build():
    start = datetime(2024, 6, 1, 18, 0, 0)
    duration = 900.0
    jpi_t = np.arange(0.0, duration, 6.0)
    t_ref = float(jpi_t.mean())
    offset = 95.0
    drift = 0.0005
    true_kf = jpi_t + offset + drift * (jpi_t - t_ref)
    kf_t = np.arange(-120.0, duration + 250.0, 1.0)
    kf_alt = 2500.0 + 700.0 * np.sin(2 * np.pi * kf_t / duration)
    jpi_alt = np.interp(true_kf, kf_t, kf_alt)
    base = start.replace(tzinfo=timezone.utc).timestamp()
    samples = []
    for t, alt in zip(jpi_t, jpi_alt):
        samples.append(
            Sample(
                t=start + timedelta(seconds=float(t)),
                values={"alt_ft": float(alt), "rpm": 2300.0, "map_inhg": 29.0},
            )
        )
    flight = Flight(
        flight_id=7,
        interval_s=6,
        samples=samples,
        channels=["rpm", "map_inhg", "alt_ft"],
    )
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE press_alt (ts_ns INTEGER, pressure_alt_ft REAL)")
    conn.executemany(
        "INSERT INTO press_alt(ts_ns, pressure_alt_ft) VALUES (?, ?)",
        [((base + t) * 1e9, float(a)) for t, a in zip(kf_t, kf_alt)],
    )
    result = align_flight(flight, conn, window_s=900.0)
    return conn, flight, result, base, true_kf


class WriteTests(unittest.TestCase):
    def test_round_trip_and_replace(self):
        conn, flight, result, base, true_kf = _build()
        self.assertTrue(result.ok, result.summary())
        n = write_flight(conn, flight, result, "synthetic_edm930.jpi")
        self.assertEqual(n, len(flight.samples))
        rows = conn.execute(
            f'SELECT ts_ns, jpi_time, rpm, map_inhg, alt_ft FROM "{TABLE}" ORDER BY ts_ns'
        ).fetchall()
        self.assertEqual(len(rows), n)
        # First aligned stamp matches the known warp, within the fit tolerance.
        got0 = rows[0][0] / 1e9
        self.assertAlmostEqual(got0, base + true_kf[0], delta=1.5)
        self.assertEqual(rows[0][1], "2024-06-01T18:00:00")
        self.assertEqual(rows[0][2], 2300.0)
        self.assertIsNotNone(rows[0][4])
        meta = dict(conn.execute("SELECT key, value FROM metadata").fetchall())
        self.assertEqual(meta["jpi_edm930_method"], "altitude_roc")
        self.assertEqual(meta["jpi_edm930_flight_id"], "7")
        self.assertEqual(meta["jpi_edm930_source"], "synthetic_edm930.jpi")
        self.assertIn("jpi_edm930_drift_s_per_s", meta)
        self.assertIn("jpi_edm930_drift_s_per_h", meta)
        drift = float(meta["jpi_edm930_drift_s_per_s"])
        self.assertAlmostEqual(drift, 0.0005, delta=3e-4)

        # Second run replaces; it does not append.
        write_flight(conn, flight, result, "synthetic_edm930.jpi")
        count = conn.execute(f'SELECT COUNT(*) FROM "{TABLE}"').fetchone()[0]
        self.assertEqual(count, n)
        keys = conn.execute(
            "SELECT COUNT(*) FROM metadata WHERE key LIKE 'jpi_edm930_%'"
        ).fetchone()[0]
        self.assertEqual(keys, 12)

    def test_refused_match_does_not_write(self):
        conn, flight, result, _base, _true = _build()
        result.ok = False
        result.reason = "test refusal"
        with self.assertRaises(ValueError):
            write_flight(conn, flight, result, "x.jpi")
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        self.assertNotIn(TABLE, tables)


class CliTests(unittest.TestCase):
    def test_dry_run_then_apply_then_refuse(self):
        # Build a download whose altitude matches a temp DB, via the parser,
        # would be heavy. Drive the CLI with a CSV whose times match.
        start = datetime(2024, 6, 1, 18, 0, 0)
        duration = 900.0
        step = 6.0
        times = np.arange(0.0, duration, step)
        t_ref = float(times.mean())
        offset = 80.0
        drift = 0.0
        kf_t = np.arange(-60.0, duration + 200.0, 1.0)
        kf_alt = 4000.0 + 600.0 * np.sin(2 * np.pi * kf_t / duration)
        true_kf = times + offset + drift * (times - t_ref)
        jpi_alt = np.interp(true_kf, kf_t, kf_alt)
        lines = ["DATE,RPM,MAP,ALT"]
        for t, alt in zip(times, jpi_alt):
            stamp = start + timedelta(seconds=float(t))
            lines.append(f"{stamp:%Y-%m-%d %H:%M:%S},2400,28.5,{alt:.2f}")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            jpi = root / "flight.csv"
            db = root / "flight.sqlite"
            jpi.write_text("\n".join(lines) + "\n", encoding="utf-8")
            base = start.replace(tzinfo=timezone.utc).timestamp()
            conn = sqlite3.connect(db)
            conn.execute("CREATE TABLE press_alt (ts_ns INTEGER, pressure_alt_ft REAL)")
            conn.executemany(
                "INSERT INTO press_alt(ts_ns, pressure_alt_ft) VALUES (?, ?)",
                [((base + t) * 1e9, float(a)) for t, a in zip(kf_t, kf_alt)],
            )
            conn.commit()
            conn.close()

            ns = _args(jpi, db, apply=False, flight=None)
            self.assertEqual(cmd_jpi(ns), 0)
            conn = sqlite3.connect(db)
            tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            conn.close()
            self.assertNotIn("jpi_edm930", tables)

            ns_apply = _args(jpi, db, apply=True, flight=1)
            self.assertEqual(cmd_jpi(ns_apply), 0)
            conn = sqlite3.connect(db)
            n = conn.execute('SELECT COUNT(*) FROM jpi_edm930').fetchone()[0]
            ts0 = conn.execute("SELECT ts_ns FROM jpi_edm930 ORDER BY ts_ns LIMIT 1").fetchone()[0]
            conn.close()
            self.assertEqual(n, len(times))
            self.assertAlmostEqual(ts0 / 1e9, base + true_kf[0], delta=1.5)

            # Uncorrelated file must exit non-zero and leave the previous table intact.
            bad_lines = ["DATE,RPM,ALT"]
            rng = np.random.default_rng(2)
            walk = 10000 + np.cumsum(rng.normal(0, 40, size=len(times)))
            for t, alt in zip(times, walk):
                stamp = start + timedelta(seconds=float(t))
                bad_lines.append(f"{stamp:%Y-%m-%d %H:%M:%S},2400,{alt:.1f}")
            bad = root / "bad.csv"
            bad.write_text("\n".join(bad_lines) + "\n", encoding="utf-8")
            self.assertEqual(cmd_jpi(_args(bad, db, apply=True, flight=1)), 1)
            conn = sqlite3.connect(db)
            n2 = conn.execute("SELECT COUNT(*) FROM jpi_edm930").fetchone()[0]
            conn.close()
            self.assertEqual(n2, n)


def _args(jpi, db, *, apply: bool, flight):
    import argparse

    return argparse.Namespace(
        jpi=jpi,
        db=db,
        flight=flight,
        apply=apply,
        window_s=900.0,
    )


if __name__ == "__main__":
    unittest.main()
