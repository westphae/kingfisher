"""Auto-ingest is idempotent and does not replace a refused re-fit."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
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


def _db(path: Path, *, gs: float = 50.0, rows: int = 40, clock_step_ns: int | None = None, start=None) -> None:
    start = start or datetime(2024, 6, 1, 17, 55, tzinfo=timezone.utc)
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

    def test_two_records_share_a_clock_and_rerun_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            flights = root / "flights"
            raw = root / "raw"
            flights.mkdir()
            raw.mkdir()
            db = flights / "flight.db"
            jpi = raw / "U240601.JPI"
            _wide_db(db)
            _csv_two(jpi)
            ledger = root / "ingest.json"

            def fit(flight, conn, window_s=900.0):
                if "lat_deg" in flight.channels:
                    return _ok()
                return _refused()

            with mock.patch("analysis.jpi.auto.align_flight", side_effect=fit) as aligned:
                run_auto(flights_dir=flights, jpi_dir=raw, ledger_path=ledger, apply=True)
                self.assertEqual(aligned.call_count, 2)
            conn = sqlite3.connect(db)
            rows = conn.execute("SELECT capture_id, ts_ns FROM jpi_edm930 ORDER BY ts_ns").fetchall()
            meta = dict(conn.execute("SELECT key, value FROM metadata").fetchall())
            conn.close()
            ids = []
            for cid, _ts in rows:
                if cid not in ids:
                    ids.append(cid)
            self.assertEqual(len(ids), 2, ids)
            self.assertEqual(meta["jpi_edm930_captures"], "2")
            described = json.loads(meta["jpi_edm930_captures_json"])
            self.assertTrue(any(item.get("shared") for item in described), described)
            stamps = [ts for _cid, ts in rows]
            self.assertEqual(len(stamps), len(set(stamps)))
            n = len(rows)
            with mock.patch("analysis.jpi.auto.align_flight", side_effect=fit) as aligned:
                run_auto(flights_dir=flights, jpi_dir=raw, ledger_path=ledger, apply=True)
                self.assertEqual(aligned.call_count, 0)
            conn = sqlite3.connect(db)
            n2 = conn.execute("SELECT COUNT(*) FROM jpi_edm930").fetchone()[0]
            conn.close()
            self.assertEqual(n2, n)

    def test_overlapping_records_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            flights = root / "flights"
            raw = root / "raw"
            flights.mkdir()
            raw.mkdir()
            db = flights / "flight.db"
            _wide_db(db)
            _csv_overlap(raw / "U240601.JPI")
            ledger = root / "ingest.json"
            with mock.patch("analysis.jpi.auto.align_flight", return_value=_ok()):
                run_auto(flights_dir=flights, jpi_dir=raw, ledger_path=ledger, apply=True)
            conn = sqlite3.connect(db)
            n = conn.execute("SELECT COUNT(*) FROM jpi_edm930").fetchone()[0]
            distinct = conn.execute("SELECT COUNT(DISTINCT ts_ns) FROM jpi_edm930").fetchone()[0]
            caps = conn.execute("SELECT COUNT(DISTINCT capture_id) FROM jpi_edm930").fetchone()[0]
            conn.close()
            self.assertGreater(n, 0)
            self.assertEqual(n, distinct)
            self.assertEqual(caps, 1)
            data = json.loads(ledger.read_text())
            self.assertTrue(any(a["status"] == "overlap" for a in data["attempts"]))
            with mock.patch("analysis.jpi.auto.align_flight", return_value=_ok()):
                run_auto(flights_dir=flights, jpi_dir=raw, ledger_path=ledger, apply=True)
            conn = sqlite3.connect(db)
            n2 = conn.execute("SELECT COUNT(*) FROM jpi_edm930").fetchone()[0]
            conn.close()
            self.assertEqual(n2, n)


def _wide_db(path: Path) -> None:
    start = datetime(2024, 6, 1, 17, 50, tzinfo=timezone.utc)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE gps (ts_ns INTEGER, lat REAL, lon REAL, gs REAL, alt_msl REAL)")
    base = int(start.timestamp() * 1e9)
    conn.executemany(
        "INSERT INTO gps VALUES (?,?,?,?,?)",
        [(base + i * int(1e9), 33.4, -112.0, 80.0, 3000.0) for i in range(4000)],
    )
    conn.commit()
    conn.close()


def _csv_two(path: Path) -> None:
    lines = ["DATE,TIME,RPM,MAP,FF,LAT,LONG"]
    start = datetime(2024, 6, 1, 18, 0, 0)
    for i in range(25):
        t = start + timedelta(seconds=6 * i)
        lines.append(
            f"{t:%m/%d/%Y},{t:%H:%M:%S},2400,29.0,15.0,"
            f"{33.4 + i * 0.01:.4f},{-112.0 - i * 0.01:.4f}"
        )
    start2 = datetime(2024, 6, 1, 18, 12, 0)
    for i in range(8):
        t = start2 + timedelta(seconds=6 * i)
        lines.append(f"{t:%m/%d/%Y},{t:%H:%M:%S},1000,12.0,3.0,,")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _touch_old(path: Path) -> None:
    past = time.time() - 7 * 86400
    os.utime(path, (past, past))


class BoundsTests(unittest.TestCase):
    def test_open_wal_is_not_merged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            flights = root / "flights"
            raw = root / "raw"
            flights.mkdir()
            raw.mkdir()
            old = flights / "20240101T000000Z.db"
            live = flights / "20240601T180000Z.db"
            _db(old)
            _db(live)
            _touch_old(old)
            wal = Path(str(live) + "-wal")
            wal.write_bytes(b"x" * 64)
            with mock.patch("analysis.jpi.auto.align_flight", return_value=_ok()) as fit:
                ledger = run_auto(
                    flights_dir=flights, jpi_dir=raw, ledger_path=root / "ingest.json", apply=False
                )
            self.assertEqual(fit.call_count, 0)
            by_db = {row["db"]: row for row in ledger["attempts"]}
            self.assertEqual(by_db[live.name]["status"], "skipped")
            self.assertIn("recorder", by_db[live.name]["reason"])
            self.assertEqual(by_db[old.name]["status"], "no_overlap")

    def test_quiet_newest_file_is_merged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            flights = root / "flights"
            raw = root / "raw"
            flights.mkdir()
            raw.mkdir()
            db = flights / "20240601T180000Z.db"
            _db(db)
            _touch_old(db)
            _csv(raw / "U240601.JPI", "one")
            with mock.patch("analysis.jpi.auto.align_flight", return_value=_ok()) as fit:
                ledger = run_auto(
                    flights_dir=flights, jpi_dir=raw, ledger_path=root / "ingest.json", apply=False
                )
            self.assertEqual(fit.call_count, 1)
            self.assertEqual(ledger["attempts"][0]["status"], "dry_run")

    def test_limit_and_since(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            flights = root / "flights"
            raw = root / "raw"
            flights.mkdir()
            raw.mkdir()
            names = ["20240101T000000Z.db", "20240601T000000Z.db", "20240901T000000Z.db"]
            for name in names:
                path = flights / name
                _db(path)
                _touch_old(path)
            (raw / "empty.jpi").write_bytes(b"")
            first = run_auto(
                flights_dir=flights,
                jpi_dir=raw,
                ledger_path=root / "a.json",
                apply=False,
                limit=1,
            )
            self.assertEqual({row["db"] for row in first["attempts"]}, {"20240101T000000Z.db"})
            later = run_auto(
                flights_dir=flights,
                jpi_dir=raw,
                ledger_path=root / "b.json",
                apply=False,
                since=datetime(2024, 8, 1, tzinfo=timezone.utc).timestamp(),
            )
            self.assertEqual({row["db"] for row in later["attempts"]}, {"20240901T000000Z.db"})


def _csv_overlap(path: Path) -> None:
    lines = ["FLIGHT,DATE,TIME,RPM,MAP,FF,LAT,LONG"]
    start = datetime(2024, 6, 1, 18, 0, 0)
    for fid in (1, 2):
        for i in range(25):
            t = start + timedelta(seconds=6 * i)
            lines.append(
                f"{fid},{t:%m/%d/%Y},{t:%H:%M:%S},2400,29.0,15.0,"
                f"{33.4 + i * 0.01:.4f},{-112.0 - i * 0.01:.4f}"
            )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")



class IncrementalTests(unittest.TestCase):
    def test_flag_sessions_cache_does_not_reopen(self):
        from analysis.jpi.duplicates import flag_sessions
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            flights = root / "flights"
            cache = root / "cache"
            flights.mkdir()
            cache.mkdir()
            _db(flights / "20260712T210041Z.db", gs=51.25, rows=80)
            _db(flights / "20260718T165100Z.db", gs=51.25, rows=80)
            # Make DBs older than the summary so the first incremental
            # pass bootstraps without opening them.
            past = time.time() - 120
            for path in flights.glob("*.db"):
                os.utime(path, (past, past))
            prev = [{"kind": "suspected_duplicate", "sessions": ["20260712T210041Z.db", "20260718T165100Z.db"]}]
            (cache / "summary.json").write_text(json.dumps({"flags": prev}), encoding="utf-8")
            with mock.patch("analysis.jpi.duplicates._fingerprint") as fp:
                flags = flag_sessions(flights, cache_dir=cache, previous_flags=prev)
            fp.assert_not_called()
            self.assertEqual(flags[0]["kind"], "suspected_duplicate")

    def test_noop_does_not_parse_hash_or_open(self):
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
            with mock.patch("analysis.jpi.auto.align_flight", return_value=_ok()):
                run_auto(flights_dir=flights, jpi_dir=raw, ledger_path=ledger, apply=True)
            with (
                mock.patch("analysis.jpi.auto.parse_file") as parsed,
                mock.patch("analysis.jpi.auto._sha256") as hashed,
                mock.patch("analysis.jpi.auto._connect") as connected,
                mock.patch("analysis.jpi.auto.align_flight", return_value=_ok()) as fit,
            ):
                run_auto(flights_dir=flights, jpi_dir=raw, ledger_path=ledger, apply=True)
            parsed.assert_not_called()
            hashed.assert_not_called()
            connected.assert_not_called()
            fit.assert_not_called()
            data = json.loads(ledger.read_text())
            skipped = [a for a in data["attempts"] if a["status"] == "skipped"]
            self.assertTrue(skipped)
            self.assertGreaterEqual(skipped[0]["count"], 1)

    def test_changed_jpi_opens_only_overlapping_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            flights = root / "flights"
            raw = root / "raw"
            flights.mkdir()
            raw.mkdir()
            june = flights / "20240601T180000Z.db"
            sept = flights / "20240901T180000Z.db"
            _db(june)
            _db(sept, start=datetime(2024, 9, 1, 17, 55, tzinfo=timezone.utc))
            _csv(raw / "U240601.JPI", "one")
            ledger = root / "ingest.json"
            with mock.patch("analysis.jpi.auto.align_flight", return_value=_ok()):
                run_auto(flights_dir=flights, jpi_dir=raw, ledger_path=ledger, apply=True)
            _csv(raw / "U240601.JPI", "two")
            opened = []
            real_connect = __import__("analysis.jpi.auto", fromlist=["_connect"])._connect

            def wrapped(db, *, write):
                opened.append(db.name)
                return real_connect(db, write=write)

            with (
                mock.patch("analysis.jpi.auto.align_flight", return_value=_ok()),
                mock.patch("analysis.jpi.auto._connect", side_effect=wrapped),
            ):
                run_auto(flights_dir=flights, jpi_dir=raw, ledger_path=ledger, apply=True)
            self.assertIn(june.name, opened)
            self.assertNotIn(sept.name, opened)

    def test_new_db_is_opened_when_jpi_files_are_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            flights = root / "flights"
            raw = root / "raw"
            flights.mkdir()
            raw.mkdir()
            first = flights / "20240601T180000Z.db"
            _db(first)
            _csv(raw / "U240601.JPI", "one")
            ledger = root / "ingest.json"
            with mock.patch("analysis.jpi.auto.align_flight", return_value=_ok()):
                run_auto(flights_dir=flights, jpi_dir=raw, ledger_path=ledger, apply=True)
            second = flights / "20240601T190000Z.db"
            _db(second)
            opened = []
            real_connect = __import__("analysis.jpi.auto", fromlist=["_connect"])._connect

            def wrapped(db, *, write):
                opened.append(db.name)
                return real_connect(db, write=write)

            with (
                mock.patch("analysis.jpi.auto.align_flight", return_value=_ok()),
                mock.patch("analysis.jpi.auto._connect", side_effect=wrapped),
            ):
                run_auto(flights_dir=flights, jpi_dir=raw, ledger_path=ledger, apply=True)
            self.assertIn(second.name, opened)
            self.assertNotIn(first.name, opened)
            self.assertGreaterEqual(opened.count(second.name), 1)


if __name__ == "__main__":
    unittest.main()
