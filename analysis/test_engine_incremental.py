"""Incremental engine review only opens DBs that changed."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from analysis.engine.cache import REVIEWER_VERSION
from analysis.engine.limits import Limits
from analysis.engine.run import review_all
from analysis.engine.summary import summarize


def _empty_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE gps (ts_ns INTEGER)")
    conn.commit()
    conn.close()


def _review(session: str, overall: str = "pass", t0: float = 1_700_000_000.0) -> dict:
    return {
        "session": session,
        "source": "db",
        "overall": overall,
        "counts": {"pass": 1, "caution": 0, "fail": 0, "skip": 0},
        "phases": [{"name": "cruise"}],
        "checks": [{"id": "mag_check", "status": "pass", "observed": "ok"}],
        "events": [],
        "metrics": {
            "cht3_offset_cruise_f": 14.0,
            "cruise_ff_gph": 16.5,
            "vapor_events": 0,
            "boost_lo": 0,
            "boost_hi": 0,
            "boost_lo_ff_drop": 0,
        },
        "notes": [],
        "alt_source": "gps.alt_msl",
        "t0_unix": t0,
        "t1_unix": t0 + 3600,
        "n_samples": 20,
    }


class IncrementalEngineTests(unittest.TestCase):
    def test_second_run_reviews_nothing_and_keeps_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            flights = root / "flights"
            cache = root / "cache"
            flights.mkdir()
            a = flights / "a.db"
            b = flights / "b.db"
            _empty_db(a)
            _empty_db(b)
            calls = []

            def fake(path, limits, cache_dir=None, *, force=False):
                calls.append(path.name)
                return _review(path.name, t0=1_700_000_000.0 + len(calls))

            with mock.patch("analysis.engine.run.review_db", side_effect=fake):
                reviews1, stats1 = review_all(flights, Limits(), cache_dir=cache, force=True)
                self.assertEqual(stats1["n_reviewed"], 2)
                summary1 = summarize(reviews1)
                (cache / "summary.json").write_text(json.dumps(summary1), encoding="utf-8")
                calls.clear()
                reviews2, stats2 = review_all(flights, Limits(), cache_dir=cache, force=False)
            self.assertEqual(calls, [])
            self.assertEqual(stats2["n_reviewed"], 0)
            self.assertEqual(stats2["n_reused"], 2)
            self.assertEqual(summarize(reviews2)["flights"], summary1["flights"])

    def test_only_changed_db_is_reviewed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            flights = root / "flights"
            cache = root / "cache"
            flights.mkdir()
            a = flights / "a.db"
            b = flights / "b.db"
            _empty_db(a)
            _empty_db(b)
            calls = []

            def fake(path, limits, cache_dir=None, *, force=False):
                calls.append(path.name)
                return _review(path.name)

            with mock.patch("analysis.engine.run.review_db", side_effect=fake):
                reviews1, _ = review_all(flights, Limits(), cache_dir=cache, force=True)
                (cache / "summary.json").write_text(json.dumps(summarize(reviews1)), encoding="utf-8")
                calls.clear()
                # Change only b.db. Keep a.db stamp the same.
                conn = sqlite3.connect(b)
                conn.execute("INSERT INTO gps VALUES (1)")
                conn.commit()
                conn.close()
                _, stats = review_all(flights, Limits(), cache_dir=cache, force=False)
            self.assertEqual(calls, ["b.db"])
            self.assertEqual(stats["n_reviewed"], 1)
            self.assertEqual(stats["n_reused"], 1)

    def test_all_forces_a_rerun(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            flights = root / "flights"
            cache = root / "cache"
            flights.mkdir()
            _empty_db(flights / "a.db")
            calls = []

            def fake(path, limits, cache_dir=None, *, force=False):
                calls.append((path.name, force))
                return _review(path.name)

            with mock.patch("analysis.engine.run.review_db", side_effect=fake):
                review_all(flights, Limits(), cache_dir=cache, force=True)
                calls.clear()
                review_all(flights, Limits(), cache_dir=cache, force=True)
            self.assertEqual(calls, [("a.db", True)])

    def test_reviewer_version_change_reruns(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            flights = root / "flights"
            cache = root / "cache"
            flights.mkdir()
            _empty_db(flights / "a.db")
            calls = []

            def fake(path, limits, cache_dir=None, *, force=False):
                calls.append(path.name)
                return _review(path.name)

            with mock.patch("analysis.engine.run.review_db", side_effect=fake):
                reviews, _ = review_all(flights, Limits(), cache_dir=cache, force=True)
                (cache / "summary.json").write_text(json.dumps(summarize(reviews)), encoding="utf-8")
                calls.clear()
                with mock.patch("analysis.engine.run.REVIEWER_VERSION", REVIEWER_VERSION + "-test"):
                    _, stats = review_all(flights, Limits(), cache_dir=cache, force=False)
            self.assertEqual(calls, ["a.db"])
            self.assertEqual(stats["n_reviewed"], 1)


if __name__ == "__main__":
    unittest.main()
