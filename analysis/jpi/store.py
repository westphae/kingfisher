"""Write aligned JPI captures into a Kingfisher SQLite database.

``ts_ns`` is the aligned Kingfisher clock (Pi ``CLOCK_REALTIME`` nanoseconds),
so the table joins to every other stream. ``jpi_time`` is the monitor face,
text, with no timezone. One session can hold several EDM recordings (a taxi
to the pump, then a restart). They share ``jpi_edm930`` and are distinguished
by ``capture_id`` (``<file>#<flight id>``). Captures do not overlap in time.

Re-running drops ``jpi_edm930`` and the ``jpi_edm930_*`` metadata keys, then
writes the whole set again.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from analysis.jpi.align import AlignResult, flight_unix
from analysis.jpi.parse import Flight

TABLE = "jpi_edm930"
META_PREFIX = "jpi_edm930_"


def aligned_times(flight: Flight, result: AlignResult) -> list[int]:
    """Kingfisher ``ts_ns`` for each sample."""
    if not result.ok:
        raise ValueError("refusing to stamp times from a rejected alignment")
    t_j = flight_unix(flight)
    t_hat = t_j + result.offset_s + result.drift_s_per_s * (t_j - result.t_ref_unix)
    return [int(round(t * 1e9)) for t in t_hat]


@dataclass
class Capture:
    flight: Flight
    result: AlignResult
    source_name: str
    source_sha256: str = ""
    shared: bool = False

    @property
    def capture_id(self) -> str:
        return f"{self.source_name}#{self.flight.flight_id}"


def write_flight(
    conn: sqlite3.Connection,
    flight: Flight,
    result: AlignResult,
    source_name: str,
    source_sha256: str = "",
) -> int:
    return write_captures(
        conn,
        [Capture(flight, result, source_name, source_sha256, shared=False)],
    )


def write_captures(
    conn: sqlite3.Connection,
    captures: list[Capture],
    keep: list[dict] | None = None,
) -> int:
    """Replace ``jpi_edm930`` with these captures plus any kept rows.

    ``keep`` is for a capture whose source file is no longer in the raw
    directory: the rows already in the table, as column-name dicts.
    """
    if not captures and not keep:
        raise ValueError("no captures to write")
    for cap in captures:
        if not cap.result.ok:
            raise ValueError(f"refusing to write a rejected alignment: {cap.result.reason}")
        if not cap.flight.samples:
            raise ValueError("flight has no samples")
    packed = _pack(captures, keep or [])
    if not packed:
        raise ValueError("no captures to write")
    _reject_overlap(packed)
    columns = ["ts_ns", "jpi_time", "capture_id"]
    channel_names = []
    seen = set(columns)
    for row in packed:
        for key in row:
            if key not in seen and key not in columns:
                channel_names.append(key)
                seen.add(key)
    columns.extend(channel_names)
    defs = [
        "ts_ns INTEGER NOT NULL",
        "jpi_time TEXT NOT NULL",
        "capture_id TEXT NOT NULL",
    ]
    defs.extend(f"{_quote(c)} REAL" for c in channel_names)
    previous = conn.isolation_level
    conn.isolation_level = None
    conn.execute("BEGIN")
    try:
        conn.execute(f'DROP TABLE IF EXISTS "{TABLE}"')
        conn.execute(f'CREATE TABLE "{TABLE}" ({", ".join(defs)})')
        conn.execute(f'CREATE INDEX "{TABLE}_ts" ON "{TABLE}"(ts_ns)')
        col_sql = ", ".join(_quote(c) for c in columns)
        placeholders = ", ".join("?" for _ in columns)
        values = []
        for row in packed:
            values.append([row.get(c) for c in columns])
        conn.executemany(
            f'INSERT INTO "{TABLE}" ({col_sql}) VALUES ({placeholders})',
            values,
        )
        _write_meta(conn, captures, keep or [])
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.isolation_level = previous
    return len(values)


def read_kept_rows(conn: sqlite3.Connection, capture_ids: set[str]) -> list[dict]:
    """Rows whose capture should survive a rewrite. No full-table interpretation."""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (TABLE,),
    ).fetchone()
    if not row:
        return []
    cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{TABLE}")')]
    quoted = ", ".join(_quote(c) for c in cols)
    out = []
    for rec in conn.execute(f'SELECT {quoted} FROM "{TABLE}" ORDER BY ts_ns'):
        item = dict(zip(cols, rec))
        cid = item.get("capture_id")
        if cid is None or cid in capture_ids:
            out.append(item)
    return out


def _pack(captures: list[Capture], keep: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for cap in captures:
        stamps = aligned_times(cap.flight, cap.result)
        for sample, ts in zip(cap.flight.samples, stamps):
            row = {
                "ts_ns": ts,
                "jpi_time": sample.t.strftime("%Y-%m-%dT%H:%M:%S"),
                "capture_id": cap.capture_id,
            }
            for name in cap.flight.channels:
                val = sample.values.get(name)
                row[name] = None if val is None else float(val)
            rows.append(row)
    for item in keep:
        if "ts_ns" not in item:
            continue
        row = dict(item)
        row.setdefault("capture_id", "kept")
        rows.append(row)
    rows.sort(key=lambda r: (int(r["ts_ns"]), str(r.get("capture_id") or "")))
    return rows


def _reject_overlap(rows: list[dict]) -> None:
    ranges: dict[str, tuple[int, int]] = {}
    for row in rows:
        cid = str(row.get("capture_id") or "")
        ts = int(row["ts_ns"])
        lo, hi = ranges.get(cid, (ts, ts))
        ranges[cid] = (min(lo, ts), max(hi, ts))
    spans = sorted(ranges.items(), key=lambda kv: kv[1][0])
    for i in range(1, len(spans)):
        prev_id, (_plo, phi) = spans[i - 1]
        cid, (lo, _hi) = spans[i]
        if lo <= phi:
            raise ValueError(f"capture {cid} overlaps {prev_id}")


def _write_meta(conn, captures: list[Capture], keep: list[dict]) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS metadata (
          key TEXT PRIMARY KEY,
          value TEXT
        )
        """
    )
    conn.execute("DELETE FROM metadata WHERE key LIKE ?", (META_PREFIX + "%",))
    primary = max(captures, key=lambda c: len(c.flight.samples)) if captures else None
    if primary is None:
        result_method = "kept"
        pairs = {
            "jpi_edm930_method": result_method,
            "jpi_edm930_source": "",
            "jpi_edm930_sha256": "",
            "jpi_edm930_flight_id": "",
            "jpi_edm930_offset_s": "",
            "jpi_edm930_drift_s_per_s": "",
            "jpi_edm930_drift_s_per_h": "",
            "jpi_edm930_t_ref_unix": "",
            "jpi_edm930_residual": "",
            "jpi_edm930_residual_unit": "",
            "jpi_edm930_oat_pearson": "",
            "jpi_edm930_oat_source": "",
            "jpi_edm930_detail": "rows kept; source file was not in the raw directory",
        }
    else:
        result = primary.result
        oat = "" if result.oat_pearson is None else f"{result.oat_pearson:.6f}"
        pairs = {
            "jpi_edm930_method": result.method,
            "jpi_edm930_offset_s": f"{result.offset_s:.6f}",
            "jpi_edm930_drift_s_per_s": f"{result.drift_s_per_s:.8e}",
            "jpi_edm930_drift_s_per_h": f"{result.drift_s_per_h:.6f}",
            "jpi_edm930_t_ref_unix": f"{result.t_ref_unix:.3f}",
            "jpi_edm930_residual": f"{result.residual:.6g}",
            "jpi_edm930_residual_unit": result.residual_unit,
            "jpi_edm930_source": primary.source_name,
            "jpi_edm930_sha256": primary.source_sha256,
            "jpi_edm930_flight_id": str(primary.flight.flight_id),
            "jpi_edm930_oat_pearson": oat,
            "jpi_edm930_oat_source": result.oat_source or "",
            "jpi_edm930_detail": result.detail,
        }
    described = [_capture_json(c) for c in captures]
    for item in keep:
        cid = str(item.get("capture_id") or "kept")
        if any(d["id"] == cid for d in described):
            continue
        described.append(
            {
                "id": cid,
                "source": "",
                "sha256": "",
                "flight_id": "",
                "kept": True,
            }
        )
    ids = [d["id"] for d in described]
    pairs["jpi_edm930_captures"] = str(len(ids))
    pairs["jpi_edm930_capture_ids"] = ",".join(ids)
    pairs["jpi_edm930_captures_json"] = json.dumps(described, separators=(",", ":"))
    conn.executemany(
        "INSERT INTO metadata(key, value) VALUES(?, ?)",
        list(pairs.items()),
    )


def _capture_json(cap: Capture) -> dict:
    result = cap.result
    return {
        "id": cap.capture_id,
        "source": cap.source_name,
        "sha256": cap.source_sha256,
        "flight_id": str(cap.flight.flight_id),
        "offset_s": result.offset_s,
        "drift_s_per_s": result.drift_s_per_s,
        "t_ref_unix": result.t_ref_unix,
        "method": result.method,
        "residual": None if result.residual != result.residual else result.residual,
        "residual_unit": result.residual_unit,
        "shared": bool(cap.shared),
    }


def _quote(name: str) -> str:
    if not name or any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789_" for ch in name):
        raise ValueError(f"unsafe column name {name!r}")
    return name
