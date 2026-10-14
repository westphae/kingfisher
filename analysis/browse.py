"""HTTP API payloads for the flight-analysis browser."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from analysis.catalog import SessionEntry, inspect_db, load_catalog
from analysis.clockalign import inspect_clock
from analysis.db import (
    CORE_CABIN,
    CORE_POD,
    OPTIONAL_POD,
    connect_ro,
    howgozit_tables,
    list_tables,
    sensor_tables,
    table_columns,
)
from analysis.sidecar import load_sidecar


SKIP_PLOT = frozenset({"ts_ns"})
# Columns that are categorical / ids — hide from the plot picker.
SKIP_PLOT_PREFIX = ("schema_",)


def _ns_to_utc(ts_ns: int | None) -> str | None:
    if ts_ns is None:
        return None
    return datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def resolve_db(flights_dir: Path, name: str) -> Path:
    base = Path(name).name
    if base != name or not base.endswith(".db"):
        raise ValueError("bad file name")
    path = (flights_dir / base).resolve()
    if path.parent.resolve() != flights_dir.resolve() or not path.is_file():
        raise FileNotFoundError(base)
    return path


def _table_span_fast(conn, table: str) -> tuple[int | None, int | None, int]:
    """Insert-order span via rowid — O(1), good enough for the browser."""
    try:
        n = conn.execute(f'SELECT MAX(rowid) FROM "{table}"').fetchone()[0] or 0
        if not n:
            return None, None, 0
        first = conn.execute(
            f'SELECT ts_ns FROM "{table}" ORDER BY rowid ASC LIMIT 1'
        ).fetchone()
        last = conn.execute(
            f'SELECT ts_ns FROM "{table}" ORDER BY rowid DESC LIMIT 1'
        ).fetchone()
        a = int(first[0]) if first and first[0] is not None else None
        b = int(last[0]) if last and last[0] is not None else None
        return a, b, int(n)
    except Exception:  # noqa: BLE001
        return None, None, 0


def _catalog_index(catalog_path: Path) -> dict[str, dict[str, Any]]:
    if not catalog_path.is_file():
        return {}
    try:
        cat = load_catalog(catalog_path)
    except (OSError, json.JSONDecodeError):
        return {}
    return {s["file"]: s for s in cat.get("sessions", [])}


def list_sessions(flights_dir: Path, catalog_path: Path) -> list[dict[str, Any]]:
    """Merge on-disk DBs with catalog.json. New files get a cheap header."""
    idx = _catalog_index(catalog_path)
    out: list[dict[str, Any]] = []
    for path in sorted(flights_dir.glob("*.db")):
        cached = idx.get(path.name)
        rec: dict[str, Any] = {
            "file": path.name,
            "size_mb": round(path.stat().st_size / 1e6, 2),
            "class": (cached or {}).get("class") or "unknown",
            "from_catalog": cached is not None,
        }
        if cached:
            rec.update(
                {
                    "duration_h": cached.get("duration_h"),
                    "start_utc": cached.get("start_utc"),
                    "end_utc": cached.get("end_utc"),
                    "max_gs": cached.get("max_gs"),
                    "sensors": cached.get("sensors") or [],
                    "has_pitot": cached.get("has_pitot"),
                    "notes_preview": cached.get("notes_preview") or "",
                    "tags": cached.get("tags") or [],
                    "missing_core_cabin": cached.get("missing_core_cabin") or [],
                    "missing_core_pod": cached.get("missing_core_pod") or [],
                }
            )
        rec.update(_quick_header(path))
        # Prefer true session start / sidecar notes over stale catalog span.
        if rec.get("true_start_utc"):
            rec["start_utc"] = rec["true_start_utc"][:19] + "Z" if rec["true_start_utc"] else rec.get("start_utc")
        out.append(rec)
    out.sort(key=lambda r: r.get("true_start_utc") or r.get("start_utc") or r["file"], reverse=True)
    return out


def _quick_header(path: Path) -> dict[str, Any]:
    extra: dict[str, Any] = {}
    sc = load_sidecar(path)
    if sc:
        extra["sidecar"] = sc.path.name
        extra["notes_preview"] = extra.get("notes_preview") or " ".join(sc.notes.split())[:200]
        if sc.overrides.get("class"):
            extra["class"] = sc.overrides["class"].strip().lower()
        if "tags" in sc.overrides:
            extra["tags"] = [t.strip() for t in sc.overrides["tags"].split(",") if t.strip()]
    try:
        conn = connect_ro(path)
    except Exception as e:  # noqa: BLE001
        extra["error"] = str(e)
        return extra
    try:
        tables = list_tables(conn)
        extra["sensors"] = sensor_tables(tables)
        extra["has_pitot"] = "ms4525" in extra["sensors"] or "airspeed" in extra["sensors"]
        extra["missing_core_cabin"] = [t for t in CORE_CABIN if t not in extra["sensors"]]
        extra["missing_core_pod"] = [t for t in CORE_POD if t not in extra["sensors"]]
        if "_session" in tables:
            cols = [c[1] for c in conn.execute('PRAGMA table_info("_session")')]
            row = conn.execute("SELECT * FROM _session LIMIT 1").fetchone()
            if row:
                m = dict(zip(cols, row))
                extra["session_start"] = m.get("start_time")
                extra["aircraft"] = m.get("aircraft")
                extra["notes"] = (m.get("notes") or "")[:400]
                if extra["notes"] and not extra.get("notes_preview"):
                    extra["notes_preview"] = extra["notes"][:200]
        if "metadata" in tables:
            keys = (
                "clock_true_start_utc",
                "clock_synced_at_utc",
                "clock_startup_state",
                "clock_startup_fallback",
            )
            q = ",".join("?" * len(keys))
            for k, v in conn.execute(
                f"SELECT key, value FROM metadata WHERE key IN ({q})", keys
            ):
                if k == "clock_true_start_utc":
                    extra["true_start_utc"] = v
                elif k == "clock_synced_at_utc":
                    extra["synced_at_utc"] = v
                elif k == "clock_startup_state":
                    extra["clock_state"] = v
                elif k == "clock_startup_fallback":
                    extra["clock_fallback"] = str(v).lower() == "true"
    except Exception as e:  # noqa: BLE001
        extra["error"] = str(e)
    finally:
        conn.close()
    return extra


def session_detail(path: Path) -> dict[str, Any]:
    entry: SessionEntry = inspect_db(path)
    payload = entry.to_json()
    conn = connect_ro(path)
    try:
        clock = inspect_clock(conn)
        tables = list_tables(conn)
        sensors = sensor_tables(tables)
        aligned_span = _aligned_span(conn, sensors, clock.model)
        payload["clock"] = clock.to_json()
        payload["true_start_utc"] = clock.true_start_utc
        payload["plot_t0_ns"] = aligned_span[0]
        payload["plot_t1_ns"] = aligned_span[1]
        payload["plot_t0_utc"] = _ns_to_utc(aligned_span[0])
        payload["plot_t1_utc"] = _ns_to_utc(aligned_span[1])
        if aligned_span[0] is not None and aligned_span[1] is not None:
            payload["duration_h"] = round(
                (aligned_span[1] - aligned_span[0]) / 1e9 / 3600.0, 3
            )
        payload["tables"] = []
        for t in sensors + howgozit_tables(tables):
            cols = table_columns(conn, t)
            if "ts_ns" not in cols:
                payload["tables"].append(
                    {"name": t, "rows": 0, "columns": cols, "plot": []}
                )
                continue
            a, b, n = _table_span_fast(conn, t)
            plot_cols = [
                c
                for c in cols
                if c not in SKIP_PLOT and not any(c.startswith(p) for p in SKIP_PLOT_PREFIX)
            ]
            payload["tables"].append(
                {
                    "name": t,
                    "rows": n,
                    "t0_utc": _ns_to_utc(a),
                    "t1_utc": _ns_to_utc(b),
                    "columns": cols,
                    "plot": plot_cols,
                    "core_cabin": t in CORE_CABIN,
                    "core_pod": t in CORE_POD,
                    "optional_pod": t in OPTIONAL_POD,
                }
            )
        if "sensor_attrs" in tables:
            payload["sensor_attrs"] = [
                {"device": d, "channel": ch, "attr": a, "value": v}
                for d, ch, a, v in conn.execute(
                    """
                    SELECT device, channel, attr, value FROM sensor_attrs
                    WHERE attr IN ('sampling_frequency', 'default_hz')
                    ORDER BY device, attr
                    """
                )
            ]
        else:
            payload["sensor_attrs"] = []
    finally:
        conn.close()
    return payload


def _aligned_span(
    conn, sensors: list[str], model
) -> tuple[int | None, int | None]:
    texpr = model.aligned_sql("ts_ns") if model is not None else "ts_ns"
    tmin = tmax = None
    prefer = [t for t in ("gps", "ahrs", "icm45686_accel", "system") if t in sensors]
    use = prefer or [t for t in sensors]
    for t in use:
        try:
            row = conn.execute(
                f'SELECT MIN({texpr}), MAX({texpr}) FROM "{t}"'
            ).fetchone()
        except Exception:  # noqa: BLE001
            continue
        if not row or row[0] is None:
            continue
        a, b = int(row[0]), int(row[1])
        tmin = a if tmin is None else min(tmin, a)
        tmax = b if tmax is None else max(tmax, b)
    return tmin, tmax
