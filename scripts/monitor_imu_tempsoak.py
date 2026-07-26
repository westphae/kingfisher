#!/usr/bin/env python3
"""Live monitor for ICM-45686 temperature-soak characterization runs.

Polls the active kingfisher flight DB (read-only, WAL-safe) and, every
--interval seconds, computes windowed means of die temperature, gyro bias,
and accel output. Appends one CSV row per poll, maintains a status.json
for external watchers, logs phase-change events, and prints a running
per-axis TCO (bias-vs-temperature slope) estimate once enough temperature
span has accumulated.

Phases: baseline -> cooling -> cold_stable -> warming -> warm_stable
(direction auto-detected, so the same invocation covers cold soak and the
subsequent room warm-up).

Stability = over the last --stable-minutes: temp span < --stable-span-c
AND |linear temp slope| < --stable-slope-c-min. This tolerates slow
fridge-compressor cycling while still catching the asymptote.

Usage:
  python3 scripts/monitor_imu_tempsoak.py --out ~/kingfisher/imu_tempcal/run_X
"""

import argparse
import csv
import glob
import json
import math
import os
import sqlite3
import sys
import time
from collections import deque
from datetime import datetime, timezone

RAD_TO_DEG = 57.29577951308232
G_MPS2 = 9.80665


def now_iso():
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")


def newest_db(flights_dir):
    dbs = glob.glob(os.path.join(flights_dir, "*.db"))
    return max(dbs, key=os.path.getmtime) if dbs else None


def window_means(db_path, window_s):
    """Return dict of windowed means from the gyro and accel tables, or None."""
    uri = f"file:{db_path}?mode=ro"
    con = sqlite3.connect(uri, uri=True, timeout=5.0)
    try:
        cutoff = (time.time() - window_s) * 1e9
        g = con.execute(
            "SELECT COUNT(*), AVG(temp_c), AVG(anglvel_x), AVG(anglvel_y),"
            " AVG(anglvel_z), MAX(ts_ns) FROM icm45686_gyro WHERE ts_ns > ?",
            (cutoff,),
        ).fetchone()
        a = con.execute(
            "SELECT COUNT(*), AVG(temp_c), AVG(accel_x), AVG(accel_y),"
            " AVG(accel_z) FROM icm45686_accel WHERE ts_ns > ?",
            (cutoff,),
        ).fetchone()
    finally:
        con.close()
    if not g or g[0] < 10 or not a or a[0] < 10:
        return None
    data_age = time.time() - g[5] / 1e9
    return {
        "n_gyro": g[0],
        "temp_c": (g[1] + a[1]) / 2.0,
        "bx_dps": g[2] * RAD_TO_DEG,
        "by_dps": g[3] * RAD_TO_DEG,
        "bz_dps": g[4] * RAD_TO_DEG,
        "ax_mps2": a[2],
        "ay_mps2": a[3],
        "az_mps2": a[4],
        "amag_mps2": math.sqrt(a[2] ** 2 + a[3] ** 2 + a[4] ** 2),
        "data_age_s": data_age,
    }


def soc_temp_c():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return int(f.read().strip()) / 1000.0
    except OSError:
        return float("nan")


def linfit(xs, ys):
    """Least-squares slope, intercept; None if degenerate."""
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx < 1e-9:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    slope = sxy / sxx
    return slope, my - slope * mx


def tco_report(history):
    """Fit each bias channel vs die temp over the whole run so far."""
    temps = [h["temp_c"] for h in history]
    if not temps or max(temps) - min(temps) < 3.0:
        return None
    out = {"dt_span_c": round(max(temps) - min(temps), 2)}
    for key, scale, unit in (
        ("bx_dps", 1.0, "dps_per_c"),
        ("by_dps", 1.0, "dps_per_c"),
        ("bz_dps", 1.0, "dps_per_c"),
        ("ax_mps2", 1000.0 / G_MPS2, "mg_per_c"),
        ("ay_mps2", 1000.0 / G_MPS2, "mg_per_c"),
        ("az_mps2", 1000.0 / G_MPS2, "mg_per_c"),
    ):
        fit = linfit(temps, [h[key] * scale for h in history])
        if fit:
            out[f"{key.split('_')[0]}_{unit}"] = round(fit[0], 5)
    return out


class PhaseTracker:
    def __init__(self, arm_delta_c, stable_minutes, stable_span_c,
                 stable_slope_c_min):
        self.arm_delta_c = arm_delta_c
        self.stable_minutes = stable_minutes
        self.stable_span_c = stable_span_c
        self.stable_slope_c_min = stable_slope_c_min
        self.t0 = None
        self.run_min = None
        self.run_max = None
        self.phase = "baseline"
        self.recent = deque()  # (wallclock_s, temp_c)

    def _window_pts(self):
        cutoff = time.time() - self.stable_minutes * 60
        return cutoff, [(t, c) for t, c in self.recent if t >= cutoff]

    def _stability(self):
        """Return (is_stable, span, slope_c_per_min) over the stable window."""
        cutoff, pts = self._window_pts()
        if len(pts) < 5 or pts[0][0] > cutoff + 0.2 * self.stable_minutes * 60:
            return False, None, None  # window not yet filled
        temps = [c for _, c in pts]
        span = max(temps) - min(temps)
        fit = linfit([t / 60.0 for t, _ in pts], temps)
        slope = fit[0] if fit else 0.0
        ok = span < self.stable_span_c and abs(slope) < self.stable_slope_c_min
        return ok, span, slope

    def update(self, temp_c):
        now = time.time()
        self.recent.append((now, temp_c))
        cutoff = now - self.stable_minutes * 60 - 120
        while self.recent and self.recent[0][0] < cutoff:
            self.recent.popleft()
        if self.t0 is None:
            self.t0 = temp_c
            self.run_min = self.run_max = temp_c
            self.ext_min = self.ext_max = temp_c
        self.run_min = min(self.run_min, temp_c)
        self.run_max = max(self.run_max, temp_c)

        stable, span, slope = self._stability()
        prev = self.phase
        # Plateaus arm against the run's global extremes; direction
        # reversals compare to the extreme since the current phase began.
        if self.phase == "baseline":
            if temp_c < self.t0 - 1.5:
                self.phase = "cooling"
            elif temp_c > self.t0 + 1.5:
                self.phase = "warming"
        elif self.phase == "cooling":
            if stable and temp_c < self.run_max - self.arm_delta_c:
                self.phase = "cold_stable"
            elif temp_c > self.ext_min + 1.5:
                self.phase = "warming"
        elif self.phase == "warming":
            if stable and temp_c > self.run_min + self.arm_delta_c:
                self.phase = "warm_stable"
            elif temp_c < self.ext_max - 1.5:
                self.phase = "cooling"
        elif self.phase in ("cold_stable", "warm_stable"):
            # exit thresholds lag a departure by the stability window and
            # sit above the entry span (1.5 vs 1.0) so enter/exit can't
            # both be satisfied by the same window contents
            win = [c for _, c in self._window_pts()[1]]
            if win and temp_c > min(win) + 1.5:
                self.phase = "warming"
            elif win and temp_c < max(win) - 1.5:
                self.phase = "cooling"
        if self.phase != prev:
            self.ext_min = self.ext_max = temp_c
        else:
            self.ext_min = min(self.ext_min, temp_c)
            self.ext_max = max(self.ext_max, temp_c)
        return prev, self.phase, span, slope


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--flights-dir",
                   default=os.path.expanduser("~/kingfisher/flights"))
    p.add_argument("--out", required=True, help="run output directory")
    p.add_argument("--interval", type=float, default=20.0)
    p.add_argument("--window", type=float, default=60.0,
                   help="averaging window seconds")
    p.add_argument("--arm-delta-c", type=float, default=5.0,
                   help="temp change from start required before a soak "
                        "plateau can be declared")
    p.add_argument("--stable-minutes", type=float, default=15.0)
    p.add_argument("--stable-span-c", type=float, default=1.0)
    p.add_argument("--stable-slope-c-min", type=float, default=0.03)
    args = p.parse_args()

    os.makedirs(args.out, exist_ok=True)
    csv_path = os.path.join(args.out, "soak.csv")
    status_path = os.path.join(args.out, "status.json")
    events_path = os.path.join(args.out, "events.log")

    tracker = PhaseTracker(args.arm_delta_c, args.stable_minutes,
                           args.stable_span_c, args.stable_slope_c_min)
    history = []

    fields = ["time", "unix_s", "phase", "temp_c", "soc_temp_c",
              "bx_dps", "by_dps", "bz_dps",
              "ax_mps2", "ay_mps2", "az_mps2", "amag_mps2",
              "n_gyro", "data_age_s", "db"]
    new_csv = not os.path.exists(csv_path)
    csv_f = open(csv_path, "a", newline="")
    writer = csv.DictWriter(csv_f, fieldnames=fields, extrasaction="ignore")
    if new_csv:
        writer.writeheader()

    def event(msg):
        line = f"{now_iso()} {msg}"
        print(line, flush=True)
        with open(events_path, "a") as f:
            f.write(line + "\n")

    event(f"monitor started pid={os.getpid()} interval={args.interval}s "
          f"window={args.window}s stable={args.stable_minutes}min/"
          f"<{args.stable_span_c}C span/<{args.stable_slope_c_min}C/min")

    while True:
        db = newest_db(args.flights_dir)
        m = window_means(db, args.window) if db else None
        if m is None or m["data_age_s"] > 30:
            event(f"WARNING no fresh IMU data (db={db}, "
                  f"age={m['data_age_s']:.0f}s)" if m else
                  f"WARNING no IMU rows in window (db={db})")
            time.sleep(args.interval)
            continue

        prev, phase, span, slope = tracker.update(m["temp_c"])
        m.update(time=now_iso(), unix_s=round(time.time(), 1), phase=phase,
                 soc_temp_c=soc_temp_c(), db=os.path.basename(db))
        history.append(m)
        writer.writerow({k: (round(v, 6) if isinstance(v, float) else v)
                         for k, v in m.items()})
        csv_f.flush()

        tco = tco_report(history)
        status = {
            "updated": m["time"],
            "phase": phase,
            "temp_c": round(m["temp_c"], 2),
            "soc_temp_c": round(m["soc_temp_c"], 1),
            "t0_c": round(tracker.t0, 2),
            "bias_dps": {"x": round(m["bx_dps"], 4),
                         "y": round(m["by_dps"], 4),
                         "z": round(m["bz_dps"], 4)},
            "accel_mps2": {"x": round(m["ax_mps2"], 4),
                           "y": round(m["ay_mps2"], 4),
                           "z": round(m["az_mps2"], 4),
                           "mag": round(m["amag_mps2"], 4)},
            "stable_window": {
                "span_c": round(span, 3) if span is not None else None,
                "slope_c_per_min": round(slope, 4) if slope is not None else None,
            },
            "tco_fit": tco,
            "points": len(history),
        }
        tmp = status_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(status, f, indent=1)
        os.replace(tmp, status_path)

        if phase != prev:
            event(f"PHASE {prev} -> {phase} at die={m['temp_c']:.2f}C "
                  f"bias=({m['bx_dps']:+.3f},{m['by_dps']:+.3f},"
                  f"{m['bz_dps']:+.3f})dps")

        print(f"{m['time']} {phase:12s} die={m['temp_c']:6.2f}C "
              f"soc={m['soc_temp_c']:5.1f}C "
              f"gyro=({m['bx_dps']:+.3f},{m['by_dps']:+.3f},"
              f"{m['bz_dps']:+.3f})dps |a|={m['amag_mps2']:.4f}"
              + (f" TCO(g)=({tco['bx_dps_per_c']:+.4f},"
                 f"{tco['by_dps_per_c']:+.4f},{tco['bz_dps_per_c']:+.4f})"
                 f"dps/C over {tco['dt_span_c']}C" if tco else ""),
              flush=True)
        time.sleep(args.interval)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
