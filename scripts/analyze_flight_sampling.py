#!/usr/bin/env python3
"""Analyze ts_ns regularity and value sanity across kingfisher flight DBs."""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

SKIP_TABLES = frozenset({"metadata", "_session", "sensor_attrs", "sqlite_sequence"})

# Map DB table names -> sensor_attrs device keys (when they differ).
TABLE_DEVICE_ALIASES = {
    "icm45686_accel": "icm45686-accel",
    "icm45686_gyro": "icm45686-gyro",
}

# Representative value column per table for time-series sanity plots.
REPRESENTATIVE_COL = {
    "ahrs": "roll",
    "airspeed": "ias_kt",
    "bmp581": "pressure_pa",
    "bq27441": "voltage_v",
    "compass": "yaw",
    "geo": "field_total_nt",
    "gps": "speed_kt",
    "icm45686_accel": "accel_x",
    "icm45686_gyro": "anglvel_x",
    "mmc5983": "mag_x_ut",
    "ms4525": "airspeed_dp_pa",
    "press_alt": "pressure_alt_ft",
}


@dataclass
class TableStats:
    table: str
    db_path: str
    row_count: int = 0
    duration_s: float = 0.0
    expected_hz: float | None = None
    median_dt_ms: float | None = None
    mean_hz: float | None = None
    p99_dt_ms: float | None = None
    max_gap_s: float | None = None
    gap_count: int = 0  # intervals > gap_threshold
    gap_threshold_ms: float = 0.0
    gap_pct: float = 0.0
    duplicate_ts: int = 0
    cols: list[str] = field(default_factory=list)
    value_col: str | None = None
    vmin: float | None = None
    vmax: float | None = None
    vstd: float | None = None
    stuck_pct: float | None = None  # % consecutive identical (rounded)
    error: str | None = None


def list_sensor_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    return [r[0] for r in rows if r[0] not in SKIP_TABLES]


def latest_expected_hz(conn: sqlite3.Connection, table: str) -> float | None:
    device = TABLE_DEVICE_ALIASES.get(table, table)
    row = conn.execute(
        """
        SELECT value FROM sensor_attrs
        WHERE device=? AND attr='sampling_frequency' AND channel=''
        ORDER BY ts_ns DESC LIMIT 1
        """,
        (device,),
    ).fetchone()
    if not row:
        row = conn.execute(
            """
            SELECT value FROM sensor_attrs
            WHERE device=? AND attr='default_hz' AND channel=''
            ORDER BY ts_ns DESC LIMIT 1
            """,
            (device,),
        ).fetchone()
    if not row:
        return None
    try:
        return float(row[0])
    except (TypeError, ValueError):
        return None


def table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    info = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    return [r[1] for r in info if r[1] != "ts_ns"]


def pick_value_col(cols: list[str], table: str) -> str | None:
    want = REPRESENTATIVE_COL.get(table)
    if want and want in cols:
        return want
    return cols[0] if cols else None


def analyze_table(
    conn: sqlite3.Connection,
    db_path: Path,
    table: str,
    *,
    max_rows: int,
    subsample_plot: int,
) -> tuple[TableStats, dict[str, Any] | None]:
    stats = TableStats(table=table, db_path=str(db_path))
    plot_data: dict[str, Any] | None = None
    try:
        stats.expected_hz = latest_expected_hz(conn, table)
        cols = table_columns(conn, table)
        stats.cols = cols
        stats.value_col = pick_value_col(cols, table)

        bounds = conn.execute(
            f'SELECT MIN(ts_ns), MAX(ts_ns), COUNT(*) FROM "{table}"'
        ).fetchone()
        if not bounds or bounds[2] == 0:
            return stats, None
        tmin, tmax, stats.row_count = bounds
        stats.duration_s = (tmax - tmin) / 1e9 if tmax and tmin else 0.0

        # Stream ts_ns in chunks for delta stats (cap work on huge tables).
        ts_chunks: list[np.ndarray] = []
        total = 0
        cur = conn.execute(f'SELECT ts_ns FROM "{table}" ORDER BY ts_ns')
        while True:
            batch = cur.fetchmany(100_000)
            if not batch:
                break
            arr = np.array([r[0] for r in batch], dtype=np.int64)
            ts_chunks.append(arr)
            total += len(arr)
            if total >= max_rows:
                break
        if not ts_chunks:
            return stats, None
        ts = np.concatenate(ts_chunks)
        if len(ts) < 2:
            return stats, None

        d = np.diff(ts) / 1e9
        stats.median_dt_ms = float(np.median(d) * 1e3)
        stats.mean_hz = float(1.0 / np.mean(d)) if np.mean(d) > 0 else None
        stats.p99_dt_ms = float(np.percentile(d, 99) * 1e3)
        stats.max_gap_s = float(np.max(d))
        stats.duplicate_ts = int(np.sum(d == 0))

        if stats.expected_hz and stats.expected_hz > 0:
            nominal_ms = 1000.0 / stats.expected_hz
            stats.gap_threshold_ms = max(3.0 * nominal_ms, 250.0)
        elif stats.median_dt_ms:
            stats.gap_threshold_ms = max(3.0 * stats.median_dt_ms, 250.0)
        else:
            stats.gap_threshold_ms = 1000.0

        thr_s = stats.gap_threshold_ms / 1000.0
        gap_mask = d > thr_s
        stats.gap_count = int(np.sum(gap_mask))
        stats.gap_pct = 100.0 * stats.gap_count / len(d) if len(d) else 0.0

        # Value stats on subsample for sanity.
        vcol = stats.value_col
        if vcol:
            step = max(1, stats.row_count // subsample_plot)
            q = conn.execute(
                f'SELECT "{vcol}" FROM "{table}" WHERE "{vcol}" IS NOT NULL '
                f"AND rowid % ? = 0 LIMIT ?",
                (step, subsample_plot),
            )
            vals = np.array([r[0] for r in q.fetchall()], dtype=np.float64)
            if len(vals) > 0:
                stats.vmin = float(np.min(vals))
                stats.vmax = float(np.max(vals))
                stats.vstd = float(np.std(vals))
                if len(vals) > 2:
                    rounded = np.round(vals, 6)
                    same = rounded[1:] == rounded[:-1]
                    stats.stuck_pct = 100.0 * float(np.mean(same))

        # Plot payload: uniform subsample of ts + value + deltas histogram bins.
        plot_n = min(8000, len(ts))
        idx = np.linspace(0, len(ts) - 1, plot_n, dtype=int)
        t_rel = (ts[idx] - ts[0]) / 1e9
        plot_data = {
            "t_rel_s": t_rel.tolist(),
            "dt_ms_hist": (
                (d * 1e3).tolist()
                if len(d) <= 200_000
                else (np.random.default_rng(0).choice(d, size=100_000, replace=False) * 1e3).tolist()
            ),
            "gap_thr_ms": stats.gap_threshold_ms,
        }
        if vcol:
            # Time series for plot: last plot_n rows evenly from full table via SQL.
            span = stats.row_count
            step2 = max(1, span // plot_n)
            rows = conn.execute(
                f'SELECT ts_ns, "{vcol}" FROM "{table}" WHERE "{vcol}" IS NOT NULL '
                f"AND rowid % ? = 0 ORDER BY ts_ns LIMIT ?",
                (step2, plot_n),
            ).fetchall()
            if rows:
                t0 = rows[0][0]
                plot_data["ts_rel"] = [(r[0] - t0) / 1e9 for r in rows]
                plot_data["values"] = [r[1] for r in rows]
                plot_data["value_col"] = vcol
    except Exception as e:  # noqa: BLE001
        stats.error = str(e)
    return stats, plot_data


def analyze_db(db_path: Path, **kwargs: Any) -> list[TableStats]:
    out: list[TableStats] = []
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        for table in list_sensor_tables(conn):
            st, _ = analyze_table(conn, db_path, table, **kwargs)
            out.append(st)
    finally:
        conn.close()
    return out


def aggregate_by_table(all_stats: list[TableStats]) -> dict[str, dict[str, Any]]:
    by: dict[str, list[TableStats]] = {}
    for s in all_stats:
        if s.error or s.row_count == 0:
            continue
        by.setdefault(s.table, []).append(s)
    agg: dict[str, dict[str, Any]] = {}
    for table, items in by.items():
        hz_obs = [x.mean_hz for x in items if x.mean_hz]
        med_dt = [x.median_dt_ms for x in items if x.median_dt_ms]
        gap_pcts = [x.gap_pct for x in items]
        exp = [x.expected_hz for x in items if x.expected_hz]
        agg[table] = {
            "sessions": len(items),
            "total_rows": sum(x.row_count for x in items),
            "expected_hz_median": float(np.median(exp)) if exp else None,
            "observed_hz_median": float(np.median(hz_obs)) if hz_obs else None,
            "median_dt_ms_median": float(np.median(med_dt)) if med_dt else None,
            "gap_pct_mean": float(np.mean(gap_pcts)) if gap_pcts else 0.0,
            "gap_pct_max": float(np.max(gap_pcts)) if gap_pcts else 0.0,
            "max_gap_s_max": max((x.max_gap_s or 0) for x in items),
        }
    return agg


def plot_sensor(
    table: str,
    plot_data: dict[str, Any],
    stats: TableStats,
    out_dir: Path,
) -> str:
    fig, axes = plt.subplots(3, 1, figsize=(10, 8), constrained_layout=True)
    fig.suptitle(f"{table} — {Path(stats.db_path).name}", fontsize=11)

    dt = np.array(plot_data["dt_ms_hist"], dtype=np.float64)
    dt_clip = dt[dt < np.percentile(dt, 99.9)] if len(dt) else dt
    axes[0].hist(dt_clip, bins=80, color="#2563eb", alpha=0.85)
    axes[0].axvline(stats.median_dt_ms or 0, color="#dc2626", ls="--", label="median Δt")
    if stats.expected_hz:
        axes[0].axvline(1000.0 / stats.expected_hz, color="#16a34a", ls=":", label="nominal")
    axes[0].set_xlabel("Δt (ms)")
    axes[0].set_ylabel("count")
    axes[0].set_title("Inter-arrival time distribution")
    axes[0].legend(fontsize=8)

    t_rel = np.array(plot_data.get("ts_rel") or plot_data["t_rel_s"])
    axes[1].plot(t_rel, np.ones_like(t_rel), "|", markersize=2, alpha=0.5, color="#64748b")
    axes[1].set_xlabel("time since first sample (s)")
    axes[1].set_ylabel("arrival")
    axes[1].set_title("Sample arrivals (subsampled)")

    if "values" in plot_data:
        axes[2].plot(plot_data["ts_rel"], plot_data["values"], lw=0.6, color="#0f766e")
        axes[2].set_ylabel(plot_data.get("value_col", "value"))
    else:
        axes[2].text(0.5, 0.5, "no value column", ha="center", va="center")
    axes[2].set_xlabel("time (s)")
    axes[2].set_title("Representative channel (subsampled)")

    fname = out_dir / f"{table}.png"
    fig.savefig(fname, dpi=120)
    plt.close(fig)
    return fname.name


def fmt_hz(x: float | None) -> str:
    if x is None:
        return "—"
    if x >= 100:
        return f"{x:.0f}"
    if x >= 10:
        return f"{x:.1f}"
    return f"{x:.2f}"


def write_report(
    *,
    report_path: Path,
    plot_dir: Path,
    db_files: list[Path],
    all_stats: list[TableStats],
    agg: dict[str, dict[str, Any]],
    plot_db: Path,
    per_table_plots: dict[str, str],
) -> None:
    lines: list[str] = []
    lines.append("# Flight database sampling & data quality report")
    lines.append("")
    lines.append(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("")
    lines.append("## Scope")
    lines.append("")
    lines.append(
        f"- **Databases analyzed:** {len(db_files)} sessions under `~/kingfisher/flights` "
        f"(files modified in the selected window)."
    )
    lines.append(
        f"- **Detail plots:** longest recent session `{plot_db.name}` "
        f"({plot_db.stat().st_size / 1e6:.0f} MB)."
    )
    lines.append("")
    lines.append("## Method")
    lines.append("")
    lines.append(
        "For each sensor table, `ts_ns` rows are sorted and consecutive differences Δt are computed. "
        "**Observed rate** uses mean(1/Δt) over the analyzed span (first N rows per table, default 500k). "
        "**Gaps** are intervals with Δt greater than `max(3×nominal period, 250 ms)` where nominal "
        "comes from `sensor_attrs.sampling_frequency` when logged, else 3× the table median Δt."
    )
    lines.append(
        "Value sanity uses a representative channel per device (subsampled ~8k points): min/max/std "
        "and fraction of consecutive identical samples (possible stuck sensor)."
    )
    lines.append("")
    lines.append("## Aggregate across all sessions")
    lines.append("")
    lines.append(
        "| Table | Sessions | Total rows | Expected Hz† | Observed Hz‡ | Median Δt (ms) | "
        "Mean gap % | Worst gap % | Max gap (s) |"
    )
    lines.append(
        "|-------|----------|------------|--------------|--------------|----------------|"
        "------------|-------------|-------------|"
    )
    for table in sorted(agg.keys()):
        a = agg[table]
        lines.append(
            f"| `{table}` | {a['sessions']} | {a['total_rows']:,} | "
            f"{fmt_hz(a['expected_hz_median'])} | {fmt_hz(a['observed_hz_median'])} | "
            f"{a['median_dt_ms_median']:.2f} | {a['gap_pct_mean']:.3f} | {a['gap_pct_max']:.2f} | "
            f"{a['max_gap_s_max']:.1f} |"
        )
    lines.append("")
    lines.append("† Median of `sensor_attrs.sampling_frequency` per session. ‡ Median of per-session mean Hz.")
    lines.append("")
    lines.append("## Findings")
    lines.append("")

    findings: list[str] = []
    for table, a in sorted(agg.items()):
        exp = a.get("expected_hz_median")
        obs = a.get("observed_hz_median")
        if exp and obs and exp > 1.5 * obs:
            findings.append(
                f"- **`{table}`:** configured ~{fmt_hz(exp)} Hz but observed ~{fmt_hz(obs)} Hz "
                f"(storage/decimation lower than attr snapshot)."
            )
        if a["gap_pct_max"] > 1.0:
            findings.append(
                f"- **`{table}`:** gaps in up to {a['gap_pct_max']:.1f}% of intervals in some sessions "
                f"(max gap {a['max_gap_s_max']:.0f} s) — pod link, service restart, or short recordings."
            )
    if not findings:
        findings.append("- No major cross-session anomalies beyond short-session edge effects.")
    lines.extend(findings)
    lines.append("")
    lines.append(f"## Per-sensor detail (`{plot_db.name}`)")
    lines.append("")

    plot_stats = [s for s in all_stats if s.db_path == str(plot_db) and s.row_count > 0]
    plot_stats.sort(key=lambda s: s.table)

    for st in plot_stats:
        lines.append(f"### `{st.table}`")
        lines.append("")
        if st.error:
            lines.append(f"Error: {st.error}")
            lines.append("")
            continue
        nominal = (
            f"{fmt_hz(st.expected_hz)} Hz" if st.expected_hz else "unknown"
        )
        lines.append(
            f"- Rows: **{st.row_count:,}** over **{st.duration_s/3600:.2f} h** "
            f"| expected {nominal} | median Δt **{st.median_dt_ms:.2f} ms** "
            f"({fmt_hz(1000/st.median_dt_ms if st.median_dt_ms else None)} Hz) "
            f"| mean rate {fmt_hz(st.mean_hz)} Hz | p99 Δt {st.p99_dt_ms:.1f} ms"
        )
        lines.append(
            f"- Gaps (>{st.gap_threshold_ms:.0f} ms): **{st.gap_count}** "
            f"({st.gap_pct:.3f}% of intervals), max **{st.max_gap_s:.1f} s**"
            + (f" | duplicate ts: {st.duplicate_ts}" if st.duplicate_ts else "")
        )
        if st.value_col:
            lines.append(
                f"- `{st.value_col}`: min **{st.vmin:.4g}**, max **{st.vmax:.4g}**, "
                f"σ **{st.vstd:.4g}**"
                + (
                    f", stuck consecutive **{st.stuck_pct:.1f}%**"
                    if st.stuck_pct is not None
                    else ""
                )
            )
        png = per_table_plots.get(st.table)
        if png:
            lines.append("")
            lines.append(f"![{st.table}]({plot_dir.name}/{png})")
        lines.append("")

    lines.append("## Session list")
    lines.append("")
    for p in sorted(db_files):
        try:
            sz = p.stat().st_size
        except OSError:
            sz = 0
        lines.append(f"- `{p.name}` ({sz/1e6:.1f} MB)")
    lines.append("")

    report_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--flights-dir",
        type=Path,
        default=Path.home() / "kingfisher" / "flights",
    )
    ap.add_argument("--days", type=float, default=5.0, help="include DBs modified within N days")
    ap.add_argument("--max-rows", type=int, default=500_000, help="max ts rows per table per DB")
    ap.add_argument(
        "--report",
        type=Path,
        default=Path("docs/reports/flight_sampling_report.md"),
    )
    ap.add_argument(
        "--plot-dir",
        type=Path,
        default=Path("docs/reports/flight_sampling_plots"),
    )
    args = ap.parse_args()

    cutoff = args.days * 86400
    now = Path(args.flights_dir).stat().st_mtime if args.flights_dir.exists() else 0
    import time

    now = time.time()
    db_files = sorted(
        p
        for p in args.flights_dir.glob("*.db")
        if (now - p.stat().st_mtime) <= cutoff
    )
    if not db_files:
        raise SystemExit(f"No .db files in {args.flights_dir} within {args.days} days")

    # Longest DB in window for plots.
    plot_db = max(db_files, key=lambda p: p.stat().st_size)

    all_stats: list[TableStats] = []
    plot_payload: dict[str, dict[str, Any]] = {}

    for db in db_files:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            for table in list_sensor_tables(conn):
                st, pdata = analyze_table(
                    conn,
                    db,
                    table,
                    max_rows=args.max_rows,
                    subsample_plot=8000,
                )
                all_stats.append(st)
                if db == plot_db and pdata:
                    plot_payload[table] = pdata
        finally:
            conn.close()

    args.plot_dir.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)

    per_table_plots: dict[str, str] = {}
    for table, pdata in sorted(plot_payload.items()):
        st = next(s for s in all_stats if s.db_path == str(plot_db) and s.table == table)
        per_table_plots[table] = plot_sensor(table, pdata, st, args.plot_dir)

    agg = aggregate_by_table(all_stats)
    write_report(
        report_path=args.report,
        plot_dir=args.plot_dir,
        db_files=db_files,
        all_stats=all_stats,
        agg=agg,
        plot_db=plot_db,
        per_table_plots=per_table_plots,
    )

    # Sidecar JSON for tooling.
    summary_path = args.report.with_suffix(".json")
    summary_path.write_text(json.dumps(agg, indent=2), encoding="utf-8")
    print(f"Wrote {args.report}")
    print(f"Plots in {args.plot_dir} ({len(per_table_plots)} images)")
    print(f"Summary JSON {summary_path}")


if __name__ == "__main__":
    main()
