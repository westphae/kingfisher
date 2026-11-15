"""The flight-list chip comes from metadata and the ledger, not a row scan."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from analysis.browse import list_sessions
from analysis.jpi.status import chip


class ChipTests(unittest.TestCase):
    def test_labels(self):
        one = chip(True, {}, [])
        self.assertEqual(one["label"], "JPI")
        self.assertEqual(one["state"], "ok")
        many = chip(True, {"jpi_edm930_captures": "2"}, [])
        self.assertEqual(many["label"], "JPI ×2")
        self.assertEqual(many["state"], "multi")
        none = chip(False, {}, [])
        self.assertEqual(none["label"], "no JPI")
        refused = chip(
            False,
            {},
            [{"jpi_file": "U.JPI", "jpi_flight_id": "4", "status": "refused", "reason": "median error"}],
        )
        self.assertEqual(refused["label"], "JPI refused")
        self.assertIn("median error", refused["title"])
        both = chip(
            True,
            {"jpi_edm930_captures": "2"},
            [{"jpi_file": "U.JPI", "jpi_flight_id": "9", "status": "overlap", "reason": "overlaps"}],
        )
        self.assertEqual(both["label"], "JPI ×2")
        self.assertIn("refused", both["title"])

    def test_list_uses_metadata_and_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            flights = root / "flights"
            flights.mkdir()
            _table(flights / "merged.db", captures="2")
            _table(flights / "legacy.db", captures=None)
            sqlite3.connect(flights / "none.db").close()
            sqlite3.connect(flights / "refused.db").close()
            (root / "ingest.json").write_text(
                json.dumps(
                    {
                        "attempts": [
                            {
                                "db": "refused.db",
                                "jpi_file": "U1.JPI",
                                "jpi_flight_id": "1",
                                "status": "refused",
                                "reason": "median error 900 m",
                            },
                            {
                                "db": "merged.db",
                                "jpi_file": "U2.JPI",
                                "jpi_flight_id": "9",
                                "status": "overlap",
                                "reason": "aligned time range overlaps",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            rows = {row["file"]: row for row in list_sessions(flights, root / "catalog.json")}
            self.assertEqual(rows["merged.db"]["jpi"]["label"], "JPI ×2")
            self.assertIn("refused", rows["merged.db"]["jpi"]["title"])
            self.assertEqual(rows["legacy.db"]["jpi"]["label"], "JPI")
            self.assertEqual(rows["none.db"]["jpi"]["label"], "no JPI")
            self.assertEqual(rows["refused.db"]["jpi"]["label"], "JPI refused")


def _table(path: Path, captures: str | None) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE jpi_edm930 (ts_ns INTEGER)")
    conn.execute("INSERT INTO jpi_edm930 VALUES (1)")
    if captures is not None:
        conn.execute("CREATE TABLE metadata (key TEXT, value TEXT)")
        conn.execute(
            "INSERT INTO metadata VALUES ('jpi_edm930_captures', ?)",
            (captures,),
        )
    conn.commit()
    conn.close()


if __name__ == "__main__":
    unittest.main()
