"""Write one aligned JPI flight into a Kingfisher SQLite database.

``ts_ns`` is the aligned Kingfisher clock (Pi ``CLOCK_REALTIME`` nanoseconds),
so the table joins to every other stream. ``jpi_time`` is the monitor face,
text, with no timezone. Alignment parameters live once in ``metadata``, not
on every row.

Re-running drops ``jpi_edm930`` and the ``jpi_edm930_*`` metadata keys, then
writes them again.
"""

from __future__ import annotations

import sqlite3

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


def write_flight(
    conn: sqlite3.Connection,
    flight: Flight,
    result: AlignResult,
    source_name: str,
) -> int:
    if not result.ok:
        raise ValueError(f"refusing to write a rejected alignment: {result.reason}")
    if not flight.samples:
        raise ValueError("flight has no samples")
    columns = ["ts_ns", "jpi_time", *flight.channels]
    col_sql = ", ".join(_quote(c) for c in columns)
    defs = ["ts_ns INTEGER NOT NULL", "jpi_time TEXT NOT NULL"]
    defs.extend(f"{_quote(c)} REAL" for c in flight.channels)
    stamps = aligned_times(flight, result)
    previous = conn.isolation_level
    conn.isolation_level = None
    conn.execute("BEGIN")
    try:
        conn.execute(f'DROP TABLE IF EXISTS "{TABLE}"')
        conn.execute(f'CREATE TABLE "{TABLE}" ({", ".join(defs)})')
        conn.execute(f'CREATE INDEX "{TABLE}_ts" ON "{TABLE}"(ts_ns)')
        placeholders = ", ".join("?" for _ in columns)
        rows = []
        for sample, ts in zip(flight.samples, stamps):
            jpi_time = sample.t.strftime("%Y-%m-%dT%H:%M:%S")
            values = [ts, jpi_time]
            for name in flight.channels:
                val = sample.values.get(name)
                values.append(None if val is None else float(val))
            rows.append(values)
        conn.executemany(
            f'INSERT INTO "{TABLE}" ({col_sql}) VALUES ({placeholders})',
            rows,
        )
        _write_meta(conn, flight, result, source_name)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.isolation_level = previous
    return len(rows)


def _write_meta(conn, flight: Flight, result: AlignResult, source_name: str) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS metadata (
          key TEXT PRIMARY KEY,
          value TEXT
        )
        """
    )
    conn.execute("DELETE FROM metadata WHERE key LIKE ?", (META_PREFIX + "%",))
    oat = "" if result.oat_pearson is None else f"{result.oat_pearson:.6f}"
    pairs = {
        "jpi_edm930_method": result.method,
        "jpi_edm930_offset_s": f"{result.offset_s:.6f}",
        "jpi_edm930_drift_s_per_s": f"{result.drift_s_per_s:.8e}",
        "jpi_edm930_drift_s_per_h": f"{result.drift_s_per_h:.6f}",
        "jpi_edm930_t_ref_unix": f"{result.t_ref_unix:.3f}",
        "jpi_edm930_residual": f"{result.residual:.6g}",
        "jpi_edm930_residual_unit": result.residual_unit,
        "jpi_edm930_source": source_name,
        "jpi_edm930_flight_id": str(flight.flight_id),
        "jpi_edm930_oat_pearson": oat,
        "jpi_edm930_oat_source": result.oat_source or "",
        "jpi_edm930_detail": result.detail,
    }
    conn.executemany(
        "INSERT INTO metadata(key, value) VALUES(?, ?)",
        list(pairs.items()),
    )


def _quote(name: str) -> str:
    if not name or any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789_" for ch in name):
        raise ValueError(f"unsafe column name {name!r}")
    return name
