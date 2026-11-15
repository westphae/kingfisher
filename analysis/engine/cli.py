"""``analyze_flights.py engine`` — one flight, or every flight."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from analysis.engine.format import format_review, format_summary
from analysis.engine.limits import load_limits
from analysis.engine.run import review_all, review_db, review_jpi_flight
from analysis.engine.summary import summarize
from analysis.jpi.duplicates import flag_sessions
from analysis.jpi.parse import ParseError, parse_file


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "engine",
        help="AFMS engine review for one flight DB or every flight",
    )
    p.add_argument("--db", type=Path, default=None, help="one Kingfisher flight SQLite")
    p.add_argument("--all", action="store_true", help="every flight DB, plus raw JPI flights that are not merged")
    p.add_argument("--jpi", type=Path, default=None, help="review a raw EDM-930 download (no Kingfisher DB)")
    p.add_argument(
        "--jpi-flight",
        type=int,
        default=None,
        help="which flight in --jpi (default: every flight in the file)",
    )
    p.add_argument("--jpi-dir", type=Path, default=None, help="with --all, raw U*.JPI not already merged")
    p.add_argument("--limits", type=Path, default=None, help="JSON overrides (default ~/kingfisher/engine/limits.json)")
    p.add_argument("--cache-dir", type=Path, default=None, help="default <analysis-cache>/engine")
    p.add_argument("--json", action="store_true", help="print the review JSON instead of the scorecard")


def cmd_engine(args: argparse.Namespace) -> int:
    limits = load_limits(args.limits)
    cache = args.cache_dir
    if cache is None and getattr(args, "catalog", None):
        cache = Path(args.catalog).parent / "engine"
    if args.db is None and not args.all and args.jpi is None:
        print("pass --db, --all, or --jpi", file=sys.stderr)
        return 2
    if args.db is not None:
        path = Path(args.db)
        if not path.is_file():
            print(f"flight db not found: {path}", file=sys.stderr)
            return 2
        review = review_db(path, limits, cache)
        _emit(review, args.json)
        return 0
    if args.jpi is not None:
        path = Path(args.jpi)
        if not path.is_file():
            print(f"JPI file not found: {path}", file=sys.stderr)
            return 2
        try:
            download = parse_file(path)
        except ParseError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        flights = [f for f in download.flights if f.samples]
        if args.jpi_flight is not None:
            flights = [f for f in flights if f.flight_id == args.jpi_flight]
            if not flights:
                print(f"no flight {args.jpi_flight} in {path.name}", file=sys.stderr)
                return 2
        for flight in flights:
            review = review_jpi_flight(flight, f"{path.name}#flight{flight.flight_id}", limits)
            _emit(review, args.json)
        return 0
    flights_dir = Path(args.flights_dir)
    jpi_dir = args.jpi_dir
    if jpi_dir is None:
        default_raw = Path.home() / "kingfisher" / "engine" / "raw"
        jpi_dir = default_raw if default_raw.is_dir() else None
    reviews = review_all(flights_dir, limits, cache_dir=cache, jpi_dir=jpi_dir)
    flags = flag_sessions(flights_dir) if flights_dir.is_dir() else []
    summary = summarize(reviews, flags)
    if cache is not None:
        cache.mkdir(parents=True, exist_ok=True)
        (cache / "summary.json").write_text(
            json.dumps(summary, indent=2, allow_nan=False),
            encoding="utf-8",
        )
        print(f"wrote {cache / 'summary.json'}", file=sys.stderr)
    if args.json:
        print(json.dumps(summary, indent=2, allow_nan=False))
    else:
        print(format_summary(summary), end="")
    return 0


def _emit(review: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(review, indent=2, allow_nan=False))
    else:
        print(format_review(review), end="")
