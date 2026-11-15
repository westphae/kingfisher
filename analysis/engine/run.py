"""Run a review from a flight DB or a raw EDM-930 download."""

from __future__ import annotations

import sqlite3
from datetime import timezone
from pathlib import Path

from analysis.engine.cache import db_stamp, flight_cache_path, load_cached, store_cached
from analysis.engine.limits import Limits
from analysis.engine.review import review_trace
from analysis.engine.trace import from_connection, from_flight
from analysis.jpi.parse import Flight, ParseError, parse_file

_OVERLAP_S = 900.0


def review_db(path: Path, limits: Limits, cache_dir: Path | None = None) -> dict:
    path = Path(path)
    stamp = db_stamp(path)
    cached = None
    cache_path = None
    if cache_dir is not None:
        cache_path = flight_cache_path(cache_dir, path.name)
        cached = load_cached(cache_path, stamp=stamp, limits=limits)
    if cached is not None:
        return cached
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tr = from_connection(conn, path.name)
    finally:
        conn.close()
    if tr is None:
        review = {
            "session": path.name,
            "source": "db",
            "overall": "skip",
            "counts": {"pass": 0, "caution": 0, "fail": 0, "skip": 1},
            "phases": [],
            "checks": [
                {
                    "id": "data",
                    "phase": "flight",
                    "title": "Engine data",
                    "status": "skip",
                    "observed": "no jpi_edm930 rows",
                    "limit": "merge a JPI flight first",
                    "detail": "",
                }
            ],
            "events": [],
            "metrics": {},
            "notes": ["This DB has no jpi_edm930 table."],
            "alt_source": None,
            "ias": "missing",
            "n_samples": 0,
            "t0_unix": None,
            "t1_unix": None,
        }
    else:
        review = review_trace(tr, limits)
    if cache_path is not None and review.get("overall") != "skip":
        store_cached(cache_path, review, stamp=stamp, limits=limits)
    elif cache_path is not None and review.get("n_samples", 0) >= 5:
        store_cached(cache_path, review, stamp=stamp, limits=limits)
    return review


def review_jpi_flight(flight: Flight, session: str, limits: Limits) -> dict:
    tr = from_flight(flight, session)
    if tr is None:
        return review_trace_empty(session)
    return review_trace(tr, limits)


def review_trace_empty(session: str) -> dict:
    return {
        "session": session,
        "source": "jpi",
        "overall": "skip",
        "counts": {"pass": 0, "caution": 0, "fail": 0, "skip": 1},
        "phases": [],
        "checks": [],
        "events": [],
        "metrics": {},
        "notes": ["empty flight"],
        "t0_unix": None,
        "t1_unix": None,
    }


def review_all(
    flights_dir: Path,
    limits: Limits,
    *,
    cache_dir: Path | None = None,
    jpi_dir: Path | None = None,
) -> list[dict]:
    """Every merged flight DB, plus raw JPI flights that were never merged."""
    reviews: list[dict] = []
    flights_dir = Path(flights_dir)
    if flights_dir.is_dir():
        for path in sorted(flights_dir.glob("*.db")):
            review = review_db(path, limits, cache_dir)
            if review.get("n_samples", 0) or _has_jpi(path):
                reviews.append(review)
    if jpi_dir is not None and Path(jpi_dir).is_dir():
        covered = [(r.get("t0_unix"), r.get("t1_unix")) for r in reviews if r.get("t0_unix")]
        for path in sorted(Path(jpi_dir).glob("U*")):
            if path.suffix.lower() not in {".jpi", ".dat", ".csv", ".txt"}:
                continue
            try:
                download = parse_file(path)
            except (ParseError, OSError):
                continue
            for flight in download.flights:
                if not flight.samples:
                    continue
                t0 = flight.start.replace(tzinfo=timezone.utc).timestamp()
                t1 = flight.end.replace(tzinfo=timezone.utc).timestamp()
                if _overlaps_any(t0, t1, covered):
                    continue
                session = f"{path.name}#flight{flight.flight_id}"
                review = review_jpi_flight(flight, session, limits)
                reviews.append(review)
                covered.append((review.get("t0_unix"), review.get("t1_unix")))
    return reviews


def _has_jpi(path: Path) -> bool:
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return False
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='jpi_edm930'"
        ).fetchone()
        if not row:
            return False
        n = conn.execute("SELECT COUNT(*) FROM jpi_edm930").fetchone()[0]
        return bool(n)
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def _overlaps_any(t0: float, t1: float, covered: list[tuple]) -> bool:
    for a, b in covered:
        if a is None or b is None:
            continue
        if min(t1, b) - max(t0, a) > -_OVERLAP_S:
            return True
    return False
