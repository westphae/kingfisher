"""Auto-ingest is idempotent and does not replace a refused re-fit."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from analysis.jpi.align import AlignResult
from analysis.jpi.auto import run_auto
from analysis.jpi.duplicates import flag_sessions


def _ok():
    return AlignResult(
        ok=True,
        method="gps_track",
        offset_s=10.0,
        drift_s_per_s=0.0,
        t_ref_unix=1_700_000_000.0,
        residual=40.0,
        residual_unit="m",
        detail="synthetic",
        n_points=20,
    )


def _refused():
    return AlignResult(
        ok=False,
        method="gps_track",
        offset_s=0.0,
        drift_s_per_s=0.0,
        t_ref_unix=0.0,
        residual=float("nan"),
        residual_unit="",
        detail="",
        reason="median error 900 m exceeds 750 m",
    )


def _csv(path: Path, marker: str) -> None:
    start = datetime(2024, 6, 1, 18, 0, 0)
    lines = ["DATE,TIME,RPM,MAP,FF,LAT,LONG,OAT"]
    for i in range(30):
        t = start + timedelta(seconds=6 * i)
        lines.append(
            f"{t.strftime('%m/%d/%Y')},{t.strftime('%H:%M:%S')},2400,29.0,15.0,"
            f"{33.4 + i * 0.01:.4f},{-112.0 - i * 0.01:.4f},60"
        )
    lines.append(f"# {marker}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _db(path: Path, *, gs: float = 50.0, rows: int = 40, clock_step_ns: int | None = None) -> None:
    start = datetime(2024, 6, 1, 17, 55, tzinfo=timezone.utc)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE gps (ts_ns INTEGER, lat REAL, lon REAL, gs REAL, alt_msl REAL)")
    base = int(start.timestamp() * 1e9)
    for i in range(rows):
        conn.execute(
            "INSERT INTO gps VALUES (?,?,?,?,?)",
            (base + i * int(1e9), 33.4, -112.0, gs, 1000.0),
        )
    if clock_step_ns is not None:
        conn.execute("CREATE TABLE clock_offsets (ts_ns INTEGER, delta_ns INTEGER, note TEXT)")
        conn.execute(
            "INSERT INTO clock_offsets VALUES (?,?,?)",
            (base, clock_step_ns, "step"),
        )
    conn.commit()
    conn.close()


class AutoTests(unittest.TestCase):
    def test_apply_then_skip_then_keep_on_refusal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            flights = root / "flights"
            raw = root / "raw"
            flights.mkdir()
            raw.mkdir()
            db = flights / "flight.db"
            jpi = raw / "U240601.JPI"
            _db(db)
            _csv(jpi, "one")
            ledger = root / "ingest.json"
            with mock.patch("analysis.jpi.auto.align_flight", return_value=_ok()) as fit:
                run_auto(flights_dir=flights, jpi_dir=raw, ledger_path=ledger, apply=True)
                self.assertEqual(fit.call_count, 1)
            conn = sqlite3.connect(db)
            n = conn.execute("SELECT COUNT(*) FROM jpi_edm930").fetchone()[0]
            sha = conn.execute(
                "SELECT value FROM metadata WHERE key='jpi_edm930_sha256'"
            ).fetchone()[0]
            conn.close()
            self.assertGreater(n, 0)
            self.assertTrue(sha)
            with mock.patch("analysis.jpi.auto.align_flight", return_value=_ok()) as fit:
                run_auto(flights_dir=flights, jpi_dir=raw, ledger_path=ledger, apply=True)
                self.assertEqual(fit.call_count, 0)
            data = json.loads(ledger.read_text())
            skipped = [a for a in data["attempts"] if a["status"] == "skipped"]
            self.assertEqual(len(skipped), 1)
            with mock.patch("analysis.jpi.auto.align_flight", return_value=_ok()) as fit:
                run_auto(flights_dir=flights, jpi_dir=raw, ledger_path=ledger, apply=True)
                self.assertEqual(fit.call_count, 0)
            data = json.loads(ledger.read_text())
            skipped = [a for a in data["attempts"] if a["status"] == "skipped"]
            self.assertEqual(len(skipped), 1)
            self.assertGreater(skipped[0]["count"], 1)
            _csv(jpi, "two")
            with mock.patch("analysis.jpi.auto.align_flight", return_value=_refused()):
                run_auto(flights_dir=flights, jpi_dir=raw, ledger_path=ledger, apply=True)
            conn = sqlite3.connect(db)
            n2 = conn.execute("SELECT COUNT(*) FROM jpi_edm930").fetchone()[0]
            sha2 = conn.execute(
                "SELECT value FROM metadata WHERE key='jpi_edm930_sha256'"
            ).fetchone()[0]
            conn.close()
            self.assertEqual(n2, n)
            self.assertEqual(sha2, sha)
            again = json.loads(ledger.read_text())
            self.assertTrue(any(a["status"] == "refused" for a in again["attempts"]))

    def test_dry_run_does_not_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            flights = root / "flights"
            raw = root / "raw"
            flights.mkdir()
            raw.mkdir()
            _db(flights / "flight.db")
            _csv(raw / "U240601.JPI", "dry")
            with mock.patch("analysis.jpi.auto.align_flight", return_value=_ok()):
                run_auto(
                    flights_dir=flights,
                    jpi_dir=raw,
                    ledger_path=root / "ingest.json",
                    apply=False,
                )
            conn = sqlite3.connect(flights / "flight.db")
            present = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name='jpi_edm930'"
            ).fetchone()
            conn.close()
            self.assertIsNone(present)

    def test_duplicate_and_clock_step_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            flights = Path(tmp)
            _db(flights / "20260712T210041Z.db", gs=51.25, rows=80)
            _db(flights / "20260718T165100Z.db", gs=51.25, rows=80, clock_step_ns=90 * 10**9)
            conn = sqlite3.connect(flights / "20260718T165100Z.db")
            conn.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)")
            conn.execute(
                "INSERT INTO metadata VALUES ('jpi_edm930_method','gps_track'),"
                "('jpi_edm930_residual','621')"
            )
            conn.commit()
            conn.close()
            flags = flag_sessions(flights)
            kinds = {f["kind"] for f in flags}
            self.assertIn("suspected_duplicate", kinds)
            self.assertIn("clock_step", kinds)
            dup = next(f for f in flags if f["kind"] == "suspected_duplicate")
            self.assertIn("621", dup["detail"])


if __name__ == "__main__":
    unittest.main()
