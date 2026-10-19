"""Align flight-DB ``ts_ns`` using ``clock_offsets``.

``clock_offsets`` is a piecewise log of ``o = CLOCK_REALTIME − CLOCK_MONOTONIC``
(see ``docs/timestamps.md``). Each row is a snapshot at an event:

* ``ts_ns`` — wall clock at the event
* ``monotonic_ns`` — ``CLOCK_MONOTONIC`` at the same instant
* ``delta_ns`` — change in ``o`` since the previous row
* ``note`` — ``anchor`` (DB open), ``step``, or ``slew``

To put a sample on the **final** (synced) wall clock:

    ts_aligned = ts_ns + (o_final − o_at_sample)

``o_at_sample`` is the mapping in effect when the row was written. Streams that
followed the step (GPS, ``time.Now()`` derive, pod reconstruction) already
carry post-step ``ts_ns``; buffered IIO may keep stamping the pre-step
timeline. Which mapping applies is the one whose implied monotonic
``ts_ns − o`` falls inside the session's monotonic span
``[anchor.monotonic_ns, anchor.monotonic_ns + cap]``.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from analysis.db import list_tables, table_span

# Longest session we will treat as one recording when inverting ts→monotonic.
_MONO_CAP_NS = 72 * 3600 * 1_000_000_000
_MONO_SLACK_NS = 2_000_000_000  # 2 s before the anchor


@dataclass
class ClockMap:
    ts_ns: int
    monotonic_ns: int
    delta_ns: int
    note: str
    offset_ns: int  # o = realtime − monotonic at this row


@dataclass
class ClockModel:
    maps: list[ClockMap] = field(default_factory=list)

    @property
    def needs_align(self) -> bool:
        return len(self.maps) > 1

    @property
    def o_final(self) -> int:
        return self.maps[-1].offset_ns if self.maps else 0

    @property
    def mono_lo(self) -> int:
        if not self.maps:
            return 0
        return self.maps[0].monotonic_ns - _MONO_SLACK_NS

    @property
    def mono_hi(self) -> int:
        if not self.maps:
            return 0
        return self.maps[0].monotonic_ns + _MONO_CAP_NS

    def offset_at_sample(self, ts_ns: int) -> int:
        """``o`` in effect when ``ts_ns`` was written."""
        if not self.maps:
            return 0
        if len(self.maps) == 1:
            return self.maps[0].offset_ns
        hits = [
            m
            for m in self.maps
            if self.mono_lo <= (ts_ns - m.offset_ns) <= self.mono_hi
        ]
        if len(hits) == 1:
            return hits[0].offset_ns
        if not hits:
            return self.o_final
        chosen = self.maps[0]
        for m in self.maps:
            if ts_ns >= m.ts_ns:
                chosen = m
        return chosen.offset_ns

    def align_ts_ns(self, ts_ns: int) -> int:
        if not self.needs_align:
            return ts_ns
        return ts_ns + (self.o_final - self.offset_at_sample(ts_ns))

    def aligned_sql(self, ts_expr: str) -> str:
        """SQL equivalent of :meth:`align_ts_ns` for a ``ts_ns`` column expr."""
        if not self.needs_align:
            return ts_expr
        o_final = self.o_final
        lo, hi = self.mono_lo, self.mono_hi
        whens: list[str] = []
        for i, m in enumerate(self.maps):
            in_i = f"(({ts_expr}) - ({m.offset_ns}) BETWEEN {lo} AND {hi})"
            not_others = [
                f"NOT ((({ts_expr}) - ({n.offset_ns}) BETWEEN {lo} AND {hi}))"
                for j, n in enumerate(self.maps)
                if j != i
            ]
            cond = in_i if not not_others else f"({in_i} AND {' AND '.join(not_others)})"
            whens.append(f"WHEN {cond} THEN {m.offset_ns}")
        # Wall-time fallback when two mappings both fit (small steps).
        fb = str(o_final)
        for m in reversed(self.maps[:-1]):
            fb = f"(CASE WHEN {ts_expr} < {m.ts_ns} THEN {m.offset_ns} ELSE {fb} END)"
        o_chosen = f"(CASE {' '.join(whens)} ELSE {fb} END)"
        return f"(({ts_expr}) + ({o_final} - {o_chosen}))"


@dataclass
class ClockInfo:
    true_start_utc: str | None = None
    synced_at_utc: str | None = None
    startup_state: str | None = None
    startup_fallback: bool = False
    startup_reason: str | None = None
    rtc_held: str | None = None
    model: ClockModel = field(default_factory=ClockModel)
    issues: list[str] = field(default_factory=list)

    @property
    def maps(self) -> list[ClockMap]:
        return self.model.maps

    @property
    def needs_align(self) -> bool:
        return self.model.needs_align

    def to_json(self) -> dict[str, Any]:
        maps = [
            {
                "ts_ns": m.ts_ns,
                "monotonic_ns": m.monotonic_ns,
                "delta_ns": m.delta_ns,
                "delta_s": round(m.delta_ns / 1e9, 3),
                "note": m.note,
                "offset_ns": m.offset_ns,
                "utc": _ns_to_utc(m.ts_ns),
            }
            for m in self.model.maps
        ]
        steps = [m for m in maps if m["note"] != "anchor" and m["delta_ns"]]
        return {
            "true_start_utc": self.true_start_utc,
            "synced_at_utc": self.synced_at_utc,
            "startup_state": self.startup_state,
            "startup_fallback": self.startup_fallback,
            "startup_reason": self.startup_reason,
            "rtc_held": self.rtc_held,
            "clock_offsets": maps,
            "steps": steps,
            "o_final_ns": self.model.o_final if self.model.maps else None,
            "issues": self.issues,
            "needs_align": self.needs_align,
        }


def _ns_to_utc(ts_ns: int | None) -> str | None:
    if ts_ns is None:
        return None
    return datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _meta_map(conn: sqlite3.Connection) -> dict[str, str]:
    if "metadata" not in list_tables(conn):
        return {}
    try:
        return {
            str(k): str(v) for k, v in conn.execute("SELECT key, value FROM metadata")
        }
    except sqlite3.Error:
        return {}


def load_clock(conn: sqlite3.Connection) -> ClockModel:
    if "clock_offsets" not in list_tables(conn):
        return ClockModel()
    try:
        rows = conn.execute(
            "SELECT ts_ns, monotonic_ns, delta_ns, note FROM clock_offsets ORDER BY rowid"
        ).fetchall()
    except sqlite3.Error:
        return ClockModel()
    maps: list[ClockMap] = []
    for ts_ns, mono, delta, note in rows:
        if ts_ns is None or mono is None:
            continue
        ts_i, mono_i = int(ts_ns), int(mono)
        maps.append(
            ClockMap(
                ts_ns=ts_i,
                monotonic_ns=mono_i,
                delta_ns=int(delta or 0),
                note=str(note or ""),
                offset_ns=ts_i - mono_i,
            )
        )
    return ClockModel(maps=maps)


def load_steps(conn: sqlite3.Connection) -> list[ClockMap]:
    """Non-anchor ``clock_offsets`` rows (steps / slews)."""
    return [m for m in load_clock(conn).maps if m.note != "anchor" and m.delta_ns]


def align_ts_ns(ts_ns: int, model: ClockModel | list[ClockMap]) -> int:
    if isinstance(model, list):
        model = ClockModel(maps=model)
    return model.align_ts_ns(ts_ns)


def aligned_sql(ts_expr: str, model: ClockModel | list[ClockMap]) -> str:
    if isinstance(model, list):
        model = ClockModel(maps=model)
    return model.aligned_sql(ts_expr)


def inspect_clock(conn: sqlite3.Connection) -> ClockInfo:
    meta = _meta_map(conn)
    model = load_clock(conn)
    info = ClockInfo(
        true_start_utc=meta.get("clock_true_start_utc"),
        synced_at_utc=meta.get("clock_synced_at_utc"),
        startup_state=meta.get("clock_startup_state"),
        startup_fallback=meta.get("clock_startup_fallback", "").lower() == "true",
        startup_reason=meta.get("clock_startup_reason"),
        rtc_held=meta.get("clock_startup_rtc_held_time"),
        model=model,
    )
    big = [m for m in model.maps if abs(m.delta_ns) >= 1_000_000_000]
    if big:
        total_h = sum(m.delta_ns for m in big) / 1e9 / 3600.0
        info.issues.append(
            f"clock_offsets: wall−monotonic jumped {total_h:+.1f} h "
            f"({len(big)} step(s) ≥ 1 s); plot uses o_final − o_at_sample"
        )
    if info.startup_fallback:
        reason = info.startup_reason or "clock unsynced at DB open"
        info.issues.append(f"startup fallback: {reason}")
    if info.rtc_held == "no":
        info.issues.append("RTC did not hold time at boot")

    imu = [t for t in ("icm45686_accel", "icm45686_gyro") if t in list_tables(conn)]
    step_wall = max((m.ts_ns for m in big), default=None)
    if imu and step_wall is not None:
        for t in imu:
            _a, tmax, n = table_span(conn, t)
            if n and tmax is not None and tmax < step_wall:
                info.issues.append(
                    f"{t} timestamps never followed the clock step "
                    f"(last sample {_ns_to_utc(tmax)}; still on pre-step timeline)"
                )
    return info
