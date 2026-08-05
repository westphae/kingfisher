#!/usr/bin/env python3
"""Kingfisher flight DB catalog + health analysis CLI.

Examples (from repo root; uses uv workspace — see docs/python.md):
  uv run --project analysis python scripts/analyze_flights.py catalog
  uv run --project analysis python scripts/analyze_flights.py health --flights-only
  uv run --project analysis python scripts/analyze_flights.py windows
  uv run --project analysis python scripts/analyze_flights.py report
  uv run --project analysis python scripts/analyze_flights.py cal-accel --json ~/kingfisher/calibration/cabin_imu_….json
  uv run --project analysis python scripts/analyze_flights.py all
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow running without install: repo root on sys.path
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from analysis.cal_accel import run as run_cal_accel
from analysis.catalog import load_catalog, scan_flights_dir, write_catalog
from analysis.health import analyze_health, write_health_report
from analysis.report import write_ledger
from analysis.noise import run_noise_study, write_sensor_noise_md
from analysis.windows import WindowParams, process_dbs


def default_flights_dir() -> Path:
    return Path.home() / "kingfisher" / "flights"


def default_cache_dir() -> Path:
    return Path.home() / "kingfisher" / "analysis-cache"


def default_ledger() -> Path:
    return _ROOT / "docs" / "analysis" / "ledger.md"


def cmd_catalog(args: argparse.Namespace) -> int:
    flights = Path(args.flights_dir)
    if not flights.is_dir():
        print(f"flights dir not found: {flights}", file=sys.stderr)
        return 1
    print(f"Scanning {flights} …")
    entries = scan_flights_dir(flights)
    out = Path(args.catalog)
    write_catalog(entries, out)
    by: dict[str, int] = {}
    for e in entries:
        by[e.class_] = by.get(e.class_, 0) + 1
    print(f"Wrote {out} ({len(entries)} sessions)")
    for k in sorted(by):
        print(f"  {k}: {by[k]}")
    return 0


def cmd_health(args: argparse.Namespace) -> int:
    catalog_path = Path(args.catalog)
    if not catalog_path.is_file():
        print(f"catalog missing; run catalog first: {catalog_path}", file=sys.stderr)
        return 1
    cat = load_catalog(catalog_path)
    sessions = cat.get("sessions", [])
    if args.file:
        want = {Path(args.file).name}
        sessions = [s for s in sessions if s["file"] in want]
    elif args.flights_only:
        sessions = [s for s in sessions if s.get("class") == "flight"]

    results = []
    for i, s in enumerate(sessions, 1):
        path = Path(s["path"])
        if not path.is_file():
            path = Path(args.flights_dir) / s["file"]
        print(f"[{i}/{len(sessions)}] health {path.name} …")
        results.append(analyze_health(path, max_rows=args.max_rows))

    out = Path(args.health)
    write_health_report(results, out)
    print(f"Wrote {out}")
    for r in results:
        print(f"  {r.file}: grade={r.grade} coverage={r.coverage} "
              f"airborne={r.airborne_min}m pod_gaps={r.aligned_pod_gaps_gt_1s}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    catalog_path = Path(args.catalog)
    if not catalog_path.is_file():
        print(f"catalog missing; run catalog first: {catalog_path}", file=sys.stderr)
        return 1
    cat = load_catalog(catalog_path)
    health = None
    hp = Path(args.health)
    if hp.is_file():
        health = json.loads(hp.read_text(encoding="utf-8"))
    ledger = Path(args.ledger)
    write_ledger(cat, ledger, health=health)
    print(f"Wrote {ledger}")
    return 0


def cmd_windows(args: argparse.Namespace) -> int:
    flights = Path(args.flights_dir)
    if not flights.is_dir():
        print(f"flights dir not found: {flights}", file=sys.stderr)
        return 1
    if args.file:
        paths = [Path(args.file)]
        if not paths[0].is_file():
            paths = [flights / Path(args.file).name]
        if not paths[0].is_file():
            print(f"DB not found: {args.file}", file=sys.stderr)
            return 1
    else:
        paths = sorted(flights.glob("*.db"))
    if not paths:
        print("no .db files to process", file=sys.stderr)
        return 1
    params = WindowParams(
        epoch_s=args.epoch_s,
        gs_flight_kt=args.gs_flight,
        gs_taxi_kt=args.gs_taxi,
        transient_max_s=args.transient_max_s,
    )
    out = Path(args.windows_dir)
    print(f"Labeling {len(paths)} sessions → {out}")
    results = process_dbs(
        paths,
        out,
        params=params,
        rebuild_all=not args.no_compact,
    )
    ok = sum(1 for r in results if not r.error)
    empty = sum(1 for r in results if r.error and "no timed samples" in (r.error or ""))
    hard = len(results) - ok - empty
    print(f"Done: {ok}/{len(results)} ok" + (f", {empty} empty/no IMU+GPS" if empty else ""))
    return 1 if hard else 0


def cmd_noise(args: argparse.Namespace) -> int:
    flights = Path(args.flights_dir)
    windows_dir = Path(args.windows_dir)
    out_dir = Path(args.noise_dir)
    if not (windows_dir / "segments_all.parquet").is_file():
        print(
            f"missing {windows_dir / 'segments_all.parquet'}; run `windows` first",
            file=sys.stderr,
        )
        return 1
    print(f"Noise study on stationary windows → {out_dir}")
    summary = run_noise_study(
        flights,
        windows_dir,
        out_dir,
        max_samples=args.max_samples,
        min_stationary_s=args.min_stationary_s,
    )
    md = Path(args.noise_md)
    write_sensor_noise_md(summary, md, out_dir)
    print(f"Wrote {out_dir / 'summary.json'}")
    print(f"Wrote {md}")
    print(
        f"sessions_ok={summary.get('n_sessions_ok')} "
        f"accel_|a|={summary.get('accel_mean_norm_median')} "
        f"scale_hint={summary.get('accel_scale_hint')}"
    )
    return 0


def cmd_cal_accel(args: argparse.Namespace) -> int:
    path = Path(args.json)
    if not path.is_file():
        print(f"cal JSON not found: {path}", file=sys.stderr)
        return 1
    plot_path = None
    if args.plot is not False:
        # --plot with optional path; default under analysis-cache
        plot_path = args.plot if args.plot is not None else (
            Path(args.noise_dir).parent / "cal_accel_norms.png"
        )
    run_cal_accel(path, plot_path=plot_path)
    return 0


def cmd_all(args: argparse.Namespace) -> int:
    rc = cmd_catalog(args)
    if rc:
        return rc
    # Force flights-only health for all
    args.flights_only = True
    args.file = None
    rc = cmd_health(args)
    if rc:
        return rc
    return cmd_report(args)


def main() -> int:
    cache = default_cache_dir()
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--flights-dir",
        type=Path,
        default=default_flights_dir(),
        help="directory of *.db sessions",
    )
    p.add_argument(
        "--catalog",
        type=Path,
        default=cache / "catalog.json",
        help="catalog JSON output/input",
    )
    p.add_argument(
        "--health",
        type=Path,
        default=cache / "health.json",
        help="health JSON output/input",
    )
    p.add_argument(
        "--ledger",
        type=Path,
        default=default_ledger(),
        help="markdown ledger path",
    )
    p.add_argument(
        "--max-rows",
        type=int,
        default=400_000,
        help="max timestamp rows sampled per table for health",
    )
    p.add_argument(
        "--windows-dir",
        type=Path,
        default=cache / "windows",
        help="motion-window parquet dataset root",
    )
    p.add_argument(
        "--noise-dir",
        type=Path,
        default=cache / "noise",
        help="noise study plots + summary.json",
    )
    p.add_argument(
        "--noise-md",
        type=Path,
        default=_ROOT / "docs" / "analysis" / "sensor_noise.md",
        help="noise report markdown path",
    )

    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("catalog", help="classify all DBs → catalog.json")
    h = sub.add_parser("health", help="sampling/gap health for sessions")
    h.add_argument("--flights-only", action="store_true", help="only class=flight")
    h.add_argument("--file", type=str, default=None, help="single DB basename or path")
    w = sub.add_parser(
        "windows",
        help="label stationary/taxi/flight/transient epochs → parquet",
    )
    w.add_argument("--file", type=str, default=None, help="single DB basename or path")
    w.add_argument("--epoch-s", type=float, default=1.0, help="epoch length (seconds)")
    w.add_argument("--gs-flight", type=float, default=40.0, help="flight if gs ≥ this (kt)")
    w.add_argument("--gs-taxi", type=float, default=5.0, help="taxi if gs ≥ this (kt)")
    w.add_argument(
        "--transient-max-s",
        type=float,
        default=20.0,
        help="documented bump length hint (IMU-only motion stays transient)",
    )
    w.add_argument(
        "--no-compact",
        action="store_true",
        help="skip rebuilding segments_all.parquet",
    )
    n = sub.add_parser(
        "noise",
        help="Phase 1: noise on stationary windows → sensor_noise.md",
    )
    n.add_argument(
        "--max-samples",
        type=int,
        default=80_000,
        help="max samples per sensor per session",
    )
    n.add_argument(
        "--min-stationary-s",
        type=float,
        default=120.0,
        help="skip sessions with less stationary time",
    )
    sub.add_parser("report", help="regenerate docs/analysis/ledger.md")
    ca = sub.add_parser(
        "cal-accel",
        help="Phase 2: re-fit / plot six-position accel cal JSON",
    )
    ca.add_argument(
        "--json",
        type=Path,
        required=True,
        help="~/kingfisher/calibration/cabin_imu_….json",
    )
    ca.add_argument(
        "--plot",
        type=Path,
        nargs="?",
        const=None,
        default=False,
        help="write before/after ‖a‖ PNG (default: <cache>/cal_accel_norms.png)",
    )
    sub.add_parser("all", help="catalog + health(flights) + report")

    args = p.parse_args()
    if args.cmd == "catalog":
        return cmd_catalog(args)
    if args.cmd == "health":
        return cmd_health(args)
    if args.cmd == "windows":
        return cmd_windows(args)
    if args.cmd == "noise":
        return cmd_noise(args)
    if args.cmd == "report":
        return cmd_report(args)
    if args.cmd == "cal-accel":
        return cmd_cal_accel(args)
    if args.cmd == "all":
        return cmd_all(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
