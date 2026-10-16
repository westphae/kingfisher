"""``analyze_flights.py jpi`` implementation.

Default is a dry-run report. ``--apply`` writes only after the alignment
passes the refusal threshold, ``--db`` names the flight database, and
``--flight`` names the JPI flight when the download contains more than one.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from analysis.jpi.align import DEFAULT_WINDOW_S, align_flight, suggest_flight
from analysis.jpi.parse import Download, Flight, ParseError, parse_file
from analysis.jpi.store import write_flight


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "jpi",
        help="align a JPI EDM-930 download into one flight DB (dry-run unless --apply)",
    )
    p.add_argument("--jpi", type=Path, required=True, help="USB .jpi/.dat download or CSV export")
    p.add_argument("--db", type=Path, default=None, help="Kingfisher flight SQLite to align against")
    p.add_argument(
        "--flight",
        type=int,
        default=None,
        help="JPI flight id ($D flight number, or 1-based CSV flight)",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="write jpi_edm930. Without this flag the command only reports.",
    )
    p.add_argument(
        "--window-s",
        type=float,
        default=DEFAULT_WINDOW_S,
        help="half-width of the clock search, seconds (default ±900)",
    )


def cmd_jpi(args: argparse.Namespace) -> int:
    path = Path(args.jpi)
    if not path.is_file():
        print(f"JPI file not found: {path}", file=sys.stderr)
        return 2
    try:
        download = parse_file(path)
    except ParseError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"could not read {path}: {exc}", file=sys.stderr)
        return 2

    _print_download(download)
    for warning in download.warnings:
        print(f"warning: {warning}")

    if args.db is None:
        print("no --db given; parsed only, nothing to align")
        return 0
    db = Path(args.db)
    if not db.is_file():
        print(f"flight db not found: {db}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(db)
    try:
        flight, suggest_only = _select_flight(download, conn, args)
        if flight is None:
            return 2
        if suggest_only and args.apply:
            print(
                f"download has {len(download.flights)} flights; "
                f"--apply needs --flight {flight.flight_id} (that id is only a suggestion)",
                file=sys.stderr,
            )
            return 2
        result = align_flight(flight, conn, window_s=args.window_s)
        print(f"flight {flight.flight_id}")
        print(result.summary())
        if not result.ok:
            print("not written", file=sys.stderr)
            return 1
        if not args.apply:
            print("dry-run: not written. Pass --apply to replace jpi_edm930 in this DB.")
            return 0
        if suggest_only:
            return 2
        n = write_flight(conn, flight, result, download.source_name)
        print(f"wrote {n} rows to jpi_edm930 in {db}")
        return 0
    finally:
        conn.close()


def _select_flight(
    download: Download, conn: sqlite3.Connection, args: argparse.Namespace
) -> tuple[Flight | None, bool]:
    """Return (flight, suggest_only). suggest_only means --flight was not passed."""
    flights = [f for f in download.flights if f.samples]
    if not flights:
        print("no decodable flights", file=sys.stderr)
        return None, False
    if args.flight is not None:
        match = [f for f in flights if f.flight_id == args.flight]
        if not match:
            ids = ", ".join(str(f.flight_id) for f in flights)
            print(f"no JPI flight {args.flight}. Ids in this file: {ids}", file=sys.stderr)
            return None, False
        return match[0], False
    if len(flights) == 1:
        return flights[0], False
    suggestion = suggest_flight(flights, conn, args.window_s)
    if suggestion is None:
        ids = ", ".join(str(f.flight_id) for f in flights)
        print(
            "several JPI flights and none overlap this DB within the search window. "
            f"Pass --flight. Ids: {ids}",
            file=sys.stderr,
        )
        return None, False
    print(
        f"suggestion: flight {suggestion.flight_id} "
        f"({suggestion.start} .. {suggestion.end}, {len(suggestion.samples)} samples). "
        "Pass --flight to select it."
    )
    return suggestion, True


def _print_download(download: Download) -> None:
    model = download.model if download.model is not None else "csv"
    print(
        f"source: {download.source_name} kind={download.kind} model={model} "
        f"tail={download.tail or '-'} "
        f"temp={download.engine_temp_unit} oat={download.oat_temp_unit} "
        f"flights={len(download.flights)}"
    )
    for flight in download.flights:
        if not flight.samples:
            print(f"  flight {flight.flight_id}: empty")
            continue
        ch = ", ".join(flight.channels) if flight.channels else "(no channels)"
        print(
            f"  flight {flight.flight_id}: {flight.start:%Y-%m-%d %H:%M:%S} "
            f"interval {flight.interval_s}s samples {len(flight.samples)} [{ch}]"
        )
