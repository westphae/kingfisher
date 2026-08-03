"""Phase 1a/1b: sensor noise on stationary windows + parameter recommendations.

Uses ``windows/segments_all.parquet`` (label=stationary). Writes:

- ``~/kingfisher/analysis-cache/noise/summary.json``
- ``~/kingfisher/analysis-cache/noise/*.png``
- ``docs/analysis/sensor_noise.md`` (regenerated)
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from analysis.db import connect_ro, list_tables, table_columns

G0 = 9.80665

# (table, columns, unit label for report)
STREAMS: list[tuple[str, tuple[str, ...], str]] = [
    ("icm45686_accel", ("accel_x", "accel_y", "accel_z"), "m/s²"),
    ("icm45686_gyro", ("anglvel_x", "anglvel_y", "anglvel_z"), "rad/s"),
    ("bmp581", ("static_pressure_pa", "pressure_pa"), "Pa"),
    ("mmc5983", ("mag_x_ut", "mag_y_ut", "mag_z_ut"), "µT"),
]


@dataclass
class ChannelNoise:
    table: str
    channel: str
    unit: str
    n: int
    mean: float
    std: float
    std_1s: float | None  # std of 1-second means
    median_dt_ms: float | None
    observed_hz: float | None
    noise_density: float | None  # std * sqrt(dt) white-noise approx
    configured_hz: float | None
    # extras for vectors
    mean_norm: float | None = None
    std_norm: float | None = None


@dataclass
class SessionNoise:
    session_id: str
    stationary_s: float
    sampled_s: float
    channels: list[ChannelNoise] = field(default_factory=list)
    attrs: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


def _latest_attr(conn: sqlite3.Connection, device: str, attr: str) -> str | None:
    if "sensor_attrs" not in list_tables(conn):
        return None
    row = conn.execute(
        """
        SELECT value FROM sensor_attrs
        WHERE device=? AND attr=? AND (channel='' OR channel IS NULL)
        ORDER BY ts_ns DESC LIMIT 1
        """,
        (device, attr),
    ).fetchone()
    return str(row[0]) if row else None


def _pick_col(have: list[str], candidates: tuple[str, ...]) -> str | None:
    for c in candidates:
        if c in have:
            return c
    return None


def _load_table_windowed(
    conn: sqlite3.Connection,
    table: str,
    cols: list[str],
    windows: list[tuple[int, int]],
    *,
    max_samples: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Load (ts_ns, values NxK) from time windows, capped at max_samples."""
    if not windows or table not in list_tables(conn) or not cols:
        return np.array([], dtype=np.int64), np.empty((0, len(cols)))
    sel = ", ".join(f'"{c}"' for c in cols)
    parts_ts: list[np.ndarray] = []
    parts_v: list[np.ndarray] = []
    n = 0
    for t0, t1 in windows:
        rows = conn.execute(
            f'SELECT ts_ns, {sel} FROM "{table}" WHERE ts_ns >= ? AND ts_ns < ? '
            f"ORDER BY ts_ns",
            (t0, t1),
        ).fetchall()
        if not rows:
            continue
        parts_ts.append(np.array([r[0] for r in rows], dtype=np.int64))
        parts_v.append(np.array([r[1:] for r in rows], dtype=np.float64))
        n += len(rows)
        if n >= max_samples:
            break
    if not parts_ts:
        return np.array([], dtype=np.int64), np.empty((0, len(cols)))
    ts = np.concatenate(parts_ts)
    vals = np.vstack(parts_v)
    if len(ts) > max_samples:
        idx = np.linspace(0, len(ts) - 1, max_samples, dtype=int)
        ts = ts[idx]
        vals = vals[idx]
    return ts, vals


def _std_of_1s_means(ts: np.ndarray, vals: np.ndarray) -> float | None:
    if len(ts) < 20:
        return None
    epoch = ts // 1_000_000_000
    # group
    order = np.argsort(epoch)
    epoch = epoch[order]
    vals = vals[order]
    means = []
    i = 0
    while i < len(epoch):
        j = i
        e = epoch[i]
        while j < len(epoch) and epoch[j] == e:
            j += 1
        if j - i >= 2:
            means.append(vals[i:j].mean())
        i = j
    if len(means) < 5:
        return None
    return float(np.std(means))


def _allan_adev_at_tau(x: np.ndarray, dt: float, tau_s: float) -> float | None:
    """Simple overlapping Allan deviation at one tau (seconds)."""
    if dt <= 0 or len(x) < 10:
        return None
    m = int(round(tau_s / dt))
    if m < 1 or len(x) < 2 * m + 1:
        return None
    # cluster averages
    n = len(x) // m
    if n < 3:
        return None
    y = x[: n * m].reshape(n, m).mean(axis=1)
    d = np.diff(y)
    if len(d) < 2:
        return None
    return float(np.sqrt(0.5 * np.mean(d**2)))


def analyze_session_noise(
    db_path: Path,
    stationary_windows: list[tuple[int, int]],
    *,
    max_samples: int = 80_000,
    min_stationary_s: float = 60.0,
) -> SessionNoise:
    sid = db_path.stem
    dur = sum((b - a) / 1e9 for a, b in stationary_windows)
    out = SessionNoise(session_id=sid, stationary_s=dur, sampled_s=0.0)
    if dur < min_stationary_s:
        out.error = f"stationary only {dur:.0f}s (< {min_stationary_s})"
        return out
    # Prefer longest windows first
    windows = sorted(stationary_windows, key=lambda w: w[1] - w[0], reverse=True)

    try:
        conn = connect_ro(db_path)
    except sqlite3.Error as e:
        out.error = str(e)
        return out

    try:
        # attrs snapshot
        for dev, key in (
            ("icm45686-accel", "sampling_frequency"),
            ("icm45686-gyro", "sampling_frequency"),
            ("icm45686-accel", "scale"),
            ("icm45686-gyro", "scale"),
            ("bmp581", "sampling_frequency"),
            ("mmc5983", "sampling_frequency"),
        ):
            v = _latest_attr(conn, dev, key)
            if v is not None:
                out.attrs[f"{dev}.{key}"] = v

        sampled_span = 0.0
        for table, candidates, unit in STREAMS:
            if table not in list_tables(conn):
                continue
            have = table_columns(conn, table)
            cols = [c for c in candidates if c in have]
            if table == "bmp581":
                c = _pick_col(have, candidates)
                cols = [c] if c else []
            if not cols:
                continue

            ts, stack = _load_table_windowed(
                conn, table, cols, windows, max_samples=max_samples
            )
            if len(ts) < 50:
                continue
            d = np.diff(ts) / 1e9
            d = d[d > 0]
            med_dt = float(np.median(d)) if len(d) else None
            hz = (1.0 / med_dt) if med_dt and med_dt > 0 else None
            sampled_span = max(sampled_span, (ts[-1] - ts[0]) / 1e9)

            device = {
                "icm45686_accel": "icm45686-accel",
                "icm45686_gyro": "icm45686-gyro",
            }.get(table, table)
            cfg_s = out.attrs.get(f"{device}.sampling_frequency")
            try:
                cfg_hz = float(cfg_s) if cfg_s else None
            except ValueError:
                cfg_hz = None

            norms = (
                np.linalg.norm(stack, axis=1) if stack.shape[1] > 1 else stack[:, 0]
            )

            for i, col in enumerate(cols):
                v = stack[:, i]
                # drop NaN rows for this channel
                ok = np.isfinite(v)
                if ok.sum() < 50:
                    continue
                v_ok = v[ok]
                ts_ok = ts[ok]
                std = float(np.std(v_ok))
                std_1s = _std_of_1s_means(ts_ok, v_ok)
                dens = (std * np.sqrt(med_dt)) if med_dt and med_dt > 0 else None
                out.channels.append(
                    ChannelNoise(
                        table=table,
                        channel=col,
                        unit=unit,
                        n=int(len(v_ok)),
                        mean=float(np.mean(v_ok)),
                        std=std,
                        std_1s=std_1s,
                        median_dt_ms=(med_dt * 1e3) if med_dt else None,
                        observed_hz=hz,
                        noise_density=float(dens) if dens is not None else None,
                        configured_hz=cfg_hz,
                        mean_norm=float(np.mean(norms[ok])) if i == 0 else None,
                        std_norm=float(np.std(norms[ok])) if i == 0 else None,
                    )
                )

            col0 = cols[0]
            v0 = stack[:, 0]
            ok0 = np.isfinite(v0)
            if med_dt and ok0.sum() > 50:
                for tau in (1.0, 10.0):
                    adev = _allan_adev_at_tau(v0[ok0], med_dt, tau)
                    if adev is not None:
                        out.attrs[f"allan_{table}_{col0}_tau{tau:g}"] = adev

        out.sampled_s = sampled_span
    except Exception as e:  # noqa: BLE001
        out.error = str(e)
    finally:
        conn.close()
    return out


def _session_windows_from_parquet(
    windows_dir: Path,
) -> dict[str, list[tuple[int, int]]]:
    import pandas as pd

    seg_path = windows_dir / "segments_all.parquet"
    if not seg_path.is_file():
        raise FileNotFoundError(
            f"{seg_path} missing; run: analyze_flights.py windows"
        )
    seg = pd.read_parquet(seg_path)
    st = seg[seg["label"] == "stationary"]
    by: dict[str, list[tuple[int, int]]] = {}
    for row in st.itertuples(index=False):
        by.setdefault(row.session_id, []).append(
            (int(row.t_start_ns), int(row.t_end_ns))
        )
    return by


def run_noise_study(
    flights_dir: Path,
    windows_dir: Path,
    out_dir: Path,
    *,
    max_samples: int = 80_000,
    min_stationary_s: float = 120.0,
) -> dict[str, Any]:
    by = _session_windows_from_parquet(windows_dir)
    results: list[SessionNoise] = []
    sessions = sorted(by.keys())
    for i, sid in enumerate(sessions, 1):
        db = flights_dir / f"{sid}.db"
        if not db.is_file():
            continue
        print(f"[{i}/{len(sessions)}] noise {sid} …")
        r = analyze_session_noise(
            db,
            by[sid],
            max_samples=max_samples,
            min_stationary_s=min_stationary_s,
        )
        results.append(r)
        if r.error:
            print(f"  skip: {r.error}")
        else:
            print(f"  channels={len(r.channels)} sampled≈{r.sampled_s:.0f}s")

    summary = _aggregate(results)
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    _plots(results, summary, out_dir)
    return summary


def _aggregate(results: list[SessionNoise]) -> dict[str, Any]:
    ok = [r for r in results if not r.error and r.channels]
    by_ch: dict[str, list[ChannelNoise]] = {}
    for r in ok:
        for ch in r.channels:
            key = f"{ch.table}.{ch.channel}"
            by_ch.setdefault(key, []).append(ch)

    agg = {}
    for key, lst in sorted(by_ch.items()):
        stds = [c.std for c in lst]
        std1 = [c.std_1s for c in lst if c.std_1s is not None]
        dens = [c.noise_density for c in lst if c.noise_density is not None]
        hz = [c.observed_hz for c in lst if c.observed_hz is not None]
        cfg = [c.configured_hz for c in lst if c.configured_hz is not None]
        norms = [c.mean_norm for c in lst if c.mean_norm is not None]
        agg[key] = {
            "n_sessions": len(lst),
            "std_median": float(np.median(stds)),
            "std_p25": float(np.percentile(stds, 25)),
            "std_p75": float(np.percentile(stds, 75)),
            "std_1s_median": float(np.median(std1)) if std1 else None,
            "noise_density_median": float(np.median(dens)) if dens else None,
            "observed_hz_median": float(np.median(hz)) if hz else None,
            "configured_hz_median": float(np.median(cfg)) if cfg else None,
            "unit": lst[0].unit,
            "mean_norm_median": float(np.median(norms)) if norms else None,
            "ratio_std_to_1s": (
                float(np.median(stds) / np.median(std1))
                if std1 and np.median(std1) > 0
                else None
            ),
        }

    # Accel |a| vs g0 from mean_norm on accel_x rows (we stash norm on first axis)
    accel_norms = []
    for r in ok:
        for ch in r.channels:
            if ch.table == "icm45686_accel" and ch.channel == "accel_x" and ch.mean_norm:
                accel_norms.append(ch.mean_norm)

    return {
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "n_sessions_ok": len(ok),
        "n_sessions_total": len(results),
        "channels": agg,
        "accel_mean_norm_median": float(np.median(accel_norms)) if accel_norms else None,
        "accel_scale_hint": (
            float(G0 / np.median(accel_norms))
            if accel_norms and np.median(accel_norms) > 0
            else None
        ),
        "sessions": [
            {
                "session_id": r.session_id,
                "stationary_s": r.stationary_s,
                "sampled_s": r.sampled_s,
                "error": r.error,
                "attrs": r.attrs,
                "channels": [asdict(c) for c in r.channels],
            }
            for r in results
        ],
    }


# (table prefix, short slug for filenames, plot title, native unit)
_PLOT_SENSORS: list[tuple[str, str, str, str]] = [
    ("icm45686_accel", "accel", "Accel (icm45686)", "m/s²"),
    ("icm45686_gyro", "gyro", "Gyro (icm45686)", "rad/s"),
    ("bmp581", "bmp581", "Baro (bmp581)", "Pa"),
    ("mmc5983", "mmc5983", "Mag (mmc5983)", "µT"),
]


def _plots(results: list[SessionNoise], summary: dict[str, Any], out_dir: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    ok = [r for r in results if not r.error]
    ch = summary.get("channels", {})
    colors = {
        "accel": "#2563eb",
        "gyro": "#c2410c",
        "bmp581": "#0f766e",
        "mmc5983": "#7c3aed",
    }

    for table, slug, title, unit in _PLOT_SENSORS:
        items = sorted(
            (k, v) for k, v in ch.items() if k.startswith(table + ".")
        )
        if not items:
            continue
        keys = [k.split(".", 1)[1] for k, _ in items]
        vals = [v["std_median"] for _, v in items]
        fig, ax = plt.subplots(figsize=(7, max(2.5, 0.55 * len(keys) + 1)), constrained_layout=True)
        ax.barh(keys, vals, color=colors.get(slug, "#2563eb"))
        ax.set_xlabel(f"median σ ({unit})")
        ax.set_title(f"Stationary-window noise — {title}")
        fig.savefig(out_dir / f"std_by_channel_{slug}.png", dpi=120)
        plt.close(fig)

        xs, ys, labs = [], [], []
        for k, v in items:
            if v.get("std_1s_median"):
                xs.append(v["std_median"])
                ys.append(v["std_1s_median"])
                labs.append(k.split(".", 1)[1])
        if not xs:
            continue
        fig, ax = plt.subplots(figsize=(5.5, 4.5), constrained_layout=True)
        ax.scatter(xs, ys, c=colors.get(slug, "#0f766e"), s=48)
        for x, y, lab in zip(xs, ys, labs):
            ax.annotate(lab, (x, y), fontsize=8, alpha=0.85, xytext=(4, 4), textcoords="offset points")
        lim = max(max(xs), max(ys)) * 1.15
        ax.plot([0, lim], [0, lim], "k--", lw=0.8, alpha=0.4)
        ax.set_xlim(0, lim)
        ax.set_ylim(0, lim)
        ax.set_xlabel(f"σ sample ({unit})")
        ax.set_ylabel(f"σ of 1 s means ({unit})")
        ax.set_title(f"1 s averaging — {title}")
        ax.set_aspect("equal", adjustable="box")
        fig.savefig(out_dir / f"std_vs_1s_{slug}.png", dpi=120)
        plt.close(fig)

    # Accel |a| distribution across sessions
    norms = []
    for r in ok:
        for c in r.channels:
            if c.table == "icm45686_accel" and c.channel == "accel_x" and c.mean_norm:
                norms.append(c.mean_norm)
    if norms:
        fig, ax = plt.subplots(figsize=(6, 3.5), constrained_layout=True)
        ax.hist(norms, bins=20, color="#7c3aed", alpha=0.85)
        ax.axvline(G0, color="#dc2626", ls="--", label=f"g0={G0}")
        ax.axvline(np.median(norms), color="#16a34a", ls=":", label="median")
        ax.set_xlabel("mean ‖a‖ (m/s²) in stationary windows")
        ax.legend(fontsize=8)
        ax.set_title("Accel magnitude (scale hint for Phase 2)")
        fig.savefig(out_dir / "accel_norm_hist.png", dpi=120)
        plt.close(fig)


def _extract_hand_notes(existing_md: str) -> str:
    begin = "<!-- HAND_NOTES_BEGIN -->"
    end = "<!-- HAND_NOTES_END -->"
    if begin not in existing_md or end not in existing_md:
        return ""
    return existing_md.split(begin, 1)[1].split(end, 1)[0].strip("\n")


def write_sensor_noise_md(summary: dict[str, Any], md_path: Path, plot_dir: Path) -> None:
    ch = summary.get("channels", {})
    notes_preserve = ""
    if md_path.is_file():
        notes_preserve = _extract_hand_notes(md_path.read_text(encoding="utf-8"))
    lines: list[str] = []
    lines.append("# Sensor noise & parameter study (Phase 1)")
    lines.append("")
    lines.append(
        f"_Generated {summary.get('generated_utc')} from stationary windows "
        f"({summary.get('n_sessions_ok')}/{summary.get('n_sessions_total')} sessions). "
        f"See [PLAN.md](PLAN.md)._"
    )
    lines.append("")
    lines.append("## Method (1a)")
    lines.append("")
    lines.append(
        "For each session with ≥120 s of `stationary` segments (from Phase 0 windows), "
        "load up to ~80k samples of accel/gyro/baro/mag from the longest still intervals. "
        "Report sample σ, σ of 1-second means, white-noise density approx "
        "`σ·√Δt`, observed median rate, and configured `sampling_frequency` from "
        "`sensor_attrs`."
    )
    lines.append("")
    lines.append("## Aggregate results")
    lines.append("")
    lines.append(
        "| Channel | Sessions | Obs Hz | Config Hz | σ (med) | σ 1s (med) | "
        "σ/σ₁ₛ | Density `σ√Δt` | Unit |"
    )
    lines.append(
        "|---------|----------|--------|-----------|---------|------------|"
        "-------|----------------|------|"
    )
    for key, v in sorted(ch.items()):
        r = v.get("ratio_std_to_1s")
        r_s = f"{r:.2f}" if r else "—"
        d = v.get("noise_density_median")
        d_s = f"{d:.4g}" if d is not None else "—"
        s1 = v.get("std_1s_median")
        s1_s = f"{s1:.4g}" if s1 is not None else "—"
        oh = v.get("observed_hz_median")
        oh_s = f"{oh:.2f}" if oh else "—"
        chz = v.get("configured_hz_median")
        chz_s = f"{chz:.0f}" if chz else "—"
        lines.append(
            f"| `{key}` | {v['n_sessions']} | {oh_s} | {chz_s} | "
            f"{v['std_median']:.4g} | {s1_s} | {r_s} | {d_s} | {v['unit']} |"
        )
    lines.append("")

    an = summary.get("accel_mean_norm_median")
    sc = summary.get("accel_scale_hint")
    if an and sc:
        lines.append("### Accel scale hint (feeds Phase 2)")
        lines.append("")
        lines.append(
            f"Median stationary ‖a‖ ≈ **{an:.4f}** m/s² vs (g₀={G0}). "
            f"Crude multiplicative scale (g₀/‖a‖) ≈ **{sc:.4f}** "
            f"(~{(sc - 1) * 100:+.1f}%). Confirm with 6-position cal before applying online."
        )
        lines.append("")

    lines.append("## Plots")
    lines.append("")
    lines.append(
        "Written under `~/kingfisher/analysis-cache/noise/` — one scale per sensor:"
    )
    lines.append("")
    for _table, slug, title, _unit in _PLOT_SENSORS:
        lines.append(
            f"- **{title}:** `std_by_channel_{slug}.png`, `std_vs_1s_{slug}.png`"
        )
    lines.append("- **Accel ‖a‖ hist:** `accel_norm_hist.png`")
    lines.append("")

    lines.append("## Findings (1a)")
    lines.append("")
    # Auto bullets from data
    imu_obs = ch.get("icm45686_accel.accel_x", {}).get("observed_hz_median")
    imu_cfg = ch.get("icm45686_accel.accel_x", {}).get("configured_hz_median")
    if imu_obs and imu_cfg:
        lines.append(
            f"- **ICM45686:** `sensor_attrs` reports ~{imu_cfg:.0f} Hz ODR but "
            f"stored/observed rate in DB is ~{imu_obs:.1f} Hz. Noise figures below "
            f"are for the **stored** stream (what AHRS sees unless you change "
            f"publish/store rate)."
        )
    bmp = ch.get("bmp581.static_pressure_pa") or ch.get("bmp581.pressure_pa")
    if bmp:
        lines.append(
            f"- **BMP581:** stationary σ(P) ≈ {bmp['std_median']:.2f} Pa "
            f"(~{bmp['std_median']/12:.1f} cm barometric at SL); "
            f"1 s means σ ≈ {bmp.get('std_1s_median') or float('nan'):.2f} Pa."
        )
    mag = ch.get("mmc5983.mag_x_ut")
    if mag:
        lines.append(
            f"- **MMC5983:** axis σ ≈ {mag['std_median']:.3f} µT at "
            f"~{mag.get('observed_hz_median') or 0:.0f} Hz stored."
        )
    gyro = ch.get("icm45686_gyro.anglvel_x")
    if gyro:
        lines.append(
            f"- **Gyro:** axis σ ≈ {gyro['std_median']:.5f} rad/s "
            f"({np.degrees(gyro['std_median'])*3600:.0f} °/h rms) at "
            f"~{gyro.get('observed_hz_median') or 0:.0f} Hz stored."
        )
    lines.append(
        "- Historical sessions share the **same** configured ODRs (accel/gyro 800, "
        "BMP/MMC 50) — little natural A/B for rate knobs in the archive; use "
        "σ(1 s means) as a proxy for heavier averaging / lower effective rate."
    )
    lines.append("")

    lines.append("## Parameter recommendations (1b)")
    lines.append("")
    lines.append("### Config profiles (proposed)")
    lines.append("")
    lines.append("| Profile | Intent | ICM ODR (chip) | Store/publish | BMP | MMC | Notes |")
    lines.append("|---------|--------|----------------|---------------|-----|-----|-------|")
    lines.append(
        "| `taxi_cal` | Mag taxi + ground cal | 100–200 Hz | 50–100 Hz | 20–50 Hz | 50–100 Hz | "
        "Lower full-scale if no saturation; prioritize mag bandwidth |"
    )
    lines.append(
        "| `cruise_log` | Long flights, storage | 200–400 Hz | **10–20 Hz** (current~10) | 25–50 Hz | 10–25 Hz | "
        "Keep chip ODR above store rate for HW filter; reduce σ via averaging |"
    )
    lines.append(
        "| `dynamics` | Maneuver / AHRS stress | 400–800 Hz | 50–100 Hz | 50 Hz | 50 Hz | "
        "Watch accel FS (±4 g today → rails at ~39 m/s²); consider ±8/16 g for aerobatics |"
    )
    lines.append("")
    lines.append("### Bench / flight sweeps still needed")
    lines.append("")
    lines.append(
        "Archive attrs do **not** vary. To finish 1b experimentally:"
    )
    lines.append("")
    lines.append(
        "1. **ICM45686:** On the bench (stationary), record 5–10 min at chip ODR "
        "100 / 200 / 400 / 800 Hz with the same store path; compare σ and "
        "σ(1 s). Optionally change accel FS ±2/±4/±8 g and confirm noise vs range."
    )
    lines.append(
        "2. **BMP581:** Sweep OSR/ODR if exposed; measure σ(P) and step response "
        "to a small height change (stairs) vs GPS/phone baro."
    )
    lines.append(
        "3. **MMC5983:** Sweep 10 / 50 / 100 Hz; measure σ(‖B‖) and "
        "heading jitter after a quick hard-iron fit while rotating the pod."
    )
    lines.append(
        "4. One **confirmation flight** on `cruise_log` after choosing settings; "
        "re-run `windows` + `noise` and compare to this baseline."
    )
    lines.append("")
    lines.append("### Practical takeaway for now")
    lines.append("")
    lines.append(
        "- For **calibration (Phase 2–3)**, prefer long `stationary` segments; "
        "1 s averaging already cuts high-frequency noise substantially when "
        "`σ/σ₁ₛ` ≫ 1."
    )
    lines.append(
        "- For **live AHRS**, either keep ~10 Hz store and accept its noise, or "
        "raise publish rate only if the filter can use the extra bandwidth; "
        "raising chip ODR without raising store rate mainly helps on-chip filtering."
    )
    lines.append(
        "- Apply **accel scale** (Phase 2) before interpreting ‖a‖−g as noise."
    )
    lines.append("")
    lines.append("## Next")
    lines.append("")
    lines.append(
        "Phase 2 — accel scale/bias from stationary windows + optional 6-position bench "
        "([PLAN.md](PLAN.md))."
    )
    lines.append("")
    lines.append("<!-- HAND_NOTES_BEGIN -->")
    if notes_preserve.strip():
        lines.append(notes_preserve.rstrip())
    else:
        lines.append(
            "_(Confirmation soaks and applied cruise-profile notes go here; "
            "preserved across `noise` regenerations.)_"
        )
    lines.append("<!-- HAND_NOTES_END -->")
    lines.append("")

    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("\n".join(lines), encoding="utf-8")
