#!/usr/bin/env python3
"""Kingfisher flight DB catalog + health analysis CLI.

Examples (from repo root; uses uv workspace — see docs/python.md):
  uv run --project analysis python scripts/analyze_flights.py catalog
  uv run --project analysis python scripts/analyze_flights.py health --flights-only
  uv run --project analysis python scripts/analyze_flights.py report
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

from analysis.catalog import load_catalog, scan_flights_dir, write_catalog
from analysis.health import analyze_health, write_health_report
from analysis.report import write_ledger


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

    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("catalog", help="classify all DBs → catalog.json")
    h = sub.add_parser("health", help="sampling/gap health for sessions")
    h.add_argument("--flights-only", action="store_true", help="only class=flight")
    h.add_argument("--file", type=str, default=None, help="single DB basename or path")
    sub.add_parser("report", help="regenerate docs/analysis/ledger.md")
    sub.add_parser("all", help="catalog + health(flights) + report")

    args = p.parse_args()
    if args.cmd == "catalog":
        return cmd_catalog(args)
    if args.cmd == "health":
        return cmd_health(args)
    if args.cmd == "report":
        return cmd_report(args)
    if args.cmd == "all":
        return cmd_all(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
