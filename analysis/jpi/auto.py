"""Merge every overlapping JPI download into the flight DBs that need it.

Idempotent. A database that already has ``jpi_edm930`` from the same file
bytes is left alone. A database whose source file changed is fitted again
and replaced only when the existing refusal limits still pass. Anything
that cannot be merged is a row in the ledger, not a silent skip.

This writes the Pi's own flight SQLite files. It does not write a copy
that lives on the NAS.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from analysis.jpi.align import DEFAULT_WINDOW_S, AlignResult, _db_span, _jpi_track, align_flight, flight_unix
from analysis.jpi.duplicates import fingerprint_conn, flags_from_fingerprints
from analysis.jpi.parse import Download, Flight, ParseError, parse_file
from analysis.jpi.store import Capture, read_kept_rows, write_captures

_SUFFIXES = {".jpi", ".dat", ".csv", ".txt"}
# A session the recorder still has open. The WAL sidecar is rewritten on
# each flush; two minutes is longer than a flush and shorter than a flight.
_LIVE_S = 120.0
_STAMP = re.compile(r"^(\d{8}T\d{6}Z)")


def run_auto(
    *,
    flights_dir: Path,
    jpi_dir: Path,
    ledger_path: Path,
    apply: bool = True,
    window_s: float = DEFAULT_WINDOW_S,
    since: float | None = None,
    limit: int | None = None,
) -> dict:
    flights_dir = Path(flights_dir)
    jpi_dir = Path(jpi_dir)
    ledger_path = Path(ledger_path)
    ledger = _load_ledger(ledger_path)
    now = _now()
    # Flight spans only. The sample arrays are dropped before the DB loop
    # and parsed again for the one download a DB actually overlaps.
    files, spans = _catalog_downloads(jpi_dir)
    ledger["files"] = [
        {"name": item["name"], "sha256": item["sha256"], "flights": item["flights"], "error": item["error"]}
        for item in files
    ]
    dbs = sorted(p for p in flights_dir.glob("*.db") if p.is_file()) if flights_dir.is_dir() else []
    live = _live_names(dbs)
    selected = []
    skipped_live = []
    n_since = 0
    for db in dbs:
        if db.name in live:
            skipped_live.append(db)
            continue
        if since is not None and _session_unix(db) < since:
            n_since += 1
            continue
        selected.append(db)
    if limit is not None:
        selected = selected[: max(0, limit)]
    extra = ""
    if skipped_live:
        extra += f", {len(skipped_live)} live skipped"
    if n_since:
        extra += f", {n_since} before --since"
    if limit is not None:
        extra += f", limit {limit}"
    print(f"jpi-auto: {len(files)} JPI file(s), {len(selected)} flight DB(s){extra}")
    for db in skipped_live:
        _record(
            ledger,
            _attempt(now, db.name, status="skipped", reason="recorder has this session open"),
        )
        print(f"  {db.name}: skipped, recorder has this session open")
    fingerprints = []
    for db in selected:
        try:
            fp = _one_db(db, files, spans, ledger, apply=apply, window_s=window_s, now=now)
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
            fp = None
        except OSError as exc:
            _record(ledger, _attempt(now, db.name, status="error", reason=str(exc)))
            print(f"  {db.name}: error {exc}")
            fp = None
        if fp is not None:
            fingerprints.append(fp)
        _checkpoint(ledger_path, ledger, now, jpi_dir, flights_dir)
        _release_memory()
    flags = flags_from_fingerprints(fingerprints)
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


def _one_db(
    db: Path,
    files: list[dict],
    spans: list[dict],
    ledger: dict,
    *,
    apply: bool,
    window_s: float,
    now: str,
) -> dict | None:
    fp_holder: list = [None]
    box = [_connect(db, write=False)]
    try:
        _one_db_work(
            box, fp_holder, db, files, spans, ledger, apply=apply, window_s=window_s, now=now
        )
    finally:
        _release_memory()
    return fp_holder[0]


def _one_db_work(box, fp_holder, db, files, spans, ledger, *, apply, window_s, now) -> None:
    conn = box[0]
    try:
        span = _db_span(conn)
        if span is None:
            _record(ledger, _attempt(now, db.name, status="skipped", reason="no gps or press_alt span"))
            print(f"  {db.name}: no time span")
            return
        # Parse only the downloads whose flight clock overlaps this session.
        downloads = _downloads_for_span(files, spans, span, window_s)
        stored = _stored_captures(conn)
        candidates = _candidates(downloads, span, window_s, only=None)
        if not candidates and not stored:
            _record(ledger, _attempt(now, db.name, status="no_overlap", reason="no JPI flight overlaps this DB"))
            print(f"  {db.name}: no JPI flight overlaps this DB")
            return
        by_file: dict[str, list[tuple[dict, Flight]]] = {}
        for item, flight in candidates:
            by_file.setdefault(item["name"], []).append((item, flight))
        fitted: list[tuple[dict, Flight, AlignResult, bool]] = []
        for group in by_file.values():
            fitted.extend(_fit_group(group, conn, stored, window_s, db.name))
        kept, overlapped = _resolve_overlaps(fitted)
        stored_caps = [cap for cap in stored.values() if isinstance(cap, dict)]
        missing = [
            cap
            for cap in stored_caps
            if cap.get("source") and _file_by_name(downloads, cap["source"]) is None
        ]
        kept_ids = {f"{item['name']}#{flight.flight_id}" for item, flight, _result, _shared in kept}
        stored_ids = {str(cap["id"]) for cap in stored_caps if cap.get("id")}
        unchanged = bool(stored_ids) and kept_ids == stored_ids and not missing and all(
            _stored_sha_ok(stored, item, flight) for item, flight, _r, _s in kept
        )
        if unchanged:
            for item, flight, result, _shared in kept:
                _record(
                    ledger,
                    _attempt(
                        now,
                        db.name,
                        jpi_file=item["name"],
                        jpi_sha256=item["sha256"],
                        jpi_flight_id=flight.flight_id,
                        offset_s=result.offset_s,
                        drift_s_per_s=result.drift_s_per_s,
                        residual=result.residual,
                        residual_unit=result.residual_unit,
                        method=result.method,
                        status="skipped",
                        reason="already merged from this file",
                    ),
                )
            print(f"  {db.name}: already merged ({len(kept_ids)} capture(s))")
            return
        refused = [row for row in fitted if not row[2].ok]
        for item, flight, result, _shared in refused:
            reason = result.reason
            if stored and not kept:
                reason = f"{result.reason} Previous merge kept."
            _record(
                ledger,
                _attempt(
                    now,
                    db.name,
                    jpi_file=item["name"],
                    jpi_sha256=item["sha256"],
                    jpi_flight_id=flight.flight_id,
                    method=result.method,
                    status="refused",
                    reason=reason,
                ),
            )
            print(f"  {db.name} × {item['name']} flight {flight.flight_id}: REFUSED {result.reason}")
        for item, flight, result, _shared in overlapped:
            _record(
                ledger,
                _attempt(
                    now,
                    db.name,
                    jpi_file=item["name"],
                    jpi_sha256=item["sha256"],
                    jpi_flight_id=flight.flight_id,
                    offset_s=result.offset_s,
                    drift_s_per_s=result.drift_s_per_s,
                    residual=result.residual,
                    residual_unit=result.residual_unit,
                    method=result.method,
                    status="overlap",
                    reason="aligned time range overlaps another capture; not written",
                ),
            )
            print(f"  {db.name} × {item['name']} flight {flight.flight_id}: overlap, not written")
        if not kept and not missing:
            print(f"  {db.name}: not written")
            return
        if not apply:
            for item, flight, result, shared in kept:
                _record(
                    ledger,
                    _attempt(
                        now,
                        db.name,
                        jpi_file=item["name"],
                        jpi_sha256=item["sha256"],
                        jpi_flight_id=flight.flight_id,
                        offset_s=result.offset_s,
                        drift_s_per_s=result.drift_s_per_s,
                        residual=result.residual,
                        residual_unit=result.residual_unit,
                        method=result.method,
                        status="dry_run",
                        reason="dry-run; limits passed, nothing written"
                        + (" (shared clock)" if shared else ""),
                    ),
                )
            print(f"  {db.name}: dry-run, {len(kept)} capture(s)")
            return
        # Read-only until there is something to write, so a dry-run does not
        # open hundreds of flight DBs in WAL mode.
        conn.close()
        conn = _connect(db, write=True)
        box[0] = conn
        keep_rows = []
        if missing:
            keep_rows = read_kept_rows(conn, {cap["id"] for cap in missing if cap.get("id")})
            only = next((cap["id"] for cap in missing if cap.get("id")), "kept")
            for row in keep_rows:
                if not row.get("capture_id"):
                    row["capture_id"] = only
        captures = [
            Capture(flight, result, item["name"], item["sha256"], shared=shared)
            for item, flight, result, shared in kept
        ]
        try:
            n = write_captures(conn, captures, keep_rows)
        except ValueError as exc:
            _record(ledger, _attempt(now, db.name, status="error", reason=str(exc)))
            print(f"  {db.name}: not written ({exc})")
            return
        for item, flight, result, shared in kept:
            _record(
                ledger,
                _attempt(
                    now,
                    db.name,
                    jpi_file=item["name"],
                    jpi_sha256=item["sha256"],
                    jpi_flight_id=flight.flight_id,
                    offset_s=result.offset_s,
                    drift_s_per_s=result.drift_s_per_s,
                    residual=result.residual,
                    residual_unit=result.residual_unit,
                    method=result.method,
                    status="applied",
                    reason="merged" + (" (shared clock from another flight in this download)" if shared else ""),
                ),
            )
        print(f"  {db.name}: merged {len(captures)} capture(s), {n} rows")
    finally:
        conn = box[0]
        try:
            fp_holder[0] = fingerprint_conn(conn, db.name)
        except sqlite3.Error:
            fp_holder[0] = None
        conn.close()


def _fit_group(group, conn, stored, window_s, db_name) -> list[tuple[dict, Flight, AlignResult, bool]]:
    out: list[tuple[dict, Flight, AlignResult, bool]] = []
    donors: list[AlignResult] = []
    for item, flight in group:
        cid = f"{item['name']}#{flight.flight_id}"
        prev = stored.get(cid) if isinstance(stored, dict) else None
        if prev and prev.get("sha256") and prev.get("sha256") == item["sha256"] and prev.get("offset_s") is not None:
            result = _result_from_stored(prev)
            shared = bool(prev.get("shared"))
            out.append((item, flight, result, shared))
            if result.ok and not shared:
                donors.append(result)
            continue
        result = align_flight(flight, conn, window_s=window_s)
        print(
            f"  {db_name} × {item['name']} flight {flight.flight_id}: "
            + ("ok" if result.ok else "REFUSED")
            + f" {result.residual if result.ok else result.reason}"
        )
        out.append((item, flight, result, False))
        if result.ok:
            donors.append(result)
    if not donors:
        return out
    donor = min(donors, key=lambda r: r.residual if r.residual == r.residual else 1e18)
    shared_out = []
    for item, flight, result, shared in out:
        if result.ok or shared:
            shared_out.append((item, flight, result, shared))
            continue
        if _has_track(flight):
            shared_out.append((item, flight, result, False))
            continue
        shared_out.append((item, flight, _shared_result(donor), True))
        print(
            f"  {db_name} × {item['name']} flight {flight.flight_id}: "
            f"shared clock from this download ({donor.method})"
        )
    return shared_out


def _has_track(flight: Flight) -> bool:
    try:
        return _jpi_track(flight, flight_unix(flight)) is not None
    except (TypeError, ValueError):
        return False


def _shared_result(donor: AlignResult) -> AlignResult:
    return AlignResult(
        ok=True,
        method=donor.method + "+shared",
        offset_s=donor.offset_s,
        drift_s_per_s=donor.drift_s_per_s,
        t_ref_unix=donor.t_ref_unix,
        residual=donor.residual,
        residual_unit=donor.residual_unit,
        detail=f"Shared clock from another flight in the same download ({donor.method}).",
        n_points=donor.n_points,
    )


def _resolve_overlaps(fitted):
    from analysis.jpi.store import aligned_times

    ok = [row for row in fitted if row[2].ok]
    spans = []
    for item, flight, result, shared in ok:
        stamps = aligned_times(flight, result)
        if not stamps:
            continue
        spans.append((min(stamps), max(stamps), (item, flight, result, shared)))
    spans.sort(key=lambda row: row[0])
    kept_spans = []
    overlapped = []
    for start, end, row in spans:
        clash = None
        for ks, ke, krow in kept_spans:
            if start <= ke and end >= ks:
                clash = (ks, ke, krow)
                break
        if clash is None:
            kept_spans.append((start, end, row))
            continue
        if _rank(row) < _rank(clash[2]):
            overlapped.append(clash[2])
            kept_spans = [s for s in kept_spans if s[2] is not clash[2]]
            kept_spans.append((start, end, row))
        else:
            overlapped.append(row)
    return [s[2] for s in kept_spans], overlapped


def _rank(row) -> tuple:
    _item, flight, result, shared = row
    residual = result.residual if result.residual == result.residual else 1e18
    return (1 if shared else 0, residual, -len(flight.samples), int(flight.flight_id))


def _stored_sha_ok(stored: dict, item: dict, flight: Flight) -> bool:
    cap = stored.get(f"{item['name']}#{flight.flight_id}")
    return bool(cap) and cap.get("sha256") == item["sha256"]


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


def _stored_captures(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='jpi_edm930'"
    ).fetchone()
    if not row:
        return {}
    if conn.execute("SELECT 1 FROM jpi_edm930 LIMIT 1").fetchone() is None:
        return {}
    meta = {}
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='metadata'").fetchone():
        for key, value in conn.execute(
            "SELECT key, value FROM metadata WHERE key LIKE 'jpi_edm930_%'"
        ):
            meta[key] = value or ""
    raw = meta.get("jpi_edm930_captures_json") or ""
    if raw:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, list):
            return {str(item["id"]): item for item in data if isinstance(item, dict) and item.get("id")}
    source = meta.get("jpi_edm930_source") or ""
    fid = meta.get("jpi_edm930_flight_id") or ""
    if not source and not fid:
        return {"legacy": {"id": "legacy", "source": "", "sha256": "", "kept": True}}
    cid = f"{source}#{fid}" if source else str(fid)

    def fnum(key: str):
        try:
            return float(meta.get(key) or "nan")
        except ValueError:
            return float("nan")

    return {
        cid: {
            "id": cid,
            "source": source,
            "sha256": meta.get("jpi_edm930_sha256") or "",
            "flight_id": fid,
            "offset_s": fnum("jpi_edm930_offset_s"),
            "drift_s_per_s": fnum("jpi_edm930_drift_s_per_s"),
            "t_ref_unix": fnum("jpi_edm930_t_ref_unix"),
            "method": meta.get("jpi_edm930_method") or "",
            "residual": fnum("jpi_edm930_residual"),
            "residual_unit": meta.get("jpi_edm930_residual_unit") or "",
            "shared": False,
        }
    }


def _result_from_stored(cap: dict) -> AlignResult:
    residual = cap.get("residual")
    try:
        residual_f = float("nan") if residual is None else float(residual)
    except (TypeError, ValueError):
        residual_f = float("nan")
    return AlignResult(
        ok=True,
        method=str(cap.get("method") or "stored"),
        offset_s=float(cap.get("offset_s") or 0.0),
        drift_s_per_s=float(cap.get("drift_s_per_s") or 0.0),
        t_ref_unix=float(cap.get("t_ref_unix") or 0.0),
        residual=residual_f,
        residual_unit=str(cap.get("residual_unit") or ""),
        detail="reused the stored fit",
    )


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


def _catalog_downloads(jpi_dir: Path) -> tuple[list[dict], list[dict]]:
    """File hashes and flight time spans. Sample arrays are not kept."""
    if not jpi_dir.is_dir():
        return [], []
    files = []
    spans = []
    for path in sorted(jpi_dir.iterdir()):
        if not path.is_file() or path.suffix.lower() not in _SUFFIXES:
            continue
        if path.suffix.lower() in {".csv", ".txt"} and not path.name.upper().startswith("U"):
            continue
        sha = _sha256(path)
        download: Download | None = None
        error = ""
        try:
            download = parse_file(path)
        except ParseError as exc:
            error = str(exc)
        except OSError as exc:
            error = str(exc)
        n_flights = 0 if download is None else len(download.flights)
        files.append(
            {"path": path, "name": path.name, "sha256": sha, "error": error, "flights": n_flights}
        )
        if download is not None:
            for flight in download.flights:
                if not flight.samples or flight.start is None or flight.end is None:
                    continue
                spans.append(
                    {
                        "name": path.name,
                        "t0": flight.start.replace(tzinfo=timezone.utc).timestamp(),
                        "t1": flight.end.replace(tzinfo=timezone.utc).timestamp(),
                    }
                )
        del download
    _release_memory()
    return files, spans


def _downloads_for_span(files: list[dict], spans: list[dict], span: tuple[float, float], window_s: float) -> list[dict]:
    db0, db1 = span
    needed = {
        item["name"]
        for item in spans
        if min(item["t1"] + window_s, db1) - max(item["t0"] - window_s, db0) > 0
    }
    out = []
    for meta in files:
        row = {
            "path": meta["path"],
            "name": meta["name"],
            "sha256": meta["sha256"],
            "error": meta["error"],
            "download": None,
        }
        if meta["name"] in needed and not meta["error"]:
            try:
                row["download"] = parse_file(meta["path"])
            except (ParseError, OSError) as exc:
                row["error"] = str(exc)
        out.append(row)
    return out


def _connect(db: Path, *, write: bool) -> sqlite3.Connection:
    if write:
        conn = sqlite3.connect(db, timeout=5.0)
    else:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5.0)
    conn.execute("PRAGMA busy_timeout=5000")
    # Do not map the whole flight file. IMU tables are most of the bytes
    # and the fit only needs the low-rate gps / pressure columns.
    conn.execute("PRAGMA mmap_size=0")
    conn.execute("PRAGMA cache_size=-2048")
    return conn


def _live_names(dbs: list[Path], now: float | None = None) -> set[str]:
    """Sessions the recorder still has open.

    That is a non-empty ``*-wal`` written in the last two minutes, or the
    newest session file when it or its WAL was written in that window.
    A leftover WAL from an unclean shutdown is older than the window and
    is still merged. A finished flight is the newest file only until the
    next session, and it is merged once its mtime goes quiet.
    """
    if not dbs:
        return set()
    now = time.time() if now is None else now
    live = set()
    newest = max(dbs, key=lambda path: (path.stat().st_mtime, path.name))

    def fresh(path: Path, *, nonempty: bool) -> bool:
        try:
            st = path.stat()
        except OSError:
            return False
        if nonempty and st.st_size <= 0:
            return False
        return now - st.st_mtime <= _LIVE_S

    for db in dbs:
        # Non-empty WAL touched in the last two minutes is being written.
        if fresh(Path(str(db) + "-wal"), nonempty=True):
            live.add(db.name)
    # The recorder's current file is the newest one, and it has a -wal/-shm
    # while it is open. A finished flight is also newest until the next
    # session, but its sidecars are gone or older than the window.
    newest_wal = Path(str(newest) + "-wal")
    newest_shm = Path(str(newest) + "-shm")
    sidecar_fresh = fresh(newest_wal, nonempty=False) or fresh(newest_shm, nonempty=False)
    try:
        file_fresh = now - newest.stat().st_mtime <= _LIVE_S
    except OSError:
        file_fresh = False
    if file_fresh and (newest_wal.exists() or newest_shm.exists()) and sidecar_fresh:
        live.add(newest.name)
    return live


def _session_unix(path: Path) -> float:
    match = _STAMP.match(path.name)
    if match:
        return datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).timestamp()
    return path.stat().st_mtime


def _parse_since(text: str) -> float:
    raw = text.strip()
    if raw.endswith("Z"):
        raw = raw[:-1]
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%Y%m%dT%H%M%S"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
    raise ValueError(text)


def _release_memory() -> None:
    gc.collect()
    try:
        import ctypes

        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError):
        pass


def _checkpoint(path: Path, ledger: dict, now: str, jpi_dir: Path, flights_dir: Path) -> None:
    ledger["updated"] = now
    ledger["jpi_dir"] = str(jpi_dir)
    ledger["flights_dir"] = str(flights_dir)
    ledger["version"] = 1
    _write_ledger(path, ledger)


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
    # Also a parent option. SUPPRESS so a value passed before the subcommand
    # is not replaced by this default. The copy installed at /usr/local/bin
    # used to pass it after the subcommand.
    p.add_argument(
        "--flights-dir",
        type=Path,
        default=argparse.SUPPRESS,
        help="directory of *.db sessions (also accepted before the subcommand)",
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
    p.add_argument(
        "--since",
        default=None,
        help="skip sessions that start before this UTC time (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ)",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="open at most this many flight DBs after the live-session skip and --since",
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
    since = None
    if getattr(args, "since", None):
        try:
            since = _parse_since(args.since)
        except ValueError:
            print(f"--since {args.since!r} is not a UTC date or timestamp", file=sys.stderr)
            return 2
    limit = getattr(args, "limit", None)
    if limit is not None and limit < 0:
        print("--limit must be >= 0", file=sys.stderr)
        return 2
    run_auto(
        flights_dir=flights,
        jpi_dir=jpi_dir,
        ledger_path=Path(args.ledger),
        apply=not args.dry_run,
        window_s=args.window_s,
        since=since,
        limit=limit,
    )
    return 0
