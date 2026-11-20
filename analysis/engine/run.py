"""Run a review from a flight DB or a raw EDM-930 download."""

from __future__ import annotations

import sqlite3
from datetime import timezone
from pathlib import Path

from analysis.engine.cache import (
    REVIEWER_VERSION,
    db_stamp,
    file_stamp,
    flight_cache_path,
    load_cached,
    load_review_state,
    store_cached,
    store_review_state,
)
from analysis.engine.limits import Limits, limits_fingerprint
from analysis.engine.review import review_trace
from analysis.engine.trace import from_connection, from_flight
from analysis.jpi.parse import Flight, ParseError, parse_file

_OVERLAP_S = 900.0


def review_db(path: Path, limits: Limits, cache_dir: Path | None = None, *, force: bool = False) -> dict:
    path = Path(path)
    stamp = db_stamp(path)
    cached = None
    cache_path = None
    if cache_dir is not None:
        cache_path = flight_cache_path(cache_dir, path.name)
        if not force:
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
    force: bool = False,
    previous_summary: dict | None = None,
) -> tuple[list[dict], dict]:
    """Merged flight DBs, plus raw JPI flights that were never merged.

    Incremental mode (the default) only opens a DB when its mtime/size,
    the limits fingerprint, or ``REVIEWER_VERSION`` changed. ``force``
    (``engine --all``) re-reviews everything. The second return value is
    a short stats dict for the log line.
    """
    flights_dir = Path(flights_dir)
    cache_dir = Path(cache_dir) if cache_dir is not None else None
    state = load_review_state(cache_dir) if cache_dir is not None else {}
    prev_dbs = dict(state.get("dbs") or {})
    prev_jpi = dict(state.get("jpi") or {})
    prev_summary = previous_summary or {}
    if not prev_summary and cache_dir is not None:
        prev_summary = _load_summary(cache_dir)
    prev_rows = {row.get("session"): row for row in prev_summary.get("flights") or [] if row.get("session")}
    summary_mtime = _summary_mtime(cache_dir)
    fp = limits_fingerprint(limits)
    state_ok = (
        not force
        and state.get("reviewer_version", "1") == REVIEWER_VERSION
        and state.get("limits_fingerprint") == fp
    )

    db_paths = sorted(flights_dir.glob("*.db")) if flights_dir.is_dir() else []
    jpi_paths = []
    if jpi_dir is not None and Path(jpi_dir).is_dir():
        for path in sorted(Path(jpi_dir).glob("U*")):
            if path.suffix.lower() in {".jpi", ".dat", ".csv", ".txt"}:
                jpi_paths.append(path)

    cur_jpi_stamps = {path.name: file_stamp(path) for path in jpi_paths}
    jpi_changed = force or (not state_ok) or (cur_jpi_stamps != prev_jpi)
    # Bootstrap: no state yet, but files are not newer than the last summary.
    if not force and not prev_jpi and summary_mtime is not None:
        jpi_changed = any(path.stat().st_mtime > summary_mtime + 2 for path in jpi_paths)

    reviews: list[dict] = []
    n_reviewed = 0
    n_reused = 0
    new_db_stamps: dict[str, str] = {}
    for path in db_paths:
        stamp = db_stamp(path)
        new_db_stamps[path.name] = stamp
        cached = None
        if cache_dir is not None and not force:
            cached = load_cached(flight_cache_path(cache_dir, path.name), stamp=stamp, limits=limits)
        unchanged = (not force) and (cached is not None or (
            state_ok and prev_dbs.get(path.name) == stamp
        ) or (
            not prev_dbs and summary_mtime is not None and path.stat().st_mtime <= summary_mtime + 2
        ))
        if unchanged and cached is not None:
            reviews.append(cached)
            n_reused += 1
            continue
        if unchanged and path.name in prev_rows:
            reviews.append(_review_from_summary_row(prev_rows[path.name]))
            n_reused += 1
            continue
        if unchanged:
            # No cached review and no summary row: nothing to emit (likely no JPI).
            n_reused += 1
            continue
        review = review_db(path, limits, cache_dir, force=force)
        n_reviewed += 1
        if review.get("n_samples", 0) or review.get("overall") != "skip" or _has_jpi(path):
            reviews.append(review)

    if jpi_dir is not None and Path(jpi_dir).is_dir():
        covered = [(r.get("t0_unix"), r.get("t1_unix")) for r in reviews if r.get("t0_unix")]
        if not force and not jpi_changed:
            # Reuse unmerged raw-JPI rows from the previous summary.
            for session, row in prev_rows.items():
                if "#" not in str(session):
                    continue
                if any(r.get("session") == session for r in reviews):
                    continue
                t0, t1 = row.get("t0_unix"), row.get("t1_unix") or row.get("t0_unix")
                if t0 and _overlaps_any(float(t0), float(t1 or t0), covered):
                    continue
                reviews.append(_review_from_summary_row(row))
                n_reused += 1
        else:
            for path in jpi_paths:
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
                    n_reviewed += 1

    if cache_dir is not None:
        store_review_state(
            cache_dir,
            {
                "reviewer_version": REVIEWER_VERSION,
                "limits_fingerprint": fp,
                "dbs": new_db_stamps,
                "jpi": cur_jpi_stamps,
            },
        )
    stats = {
        "n_dbs": len(db_paths),
        "n_reviewed": n_reviewed,
        "n_reused": n_reused,
        "force": force,
        "jpi_changed": bool(jpi_changed),
    }
    return reviews, stats


def _load_summary(cache_dir: Path) -> dict:
    path = cache_dir / "summary.json"
    if not path.is_file():
        return {}
    try:
        import json
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _summary_mtime(cache_dir: Path | None) -> float | None:
    if cache_dir is None:
        return None
    path = cache_dir / "summary.json"
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _review_from_summary_row(row: dict) -> dict:
    """Enough of a review that summarize() reproduces the same summary row."""
    return {
        "session": row.get("session"),
        "source": row.get("source"),
        "overall": row.get("overall"),
        "counts": row.get("counts") or {"pass": 0, "caution": 0, "fail": 0, "skip": 0},
        "phases": [{"name": "cruise"}] if row.get("overall") == "skip" else [{"name": "flight"}],
        "checks": [
            {
                "id": "mag_check",
                "status": row.get("mag_status"),
                "observed": row.get("mag_observed"),
            }
        ] if row.get("mag_status") is not None or row.get("mag_observed") is not None else [],
        "events": [],
        "metrics": {
            "cht3_offset_climb_f": row.get("cht3_offset_climb_f"),
            "cht3_offset_cruise_f": row.get("cht3_offset_cruise_f"),
            "cht3_wiggle_f": row.get("cht3_wiggle_f"),
            "peak_cht_f": row.get("peak_cht_f"),
            "time_above_380_s": row.get("time_above_380_s"),
            "cruise_ff_gph": row.get("cruise_ff_gph"),
            "cruise_rpm": row.get("cruise_rpm"),
            "cruise_tit_f": row.get("cruise_tit_f"),
            "lop_depth_f": row.get("lop_depth_f"),
            "peak_tit_f": row.get("peak_tit_f"),
            "mixture_side": row.get("mixture_side"),
            "hp_est": row.get("hp_est"),
            "vapor_events": row.get("vapor_events") or 0,
            "boost_lo": row.get("boost_lo") or 0,
            "boost_hi": row.get("boost_hi") or 0,
            "boost_lo_ff_drop": row.get("boost_lo_ff_drop") or 0,
            "oil_press_min": row.get("oil_press_min"),
            "oil_temp_max_f": row.get("oil_temp_max_f"),
        },
        "notes": [],
        "alt_source": row.get("alt_source"),
        "t0_unix": row.get("t0_unix"),
        "t1_unix": None,
        "n_samples": 1,
    }



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
