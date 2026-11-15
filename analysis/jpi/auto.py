"""Merge every overlapping JPI download into the flight DBs that need it.

Idempotent. A database that already has ``jpi_edm930`` from the same file
bytes is left alone. A database whose source file changed is fitted again
and replaced only when the existing refusal limits still pass. Anything
that cannot be merged is a row in the ledger, not a silent skip.

This writes the Pi's own flight SQLite files. It does not write a copy
that lives on the NAS.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from analysis.jpi.align import DEFAULT_WINDOW_S, _db_span, align_flight
from analysis.jpi.duplicates import flag_sessions
from analysis.jpi.parse import Download, Flight, ParseError, parse_file
from analysis.jpi.store import write_flight

_SUFFIXES = {".jpi", ".dat", ".csv", ".txt"}


def run_auto(
    *,
    flights_dir: Path,
    jpi_dir: Path,
    ledger_path: Path,
    apply: bool = True,
    window_s: float = DEFAULT_WINDOW_S,
) -> dict:
    flights_dir = Path(flights_dir)
    jpi_dir = Path(jpi_dir)
    ledger_path = Path(ledger_path)
    ledger = _load_ledger(ledger_path)
    now = _now()
    downloads = _load_downloads(jpi_dir)
    ledger["files"] = [
        {
            "name": item["name"],
            "sha256": item["sha256"],
            "flights": 0 if item["download"] is None else len(item["download"].flights),
            "error": item["error"],
        }
        for item in downloads
    ]
    dbs = sorted(flights_dir.glob("*.db")) if flights_dir.is_dir() else []
    print(f"jpi-auto: {len(downloads)} JPI file(s), {len(dbs)} flight DB(s)")
    for db in dbs:
        try:
            _one_db(db, downloads, ledger, apply=apply, window_s=window_s, now=now)
        except sqlite3.OperationalError as exc:
            _record(
                ledger,
                _attempt(
                    now,
                    db.name,
                    status="error",
                    reason=f"database locked or unreadable: {exc}",
                ),
            )
            print(f"  {db.name}: error {exc}")
        except OSError as exc:
            _record(ledger, _attempt(now, db.name, status="error", reason=str(exc)))
            print(f"  {db.name}: error {exc}")
    flags = flag_sessions(flights_dir) if flights_dir.is_dir() else []
    ledger["flags"] = flags
    ledger["updated"] = now
    ledger["jpi_dir"] = str(jpi_dir)
    ledger["flights_dir"] = str(flights_dir)
    ledger["version"] = 1
    _write_ledger(ledger_path, ledger)
    for flag in flags:
        print(f"flag: {flag['detail']}")
    print(f"ledger: {ledger_path}")
    return ledger


def _one_db(db: Path, downloads: list[dict], ledger: dict, *, apply: bool, window_s: float, now: str) -> None:
    conn = sqlite3.connect(db, timeout=5.0)
    try:
        conn.execute("PRAGMA busy_timeout=5000")
        span = _db_span(conn)
        existing = _existing(conn)
        if span is None:
            _record(ledger, _attempt(now, db.name, status="skipped", reason="no gps or press_alt span"))
            print(f"  {db.name}: no time span")
            return
        changed = None
        if existing:
            named = _file_by_name(downloads, existing["source"]) if existing["source"] else None
            if existing["sha256"] and named is not None and named["sha256"] == existing["sha256"]:
                _record(
                    ledger,
                    _attempt(
                        now,
                        db.name,
                        jpi_file=named["name"],
                        jpi_sha256=existing["sha256"],
                        jpi_flight_id=existing["flight_id"],
                        status="skipped",
                        reason="already merged from this file",
                    ),
                )
                print(f"  {db.name}: already merged from {named['name']}")
                return
            if not existing["sha256"] and named is not None and named["download"] is not None:
                _set_meta(conn, "jpi_edm930_sha256", named["sha256"])
                conn.commit()
                _record(
                    ledger,
                    _attempt(
                        now,
                        db.name,
                        jpi_file=named["name"],
                        jpi_sha256=named["sha256"],
                        jpi_flight_id=existing["flight_id"],
                        status="backfilled",
                        reason="stored the current file hash; rows kept",
                    ),
                )
                print(f"  {db.name}: recorded sha256 of {named['name']}")
                return
            if named is None:
                _record(
                    ledger,
                    _attempt(
                        now,
                        db.name,
                        jpi_file=existing["source"],
                        jpi_sha256=existing["sha256"],
                        jpi_flight_id=existing["flight_id"],
                        status="kept",
                        reason="already merged; source file is not in the raw directory",
                    ),
                )
                print(f"  {db.name}: kept existing merge ({existing['source'] or 'unknown source'})")
                return
            # Named file is on disk and its bytes differ from the stored hash.
            changed = named
        candidates = _candidates(downloads, span, window_s, only=changed)
        if not candidates:
            status = "kept" if existing else "no_overlap"
            reason = (
                "source file changed but it does not overlap this DB"
                if existing
                else "no JPI flight overlaps this DB"
            )
            _record(ledger, _attempt(now, db.name, status=status, reason=reason))
            print(f"  {db.name}: {reason}")
            return
        fitted = []
        for item, flight in candidates:
            if item["download"] is None:
                continue
            result = align_flight(flight, conn, window_s=window_s)
            fitted.append((item, flight, result))
            print(
                f"  {db.name} × {item['name']} flight {flight.flight_id}: "
                + ("ok" if result.ok else "REFUSED")
                + f" {result.residual if result.ok else result.reason}"
            )
        ok = [row for row in fitted if row[2].ok]
        ok.sort(key=lambda row: row[2].residual)
        winner = ok[0] if ok else None
        for item, flight, result in fitted:
            chosen = winner is not None and flight is winner[1] and item is winner[0]
            if result.ok and chosen:
                status = "applied" if apply else "dry_run"
                reason = "" if apply else "dry-run; limits passed, nothing written"
                if apply:
                    if existing and item["sha256"] == (existing["sha256"] or ""):
                        status = "skipped"
                        reason = "already merged from this file"
                    else:
                        write_flight(conn, flight, result, item["name"], item["sha256"])
                        reason = "merged"
                        if existing:
                            reason = "replaced the previous merge; source file changed or there was no hash"
            elif result.ok:
                status = "not_applied"
                reason = "another overlapping flight matched more closely"
            else:
                status = "refused"
                reason = result.reason
                if existing and winner is None:
                    reason = f"{result.reason} Previous merge kept."
            _record(
                ledger,
                _attempt(
                    now,
                    db.name,
                    jpi_file=item["name"],
                    jpi_sha256=item["sha256"],
                    jpi_flight_id=flight.flight_id,
                    offset_s=result.offset_s if result.ok else None,
                    drift_s_per_s=result.drift_s_per_s if result.ok else None,
                    residual=result.residual if result.ok else None,
                    residual_unit=result.residual_unit if result.ok else "",
                    method=result.method,
                    status=status,
                    reason=reason,
                ),
            )
        if winner is None:
            print(f"  {db.name}: not written")
        elif apply:
            print(f"  {db.name}: merged flight {winner[1].flight_id} from {winner[0]['name']}")
    finally:
        conn.close()


def _candidates(downloads, span, window_s, only) -> list[tuple[dict, Flight]]:
    db0, db1 = span
    pool = [only] if only is not None else downloads
    found = []
    for item in pool:
        download = item.get("download")
        if download is None:
            continue
        for flight in download.flights:
            if not flight.samples or flight.start is None or flight.end is None:
                continue
            t0 = flight.start.replace(tzinfo=timezone.utc).timestamp()
            t1 = flight.end.replace(tzinfo=timezone.utc).timestamp()
            if min(t1 + window_s, db1) - max(t0 - window_s, db0) > 0:
                found.append((item, flight))
    return found


def _existing(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='jpi_edm930'"
    ).fetchone()
    if not row:
        return None
    n = conn.execute("SELECT COUNT(*) FROM jpi_edm930").fetchone()[0]
    if not n:
        return None
    meta = {}
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='metadata'").fetchone():
        for key, value in conn.execute(
            "SELECT key, value FROM metadata WHERE key LIKE 'jpi_edm930_%'"
        ):
            meta[key] = value or ""
    return {
        "rows": int(n),
        "sha256": meta.get("jpi_edm930_sha256") or "",
        "source": meta.get("jpi_edm930_source") or "",
        "flight_id": meta.get("jpi_edm930_flight_id") or "",
    }


def _set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS metadata (
          key TEXT PRIMARY KEY,
          value TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO metadata(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def _load_downloads(jpi_dir: Path) -> list[dict]:
    if not jpi_dir.is_dir():
        return []
    out = []
    for path in sorted(jpi_dir.iterdir()):
        if not path.is_file() or path.suffix.lower() not in _SUFFIXES:
            continue
        # Whole-download names from the USB stick, plus any CSV dropped beside them.
        if path.suffix.lower() in {".csv", ".txt"} and not path.name.upper().startswith("U"):
            continue
        sha = _sha256(path)
        try:
            download: Download | None = parse_file(path)
            error = ""
        except ParseError as exc:
            download = None
            error = str(exc)
        except OSError as exc:
            download = None
            error = str(exc)
        out.append({"path": path, "name": path.name, "sha256": sha, "download": download, "error": error})
    return out


def _file_by_name(downloads: list[dict], name: str) -> dict | None:
    if not name:
        return None
    for item in downloads:
        if item["name"] == name:
            return item
    return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _attempt(
    now: str,
    db: str,
    *,
    jpi_file: str = "",
    jpi_sha256: str = "",
    jpi_flight_id: str | int | None = None,
    offset_s: float | None = None,
    drift_s_per_s: float | None = None,
    residual: float | None = None,
    residual_unit: str = "",
    method: str = "",
    status: str,
    reason: str,
) -> dict:
    drift_h = None if drift_s_per_s is None else drift_s_per_s * 3600.0
    return {
        "ts": now,
        "last_seen": now,
        "count": 1,
        "db": db,
        "jpi_file": jpi_file,
        "jpi_sha256": jpi_sha256,
        "jpi_flight_id": "" if jpi_flight_id is None else str(jpi_flight_id),
        "offset_s": _num(offset_s),
        "drift_s_per_s": _num(drift_s_per_s),
        "drift_s_per_h": _num(drift_h),
        "residual": _num(residual),
        "residual_unit": residual_unit,
        "method": method,
        "status": status,
        "reason": reason,
    }


def _num(value: float | None):
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return round(number, 6)


def _record(ledger: dict, attempt: dict) -> None:
    attempts = ledger.setdefault("attempts", [])
    for prev in reversed(attempts):
        if (
            prev.get("db") == attempt["db"]
            and prev.get("jpi_sha256") == attempt["jpi_sha256"]
            and prev.get("jpi_flight_id") == attempt["jpi_flight_id"]
            and prev.get("status") == attempt["status"]
            and prev.get("reason") == attempt["reason"]
        ):
            prev["last_seen"] = attempt["ts"]
            prev["count"] = int(prev.get("count") or 1) + 1
            return
    attempts.append(attempt)


def _load_ledger(path: Path) -> dict:
    if not path.is_file():
        return {"version": 1, "attempts": [], "flags": [], "files": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ledger unreadable, starting a new one: {exc}", file=sys.stderr)
        return {"version": 1, "attempts": [], "flags": [], "files": []}
    if not isinstance(data, dict):
        return {"version": 1, "attempts": [], "flags": [], "files": []}
    data.setdefault("attempts", [])
    data.setdefault("flags", [])
    data.setdefault("files", [])
    return data


def _write_ledger(path: Path, ledger: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(ledger, indent=2, allow_nan=False), encoding="utf-8")
    tmp.replace(path)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def add_parser(sub) -> None:
    p = sub.add_parser(
        "jpi-auto",
        help="merge overlapping JPI downloads into flight DBs that have no current merge",
    )
    p.add_argument(
        "--jpi-dir",
        type=Path,
        default=Path.home() / "kingfisher" / "engine" / "raw",
        help="local copies of U*.JPI (default ~/kingfisher/engine/raw)",
    )
    p.add_argument(
        "--ledger",
        type=Path,
        default=Path.home() / "kingfisher" / "engine" / "ingest.json",
        help="attempt ledger (default ~/kingfisher/engine/ingest.json)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="fit and record, but do not write jpi_edm930",
    )
    p.add_argument(
        "--window-s",
        type=float,
        default=DEFAULT_WINDOW_S,
        help="half-width of the clock search, seconds (default ±900)",
    )


def cmd_jpi_auto(args) -> int:
    flights = Path(args.flights_dir)
    jpi_dir = Path(args.jpi_dir)
    if not flights.is_dir():
        print(f"flights dir not found: {flights}", file=sys.stderr)
        return 2
    if not jpi_dir.is_dir():
        print(f"JPI dir not found: {jpi_dir}", file=sys.stderr)
        return 2
    run_auto(
        flights_dir=flights,
        jpi_dir=jpi_dir,
        ledger_path=Path(args.ledger),
        apply=not args.dry_run,
        window_s=args.window_s,
    )
    return 0
