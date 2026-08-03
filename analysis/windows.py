"""Motion windows for every kingfisher session DB.

Labels 1-second epochs (then merges contiguous runs into segments) as one of:

- ``stationary`` — still; primary set for sensor calibration ("level windows")
- ``taxi`` — GPS groundspeed in taxi band (needs reliable GPS)
- ``flight`` — GPS groundspeed in flight band
- ``transient`` — short IMU excursion (pick-up / bump) or sustained motion
  without GPS evidence of taxi/flight (e.g. desk handling)

Pre-session samples (``ts_ns < _session.start_time``) are excluded.

Parquet layout (append-friendly, partitioned by session)::

    ~/kingfisher/analysis-cache/windows/
      manifest.json
      epochs/session_id=<stem>/part.parquet
      segments/session_id=<stem>/part.parquet
      segments_all.parquet

Re-running a session overwrites only that session's partitions.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Literal

import numpy as np

from analysis.db import (
    connect_ro,
    gps_speed_col,
    list_tables,
    session_start_ns,
    table_columns,
)

Label = Literal["stationary", "taxi", "flight", "transient"]

LABELS: tuple[Label, ...] = ("stationary", "taxi", "flight", "transient")

GS_FLIGHT_KT = 40.0
GS_TAXI_KT = 5.0
GYRO_STILL = 0.04
ACCEL_STD_STILL = 0.35
ACCEL_MAG_TOL = 1.2
G0 = 9.80665
TRANSIENT_MAX_S = 20.0
EPOCH_S = 1.0


@dataclass
class WindowParams:
    epoch_s: float = EPOCH_S
    gs_flight_kt: float = GS_FLIGHT_KT
    gs_taxi_kt: float = GS_TAXI_KT
    gyro_still: float = GYRO_STILL
    accel_std_still: float = ACCEL_STD_STILL
    accel_mag_tol: float = ACCEL_MAG_TOL
    transient_max_s: float = TRANSIENT_MAX_S
    g0: float = G0


@dataclass
class SessionWindows:
    session_id: str
    db_path: str
    session_start_utc: str | None
    params: dict[str, Any]
    epoch_rows: list[dict[str, Any]] = field(default_factory=list)
    segment_rows: list[dict[str, Any]] = field(default_factory=list)
    label_counts: dict[str, int] = field(default_factory=dict)
    error: str | None = None


def _session_id(db_path: Path) -> str:
    return db_path.stem


def _epoch_expr(epoch_s: float) -> str:
    """SQLite expression: epoch index from ts_ns (integer seconds if epoch_s==1)."""
    if abs(epoch_s - 1.0) < 1e-9:
        return "(ts_ns / 1000000000)"
    ns = int(epoch_s * 1e9)
    return f"(ts_ns / {ns})"


def _gps_epoch_stats(
    conn: sqlite3.Connection,
    *,
    min_ts: int | None,
    epoch_s: float,
    speed_col: str,
) -> dict[int, dict[str, Any]]:
    ep = _epoch_expr(epoch_s)
    scale = " * 1.94384" if speed_col == "speed_mps" else ""
    where = " WHERE ts_ns >= ?" if min_ts is not None else ""
    args: tuple[Any, ...] = (min_ts,) if min_ts is not None else ()
    q = f"""
    SELECT {ep} AS ep,
           COUNT(*) AS n,
           AVG("{speed_col}"){scale} AS mean_gs,
           MAX("{speed_col}"){scale} AS max_gs
    FROM gps
    {where}
    GROUP BY ep
    """
    out: dict[int, dict[str, Any]] = {}
    for ep_i, n, mean_gs, max_gs in conn.execute(q, args):
        out[int(ep_i)] = {
            "n": int(n),
            "mean_gs": float(mean_gs) if mean_gs is not None else None,
            "max_gs": float(max_gs) if max_gs is not None else None,
        }
    return out


def _vec_norm_epoch_stats(
    conn: sqlite3.Connection,
    table: str,
    cols: tuple[str, str, str],
    *,
    min_ts: int | None,
    epoch_s: float,
) -> dict[int, dict[str, Any]]:
    """Per-epoch count / mean‖v‖ / max‖v‖ / std‖v‖ via SQL."""
    if table not in list_tables(conn):
        return {}
    have = set(table_columns(conn, table))
    if not all(c in have for c in cols):
        return {}
    x, y, z = cols
    ep = _epoch_expr(epoch_s)
    # norm per row, then aggregate
    where = " WHERE ts_ns >= ?" if min_ts is not None else ""
    args: tuple[Any, ...] = (min_ts,) if min_ts is not None else ()
    q = f"""
    SELECT ep,
           COUNT(*) AS n,
           AVG(nrm) AS mean_norm,
           MAX(nrm) AS max_norm,
           AVG(nrm*nrm) AS mean_norm2
    FROM (
      SELECT {ep} AS ep,
             SQRT("{x}"*"{x}" + "{y}"*"{y}" + "{z}"*"{z}") AS nrm
      FROM "{table}"
      {where}
    )
    GROUP BY ep
    """
    out: dict[int, dict[str, Any]] = {}
    for ep_i, n, mean_n, max_n, mean_n2 in conn.execute(q, args):
        mean_n = float(mean_n) if mean_n is not None else 0.0
        mean_n2 = float(mean_n2) if mean_n2 is not None else 0.0
        var = max(0.0, mean_n2 - mean_n * mean_n)
        out[int(ep_i)] = {
            "n": int(n),
            "mean_norm": mean_n,
            "max_norm": float(max_n) if max_n is not None else 0.0,
            "std_norm": float(np.sqrt(var)),
        }
    return out


def _classify_epoch(
    *,
    gps_n: int,
    mean_gs: float | None,
    accel: dict[str, Any],
    gyro: dict[str, Any],
    p: WindowParams,
) -> Label:
    gps_ok = gps_n > 0 and mean_gs is not None and np.isfinite(mean_gs)
    if gps_ok:
        assert mean_gs is not None
        if mean_gs >= p.gs_flight_kt:
            return "flight"
        if mean_gs >= p.gs_taxi_kt:
            return "taxi"

    gyro_n = int(gyro.get("n", 0))
    accel_n = int(accel.get("n", 0))
    gyro_busy = gyro_n > 0 and float(gyro.get("mean_norm", 0)) > p.gyro_still
    accel_busy = False
    if accel_n > 2:
        std_n = float(accel.get("std_norm", 0))
        mean_n = float(accel.get("mean_norm", p.g0))
        accel_busy = std_n > p.accel_std_still or abs(mean_n - p.g0) > p.accel_mag_tol

    if gyro_busy or accel_busy:
        return "transient"
    return "stationary"


def _merge_segments(epochs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not epochs:
        return []
    segs: list[dict[str, Any]] = []
    cur_label = epochs[0]["label"]
    t0 = epochs[0]["t_start_ns"]
    t1 = epochs[0]["t_end_ns"]
    n_ep = 1
    sum_gs = epochs[0].get("mean_gs") or 0.0
    n_gs = 1 if epochs[0].get("mean_gs") is not None else 0
    sid = epochs[0]["session_id"]

    def flush(label: str, start: int, end: int, n: int, gs_sum: float, gs_n: int) -> None:
        segs.append(
            {
                "session_id": sid,
                "label": label,
                "t_start_ns": start,
                "t_end_ns": end,
                "duration_s": round((end - start) / 1e9, 3),
                "n_epochs": n,
                "mean_gs": (gs_sum / gs_n) if gs_n else None,
            }
        )

    for ep in epochs[1:]:
        if ep["label"] == cur_label:
            t1 = ep["t_end_ns"]
            n_ep += 1
            if ep.get("mean_gs") is not None:
                sum_gs += ep["mean_gs"]
                n_gs += 1
        else:
            flush(cur_label, t0, t1, n_ep, sum_gs, n_gs)
            cur_label = ep["label"]
            t0 = ep["t_start_ns"]
            t1 = ep["t_end_ns"]
            n_ep = 1
            sum_gs = ep.get("mean_gs") or 0.0
            n_gs = 1 if ep.get("mean_gs") is not None else 0
    flush(cur_label, t0, t1, n_ep, sum_gs, n_gs)
    return segs


def label_session(db_path: Path, params: WindowParams | None = None) -> SessionWindows:
    p = params or WindowParams()
    sid = _session_id(db_path)
    out = SessionWindows(
        session_id=sid,
        db_path=str(db_path.resolve()),
        session_start_utc=None,
        params=asdict(p),
    )
    try:
        conn = connect_ro(db_path)
    except sqlite3.Error as e:
        out.error = str(e)
        return out

    try:
        sess_ns = session_start_ns(conn)
        if sess_ns is not None:
            out.session_start_utc = datetime.fromtimestamp(
                sess_ns / 1e9, tz=timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%SZ")

        tables = list_tables(conn)
        span_t0 = span_t1 = None
        for tname in ("gps", "icm45686_accel", "icm45686_gyro", "ahrs"):
            if tname not in tables:
                continue
            q = f'SELECT MIN(ts_ns), MAX(ts_ns) FROM "{tname}"'
            if sess_ns is not None:
                row = conn.execute(q + " WHERE ts_ns >= ?", (sess_ns,)).fetchone()
            else:
                row = conn.execute(q).fetchone()
            if row and row[0] is not None:
                span_t0 = row[0] if span_t0 is None else min(span_t0, row[0])
                span_t1 = row[1] if span_t1 is None else max(span_t1, row[1])

        if span_t0 is None or span_t1 is None or span_t1 <= span_t0:
            out.error = "no timed samples after session start"
            return out

        epoch_ns = int(p.epoch_s * 1e9)
        ep0 = int(span_t0 // epoch_ns)
        ep1 = int(span_t1 // epoch_ns)

        gps_cols = table_columns(conn, "gps") if "gps" in tables else []
        scol = gps_speed_col(gps_cols)
        gps_by = (
            _gps_epoch_stats(conn, min_ts=sess_ns, epoch_s=p.epoch_s, speed_col=scol)
            if scol
            else {}
        )
        accel_by = _vec_norm_epoch_stats(
            conn,
            "icm45686_accel",
            ("accel_x", "accel_y", "accel_z"),
            min_ts=sess_ns,
            epoch_s=p.epoch_s,
        )
        gyro_by = _vec_norm_epoch_stats(
            conn,
            "icm45686_gyro",
            ("anglvel_x", "anglvel_y", "anglvel_z"),
            min_ts=sess_ns,
            epoch_s=p.epoch_s,
        )

        epochs: list[dict[str, Any]] = []
        for e in range(ep0, ep1 + 1):
            gs = gps_by.get(e, {"n": 0, "mean_gs": None, "max_gs": None})
            acc = accel_by.get(e, {"n": 0})
            gyr = gyro_by.get(e, {"n": 0})
            # Skip empty epochs (no GPS and no IMU) — gaps in recording
            if int(gs["n"]) == 0 and int(acc.get("n", 0)) == 0 and int(gyr.get("n", 0)) == 0:
                continue

            label = _classify_epoch(
                gps_n=int(gs["n"]),
                mean_gs=gs["mean_gs"],
                accel=acc,
                gyro=gyr,
                p=p,
            )
            # Parked but bumped
            if (
                label == "stationary"
                and int(gs["n"]) > 0
                and gs["mean_gs"] is not None
                and gs["mean_gs"] < p.gs_taxi_kt
            ):
                gyro_busy = int(gyr.get("n", 0)) > 0 and float(
                    gyr.get("mean_norm", 0)
                ) > p.gyro_still
                accel_busy = int(acc.get("n", 0)) > 2 and (
                    float(acc.get("std_norm", 0)) > p.accel_std_still
                    or abs(float(acc.get("mean_norm", p.g0)) - p.g0) > p.accel_mag_tol
                )
                if gyro_busy or accel_busy:
                    label = "transient"

            t_start = e * epoch_ns
            epochs.append(
                {
                    "session_id": sid,
                    "epoch_i": e - ep0,
                    "t_start_ns": t_start,
                    "t_end_ns": t_start + epoch_ns,
                    "label": label,
                    "gps_n": int(gs["n"]),
                    "mean_gs": gs["mean_gs"],
                    "max_gs": gs["max_gs"],
                    "accel_n": int(acc.get("n", 0)),
                    "accel_mean_norm": acc.get("mean_norm"),
                    "accel_std_norm": acc.get("std_norm"),
                    "gyro_n": int(gyr.get("n", 0)),
                    "gyro_mean_norm": gyr.get("mean_norm"),
                    "gyro_max_norm": gyr.get("max_norm"),
                }
            )

        # Demote tiny GPS flight/taxi blips
        segments = _merge_segments(epochs)
        for seg in segments:
            if seg["label"] in ("flight", "taxi") and seg["duration_s"] < 3.0:
                for ep in epochs:
                    if (
                        ep["t_start_ns"] >= seg["t_start_ns"]
                        and ep["t_end_ns"] <= seg["t_end_ns"]
                    ):
                        ep["label"] = "transient"
        segments = _merge_segments(epochs)

        out.epoch_rows = epochs
        out.segment_rows = segments
        counts: dict[str, int] = {k: 0 for k in LABELS}
        for ep in epochs:
            counts[ep["label"]] = counts.get(ep["label"], 0) + 1
        out.label_counts = counts
    except Exception as e:  # noqa: BLE001
        out.error = str(e)
    finally:
        conn.close()
    return out


def _write_parquet(path: Path, rows: list[dict[str, Any]]) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        table = pa.table({"session_id": pa.array([], type=pa.string())})
        pq.write_table(table, path, compression="zstd")
        return
    table = pa.Table.from_pylist(rows)
    pq.write_table(table, path, compression="zstd")


def write_session_windows(result: SessionWindows, windows_dir: Path) -> None:
    sid = result.session_id
    _write_parquet(
        windows_dir / "epochs" / f"session_id={sid}" / "part.parquet",
        result.epoch_rows,
    )
    _write_parquet(
        windows_dir / "segments" / f"session_id={sid}" / "part.parquet",
        result.segment_rows,
    )


def rebuild_segments_all(windows_dir: Path) -> Path | None:
    import pyarrow.dataset as ds
    import pyarrow.parquet as pq

    seg_root = windows_dir / "segments"
    if not seg_root.is_dir():
        return None
    dataset = ds.dataset(seg_root, format="parquet", partitioning="hive")
    table = dataset.to_table()
    out = windows_dir / "segments_all.parquet"
    pq.write_table(table, out, compression="zstd")
    return out


def update_manifest(
    windows_dir: Path,
    *,
    results: Iterable[SessionWindows],
    params: WindowParams,
) -> Path:
    path = windows_dir / "manifest.json"
    prev: dict[str, Any] = {}
    if path.is_file():
        prev = json.loads(path.read_text(encoding="utf-8"))
    sessions = dict(prev.get("sessions", {}))
    for r in results:
        sessions[r.session_id] = {
            "db_path": r.db_path,
            "session_start_utc": r.session_start_utc,
            "n_epochs": len(r.epoch_rows),
            "n_segments": len(r.segment_rows),
            "label_counts": r.label_counts,
            "error": r.error,
            "updated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    payload = {
        "updated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "params": asdict(params),
        "layout": {
            "epochs": "epochs/session_id=<stem>/part.parquet",
            "segments": "segments/session_id=<stem>/part.parquet",
            "segments_all": "segments_all.parquet",
        },
        "labels": list(LABELS),
        "sessions": sessions,
    }
    windows_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def process_dbs(
    db_paths: list[Path],
    windows_dir: Path,
    *,
    params: WindowParams | None = None,
    rebuild_all: bool = True,
) -> list[SessionWindows]:
    p = params or WindowParams()
    results: list[SessionWindows] = []
    for i, db in enumerate(db_paths, 1):
        print(f"[{i}/{len(db_paths)}] windows {db.name} …")
        r = label_session(db, p)
        results.append(r)
        if r.error:
            print(f"  ERROR: {r.error}")
            continue
        write_session_windows(r, windows_dir)
        print(
            f"  epochs={len(r.epoch_rows)} segments={len(r.segment_rows)} "
            f"counts={r.label_counts}"
        )
    update_manifest(windows_dir, results=results, params=p)
    if rebuild_all:
        out = rebuild_segments_all(windows_dir)
        if out:
            print(f"Wrote {out}")
    return results
