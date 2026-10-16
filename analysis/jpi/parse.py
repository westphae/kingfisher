"""JPI EDM-900/930 (and protocol-2 EDM-830) download parser.

The USB-stick / EZTrends file is an ASCII ``$U`` header, then big-endian
delta-compressed flights. Layout used here, checked against a real
protocol-2 download (EDM-830, the same record family the EDM-930 writes):

* Dollar lines are CRLF, NMEA XOR checksum of the bytes between ``$`` and ``*``.
* ``$D`` is one flight. Binary size is ``halfsize * 2`` bytes, sometimes one
  byte long because the firmware word-pads. The next flight id is accepted
  at the expected offset or one byte earlier.
* Each flight starts with a 29-byte header (14 big-endian words plus an
  additive checksum byte; the 29 bytes sum to 0 mod 256).
* Data records are ``mask, mask, repeat, control bytes, sign bytes, deltas,
  checksum``. Mask words are identical. Sign bytes are omitted for mask
  bits 6 and 7; those signs are copied from bits 0 and 3 (EGT/TIT high
  bytes share the low-byte sign). Checksum is additive mod 256 over the
  whole record including the checksum byte.
* A sensor's running total starts at 240 (0 for the high byte of a 16-bit
  pair, and for percent power). The first non-zero delta establishes the
  reading. A channel that never leaves that seed is absent, not zero.
* Fuel flow, fuel used, manifold pressure, and volts are stored in tenths
  when ``$F`` fuel unit is 0 (US gallons). GPS lat/lon in the header are
  signed int32 counts of 1/6000 degree. Later records add the same units.

CSV is the fallback (EZTrends "Export", or a spreadsheet save). Values are
already in display units. Blank cells are missing, not zero.

A file that is neither of those fails with :class:`ParseError`.
"""

from __future__ import annotations

import csv
import io
import struct
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

# High byte of a 16-bit pair. Seed 0, not 240. GPS high bytes are not in
# this set: they are the high half of a same-record delta, not a running total.
_HIGH_SEED_ZERO = frozenset({42, 48, 49, 50, 51, 52, 53, 54, 55})
_HP_INDEX = 30
_GPS_LAT_LO = 87
_GPS_LON_LO = 86
_GPS_LAT_HI = 82
_GPS_LON_HI = 81
_GPS_SCALE = 6000.0
_SEED = 240

# (stem, lo index, hi index or None, kind)
# kind: temp, oat, psi, scaled10, fuel, rpm, hp, amps, alt
_SENSORS: tuple[tuple[str, int, int | None, str], ...] = (
    ("egt1", 0, 48, "temp"),
    ("egt2", 1, 49, "temp"),
    ("egt3", 2, 50, "temp"),
    ("egt4", 3, 51, "temp"),
    ("egt5", 4, 52, "temp"),
    ("egt6", 5, 53, "temp"),
    ("tit1", 6, 54, "temp"),
    ("tit2", 7, 55, "temp"),
    ("cht1", 8, None, "temp"),
    ("cht2", 9, None, "temp"),
    ("cht3", 10, None, "temp"),
    ("cht4", 11, None, "temp"),
    ("cht5", 12, None, "temp"),
    ("cht6", 13, None, "temp"),
    ("oil_temp", 15, None, "temp"),
    ("oil_press", 17, None, "psi"),
    ("cdt", 18, None, "temp"),
    ("iat", 19, None, "temp"),
    ("volts", 20, None, "scaled10"),
    ("oat", 21, None, "oat"),
    ("fuel_used", 22, None, "fuel"),
    ("fuel_flow", 23, None, "fuel"),
    ("hp_pct", _HP_INDEX, None, "hp"),
    ("map", 40, None, "scaled10"),
    ("rpm", 41, 42, "rpm"),
    ("amps", 64, None, "amps"),
    ("alt", 83, None, "alt"),
)

_PREFERRED = (
    "rpm",
    "map_inhg",
    "fuel_flow_gph",
    "fuel_used_gal",
    "fuel_flow",
    "fuel_used",
    "egt1_f",
    "egt2_f",
    "egt3_f",
    "egt4_f",
    "egt5_f",
    "egt6_f",
    "egt1_c",
    "egt2_c",
    "egt3_c",
    "egt4_c",
    "egt5_c",
    "egt6_c",
    "cht1_f",
    "cht2_f",
    "cht3_f",
    "cht4_f",
    "cht5_f",
    "cht6_f",
    "cht1_c",
    "cht2_c",
    "cht3_c",
    "cht4_c",
    "cht5_c",
    "cht6_c",
    "tit1_f",
    "tit2_f",
    "tit1_c",
    "tit2_c",
    "oil_temp_f",
    "oil_temp_c",
    "oil_press_psi",
    "oat_f",
    "oat_c",
    "cdt_f",
    "cdt_c",
    "iat_f",
    "iat_c",
    "volts",
    "amps",
    "hp_pct",
    "alt_ft",
    "lat_deg",
    "lon_deg",
)


class ParseError(Exception):
    """The file is not a JPI download this tool knows how to read."""


@dataclass
class Sample:
    """One monitor row. ``t`` is the JPI clock, naive, read as UTC for alignment."""

    t: datetime
    values: dict[str, float | None]


@dataclass
class Flight:
    flight_id: int
    interval_s: int
    samples: list[Sample]
    channels: list[str]
    warnings: list[str] = field(default_factory=list)
    temp_unit: str = "F"
    oat_unit: str = "F"

    @property
    def start(self) -> datetime | None:
        return self.samples[0].t if self.samples else None

    @property
    def end(self) -> datetime | None:
        return self.samples[-1].t if self.samples else None


@dataclass
class Download:
    source_name: str
    kind: str  # "jpi" or "csv"
    model: int | None
    tail: str
    flights: list[Flight]
    warnings: list[str] = field(default_factory=list)
    engine_temp_unit: str = "F"
    oat_temp_unit: str = "F"
    fuel_unit: int = 0


def parse_file(path: Path | str) -> Download:
    p = Path(path)
    data = p.read_bytes()
    return parse_bytes(data, p.name)


def parse_bytes(data: bytes, source_name: str = "download") -> Download:
    if not data:
        raise ParseError(_unrecognized(source_name))
    window = data[:4096]
    if b"$U" in window or _looks_like_dollar(window):
        return _parse_jpi(data, source_name)
    if _looks_like_text(data[:4096]):
        return _parse_csv(data, source_name)
    raise ParseError(_unrecognized(source_name))


def _unrecognized(name: str) -> str:
    return (
        f"unrecognized JPI file {name!r}: expected a USB/EZTrends download "
        "whose header starts with $U, or a CSV export with a header containing "
        "RPM, MAP, EGT, or fuel flow. "
        "See analysis/testdata/jpi/README.md"
    )


def _looks_like_dollar(window: bytes) -> bool:
    stripped = window.lstrip(b"\x00\r\n\t ")
    return stripped.startswith(b"$")


def _looks_like_text(window: bytes) -> bool:
    if not window:
        return False
    sample = window[:1024]
    if b"\x00" in sample:
        return False
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _xor_checksum(body_between_dollar_and_star: bytes) -> int:
    acc = 0
    for b in body_between_dollar_and_star:
        acc ^= b
    return acc


def _iter_dollar_lines(data: bytes, start: int):
    pos = start
    n = len(data)
    while pos < n:
        while pos < n and data[pos] == 0:
            pos += 1
        if pos >= n or data[pos] != ord("$"):
            break
        body = bytearray()
        while pos < n and data[pos] != ord("*"):
            if data[pos] != 0:
                body.append(data[pos])
            pos += 1
        if pos >= n or data[pos] != ord("*"):
            raise ParseError("JPI header line has no NMEA checksum")
        pos += 1
        if pos + 2 > n:
            raise ParseError("JPI header checksum is truncated")
        try:
            got = int(data[pos : pos + 2].decode("ascii"), 16)
        except ValueError as exc:
            raise ParseError("JPI header checksum is not hex") from exc
        pos += 2
        if pos < n and data[pos] == 13:
            pos += 1
        if pos < n and data[pos] == 10:
            pos += 1
        raw = bytes(body)
        if not raw.startswith(b"$"):
            raise ParseError("JPI header line does not start with $")
        expect = _xor_checksum(raw[1:])
        if expect != got:
            raise ParseError(
                f"JPI header checksum mismatch on {raw[:12]!r}: "
                f"expected {expect:02X}, file has {got:02X}"
            )
        yield raw, pos


def _fields(line: bytes) -> list[str]:
    text = line.decode("latin1")
    # drop the leading $X
    comma = text.find(",")
    rest = text[comma + 1 :] if comma >= 0 else ""
    return [part.strip() for part in rest.split(",")]


def _parse_jpi(data: bytes, source_name: str) -> Download:
    origin = data.find(b"$U")
    if origin < 0:
        raise ParseError(_unrecognized(source_name))
    tail = ""
    model: int | None = None
    fuel_unit = 0
    engine_unit = "F"
    oat_unit = "F"
    protocol: int | None = None
    directory: list[tuple[int, int]] = []
    data_start: int | None = None
    try:
        for line, pos in _iter_dollar_lines(data, origin):
            tag = line[:2]
            fields = _fields(line)
            if tag == b"$U":
                tail = ",".join(fields).strip()
            elif tag == b"$F" and fields:
                fuel_unit = _to_int(fields[0], 0)
            elif tag == b"$C" and fields:
                model = _to_int(fields[0], 0)
                # Spec field 3 is the engine-unit mask, field 5 the OAT mask.
                if len(fields) > 2 and (_to_int(fields[2], 0) & 0x1000):
                    engine_unit = "F"
                elif len(fields) > 2:
                    engine_unit = "C"
                if len(fields) > 4 and (_to_int(fields[4], 0) & 0x2000):
                    oat_unit = "F"
                elif len(fields) > 4:
                    oat_unit = "C"
            elif tag == b"$P" and fields:
                protocol = _to_int(fields[0], 0)
            elif tag == b"$D" and len(fields) >= 2:
                directory.append((_to_int(fields[0], 0), _to_int(fields[1], 0) * 2))
            elif tag == b"$L":
                data_start = pos
                break
    except ParseError:
        raise
    except Exception as exc:
        raise ParseError(f"could not read JPI header in {source_name}: {exc}") from exc

    if data_start is None:
        raise ParseError(
            f"{source_name} has a $U header but no $L end-of-header; "
            "this is not a complete USB download"
        )
    if not directory:
        raise ParseError(f"{source_name} has no $D flight records")
    if protocol not in (None, 2) and (model is None or model < 900):
        raise ParseError(
            f"{source_name} is JPI protocol {protocol}, model {model}. "
            "This tool reads protocol 2 (EDM-830/900/930). "
            "It will not guess at a legacy EDM-700 download."
        )
    if model is not None and model < 800 and protocol != 2:
        raise ParseError(
            f"{source_name} looks like EDM model {model} without protocol 2. "
            "Refusing to guess at the legacy record size."
        )

    additive = protocol == 2 or (model is not None and model >= 900) or protocol is None
    if not additive:
        raise ParseError(
            f"{source_name}: checksum mode is not the protocol-2 additive sum. "
            "Refusing to guess."
        )

    warnings: list[str] = []
    flights: list[Flight] = []
    pos = data_start
    for index, (flight_id, declared) in enumerate(directory):
        pos = _align_flight_start(data, pos, flight_id)
        if index + 1 < len(directory):
            next_id = directory[index + 1][0]
            # Declared length is word-padded. Find the next id at declared or declared-1.
            next_at = _find_next(data, pos + max(declared - 1, 29), next_id, declared + 2)
            end = next_at if next_at is not None else min(len(data), pos + declared)
        else:
            end = _flight_end(data, pos, declared)
        try:
            flight = _parse_flight(
                data[pos:end],
                flight_id,
                engine_unit,
                oat_unit,
                fuel_unit,
            )
        except ParseError as exc:
            warnings.append(f"flight {flight_id}: {exc}")
            pos = end
            continue
        flights.append(flight)
        warnings.extend(f"flight {flight_id}: {w}" for w in flight.warnings)
        pos = end

    if not any(f.samples for f in flights):
        raise ParseError(
            f"{source_name} contained {len(directory)} flight mark(s) but no "
            "decodable samples. The download may be a different JPI generation."
        )
    return Download(
        source_name=source_name,
        kind="jpi",
        model=model,
        tail=tail,
        flights=flights,
        warnings=warnings,
        engine_temp_unit=engine_unit,
        oat_temp_unit=oat_unit,
        fuel_unit=fuel_unit,
    )


def _to_int(text: str, default: int) -> int:
    try:
        return int(text, 10)
    except (TypeError, ValueError):
        return default


def _align_flight_start(data: bytes, pos: int, flight_id: int) -> int:
    for delta in (0, -1, 1):
        at = pos + delta
        if at < 0 or at + 2 > len(data):
            continue
        if struct.unpack_from(">H", data, at)[0] == flight_id:
            return at
    raise ParseError(
        f"flight {flight_id} header not found at byte {pos} (±1). "
        "The directory and the binary section disagree."
    )


def _find_next(data: bytes, earliest: int, flight_id: int, span: int) -> int | None:
    last = min(len(data) - 2, earliest + span)
    for at in range(max(0, earliest), last + 1):
        if struct.unpack_from(">H", data, at)[0] == flight_id:
            return at
    return None


def _flight_end(data: bytes, pos: int, declared: int) -> int:
    hard = min(len(data), pos + declared)
    marker = data.find(b"$E,", pos, hard + 8)
    if marker != -1 and marker >= pos + 29:
        return marker
    return hard


def _unpack_clock(date_w: int, time_w: int) -> datetime:
    day = date_w & 0x1F
    month = (date_w >> 5) & 0x0F
    year = ((date_w >> 9) & 0x7F) + 2000
    second = (time_w & 0x1F) * 2
    minute = (time_w >> 5) & 0x3F
    hour = (time_w >> 11) & 0x1F
    return datetime(year, month, day, hour, minute, second)


def _parse_flight(
    blob: bytes,
    flight_id: int,
    engine_unit: str,
    oat_unit: str,
    fuel_unit: int,
) -> Flight:
    if len(blob) < 29:
        raise ParseError(f"flight record is {len(blob)} bytes, need 29")
    if sum(blob[:29]) & 0xFF:
        raise ParseError("flight header checksum failed (expected sum mod 256 == 0)")
    words = struct.unpack(">14H", blob[:28])
    got_id = words[0]
    if got_id != flight_id:
        raise ParseError(f"header flight id {got_id} != directory {flight_id}")
    interval = words[11]
    if interval <= 0 or interval > 60:
        raise ParseError(f"implausible record interval {interval}s")
    try:
        clock = _unpack_clock(words[12], words[13])
    except ValueError as exc:
        raise ParseError(f"flight header date is not a real calendar day ({exc})") from exc
    lat_raw = struct.unpack_from(">i", blob, 12)[0]
    lon_raw = struct.unpack_from(">i", blob, 16)[0]
    header_fuel = blob[20]
    if header_fuel in (0, 1, 2, 3):
        fuel_unit = header_fuel

    warnings: list[str] = []
    acc: list[int | None] = [None] * 128
    gps_lat_units = 0
    gps_lon_units = 0
    gps_moved = False
    names = _name_table(engine_unit, oat_unit, fuel_unit)
    samples: list[Sample] = []
    pos = 29
    resyncs = 0
    t = clock
    while pos + 6 <= len(blob):
        rec = _try_record(blob, pos)
        if rec is None:
            found = _resync(blob, pos)
            if found is None or resyncs >= 5:
                warnings.append(
                    f"stopped at byte {pos} after {len(samples)} samples "
                    "(checksum failed and no later record was valid)"
                )
                break
            warnings.append(
                f"resync +{found - pos} bytes at sample {len(samples)} (byte {pos})"
            )
            resyncs += 1
            pos = found
            continue
        repeat, deltas, next_pos = rec
        if repeat:
            # Non-zero repeat is the download's gap mark. The new sample is
            # delayed by repeat*interval on top of the one interval that
            # follows every sample. No filler rows are invented.
            t += timedelta(seconds=repeat * interval)
        _apply(acc, deltas)
        gps_lat_units, gps_lon_units, moved = _apply_gps(
            gps_lat_units, gps_lon_units, deltas
        )
        gps_moved = gps_moved or moved
        values = _sample_values(acc, names, fuel_unit)
        lat, lon = _gps_degrees(lat_raw, lon_raw, gps_lat_units, gps_lon_units)
        if lat is not None:
            values["lat_deg"] = lat
            values["lon_deg"] = lon
        samples.append(Sample(t=t, values=values))
        t += timedelta(seconds=interval)
        pos = next_pos

    channels = _channels_present(samples)
    if not gps_moved and "lat_deg" in channels:
        warnings.append(
            "GPS lat/lon is constant across this flight (header position only); "
            "alignment will not treat it as a track"
        )
    # Drop keys that never took a value so absent channels are absent.
    for sample in samples:
        sample.values = {k: sample.values.get(k) for k in channels}
    return Flight(
        flight_id=flight_id,
        interval_s=interval,
        samples=samples,
        channels=channels,
        warnings=warnings,
        temp_unit=engine_unit,
        oat_unit=oat_unit,
    )


def _try_record(blob: bytes, pos: int) -> tuple[int, dict[int, tuple[int, int]], int] | None:
    """Return (repeat, {index: (raw_byte, sign)}, next_pos) or None."""
    if pos + 6 > len(blob):
        return None
    mask1 = struct.unpack_from(">H", blob, pos)[0]
    mask2 = struct.unpack_from(">H", blob, pos + 2)[0]
    if mask1 != mask2 or mask1 == 0:
        return None
    p = pos + 4
    repeat = blob[p]
    p += 1
    control: dict[int, int] = {}
    for i in range(16):
        if mask1 & (1 << i):
            if p >= len(blob):
                return None
            control[i] = blob[p]
            p += 1
    sign: dict[int, int] = {}
    for i in range(16):
        if mask1 & (1 << i) and i not in (6, 7):
            if p >= len(blob):
                return None
            sign[i] = blob[p]
            p += 1
    if 0 in sign:
        sign[6] = sign[0]
    if 3 in sign:
        sign[7] = sign[3]
    deltas: dict[int, tuple[int, int]] = {}
    for i, cbyte in control.items():
        sbyte = sign.get(i, 0)
        for bit in range(8):
            if cbyte & (1 << bit):
                if p >= len(blob):
                    return None
                raw = blob[p]
                p += 1
                deltas[i * 8 + bit] = (raw, (sbyte >> bit) & 1)
    if p >= len(blob):
        return None
    if sum(blob[pos : p + 1]) & 0xFF:
        return None
    return repeat, deltas, p + 1


def _resync(blob: bytes, pos: int) -> int | None:
    limit = min(len(blob) - 6, pos + 48)
    for at in range(pos + 1, limit + 1):
        first = _try_record(blob, at)
        if first is None:
            continue
        if _try_record(blob, first[2]) is not None:
            return at
    return None


def _apply(acc: list[int | None], deltas: dict[int, tuple[int, int]]) -> None:
    for index, (raw, sign) in deltas.items():
        if index in (_GPS_LAT_LO, _GPS_LON_LO, _GPS_LAT_HI, _GPS_LON_HI):
            continue
        delta = -raw if sign else raw
        if acc[index] is None:
            if delta == 0:
                continue
            if index in _HIGH_SEED_ZERO or index == _HP_INDEX:
                seed = 0
            else:
                seed = _SEED
            acc[index] = seed + delta
        else:
            acc[index] += delta


def _apply_gps(
    lat_units: int,
    lon_units: int,
    deltas: dict[int, tuple[int, int]],
) -> tuple[int, int, bool]:
    moved = False
    if _GPS_LAT_LO in deltas:
        raw, sign = deltas[_GPS_LAT_LO]
        if _GPS_LAT_HI in deltas:
            raw = (deltas[_GPS_LAT_HI][0] << 8) | raw
        delta = -raw if sign else raw
        if delta:
            lat_units += delta
            moved = True
    if _GPS_LON_LO in deltas:
        raw, sign = deltas[_GPS_LON_LO]
        if _GPS_LON_HI in deltas:
            raw = (deltas[_GPS_LON_HI][0] << 8) | raw
        delta = -raw if sign else raw
        if delta:
            lon_units += delta
            moved = True
    return lat_units, lon_units, moved


def _gps_degrees(
    lat_raw: int, lon_raw: int, lat_units: int, lon_units: int
) -> tuple[float | None, float | None]:
    if lat_raw == 0 and lon_raw == 0 and lat_units == 0 and lon_units == 0:
        return None, None
    lat = (lat_raw + lat_units) / _GPS_SCALE
    lon = (lon_raw + lon_units) / _GPS_SCALE
    if abs(lat) > 90 or abs(lon) > 180:
        return None, None
    return lat, lon


def _name_table(engine_unit: str, oat_unit: str, fuel_unit: int) -> dict[int, tuple[str, str]]:
    """Map sensor lo-index → (column name, kind). Hi index is looked up via _SENSORS."""
    out: dict[int, tuple[str, str]] = {}
    eu = engine_unit.lower()
    ou = oat_unit.lower()
    for stem, lo, _hi, kind in _SENSORS:
        if kind == "temp":
            name = f"{stem}_{eu}"
        elif kind == "oat":
            name = f"oat_{ou}"
        elif kind == "psi":
            name = "oil_press_psi"
        elif kind == "scaled10" and stem == "map":
            name = "map_inhg"
        elif kind == "scaled10":
            name = "volts"
        elif kind == "fuel":
            if fuel_unit == 0:
                name = "fuel_flow_gph" if stem == "fuel_flow" else "fuel_used_gal"
            else:
                name = "fuel_flow" if stem == "fuel_flow" else "fuel_used"
        elif kind == "rpm":
            name = "rpm"
        elif kind == "hp":
            name = "hp_pct"
        elif kind == "amps":
            name = "amps"
        elif kind == "alt":
            name = "alt_ft"
        else:
            continue
        out[lo] = (name, kind)
    return out


def _reading(acc: list[int | None], lo: int, hi: int | None) -> int | None:
    if acc[lo] is None:
        return None
    total = acc[lo]
    if hi is not None and acc[hi] is not None:
        total += acc[hi] << 8
    return total


def _sample_values(
    acc: list[int | None],
    names: dict[int, tuple[str, str]],
    fuel_unit: int,
) -> dict[str, float | None]:
    values: dict[str, float | None] = {}
    for stem, lo, hi, kind in _SENSORS:
        if lo not in names:
            continue
        raw = _reading(acc, lo, hi)
        if raw is None:
            continue
        name, kind = names[lo]
        if kind in ("scaled10", "fuel", "amps") and (kind != "fuel" or fuel_unit == 0):
            values[name] = raw / 10.0
        elif kind == "fuel":
            values[name] = float(raw)
        else:
            values[name] = float(raw)
    return values


def _channels_present(samples: list[Sample]) -> list[str]:
    seen: set[str] = set()
    for sample in samples:
        for key, val in sample.values.items():
            if val is not None:
                seen.add(key)
    order = {name: i for i, name in enumerate(_PREFERRED)}
    return sorted(seen, key=lambda name: (order.get(name, 1000), name))


# --- CSV ------------------------------------------------------------------

_CSV_ALIASES = {
    "rpm": "rpm",
    "rpmlo": "rpm",
    "map": "map_inhg",
    "ff": "fuel_flow_gph",
    "fuelflow": "fuel_flow_gph",
    "gph": "fuel_flow_gph",
    "usd": "fuel_used_gal",
    "fused": "fuel_used_gal",
    "fuelused": "fuel_used_gal",
    "oilp": "oil_press_psi",
    "oilpr": "oil_press_psi",
    "volt": "volts",
    "volts": "volts",
    "bat": "volts",
    "amps": "amps",
    "amps1": "amps",
    "hp": "hp_pct",
    "alt": "alt_ft",
    "altitude": "alt_ft",
    "lat": "lat_deg",
    "latitude": "lat_deg",
    "lon": "lon_deg",
    "long": "lon_deg",
    "longitude": "lon_deg",
}

_CSV_TEMP = {
    "egt1": "egt1",
    "egt2": "egt2",
    "egt3": "egt3",
    "egt4": "egt4",
    "egt5": "egt5",
    "egt6": "egt6",
    "cht1": "cht1",
    "cht2": "cht2",
    "cht3": "cht3",
    "cht4": "cht4",
    "cht5": "cht5",
    "cht6": "cht6",
    "tit": "tit1",
    "tit1": "tit1",
    "t1": "tit1",
    "tit2": "tit2",
    "t2": "tit2",
    "oilt": "oil_temp",
    "oiltemp": "oil_temp",
    "oiltemperature": "oil_temp",
    "cdt": "cdt",
    "iat": "iat",
    "oat": "oat",
}

_CSV_TIME = {"date", "time", "datetime", "timestamp", "jpi_time"}
_CSV_FLIGHT = {"flight", "flt", "flight_id", "flightid"}
_GAP_SPLIT_S = 300


def _parse_csv(data: bytes, source_name: str) -> Download:
    text = data.decode("utf-8-sig", errors="replace")
    # Skip a short preamble (EZTrends sometimes writes a title line) until a
    # comma row that names a known channel.
    lines = text.splitlines()
    header_at = None
    for i, line in enumerate(lines[:30]):
        cells = [c.strip() for c in next(csv.reader([line]))]
        if _csv_header_map(cells):
            header_at = i
            break
    if header_at is None:
        raise ParseError(_unrecognized(source_name))
    reader = csv.DictReader(io.StringIO("\n".join(lines[header_at:])))
    if not reader.fieldnames:
        raise ParseError(_unrecognized(source_name))
    mapping = _csv_header_map(list(reader.fieldnames))
    if not mapping:
        raise ParseError(_unrecognized(source_name))
    date_col = next((c for c in reader.fieldnames if _norm_header(c) == "date"), None)
    time_col = next((c for c in reader.fieldnames if _norm_header(c) == "time"), None)
    datetime_col = next(
        (
            c
            for c in reader.fieldnames
            if _norm_header(c) in {"datetime", "timestamp", "jpi_time"}
            or (_norm_header(c) == "date" and time_col is None)
        ),
        None,
    )
    flight_col = next((c for c in reader.fieldnames if _norm_header(c) in _CSV_FLIGHT), None)
    if datetime_col is None and not (date_col and time_col):
        raise ParseError(
            f"{source_name} CSV has engine columns but no date/time column. "
            "Need DATE, TIME, or a combined timestamp."
        )

    rows: list[tuple[datetime, dict[str, float | None], str | None]] = []
    for raw in reader:
        if raw is None:
            continue
        if all((v is None or str(v).strip() == "") for v in raw.values()):
            continue
        stamp = _csv_time(raw, datetime_col, date_col, time_col)
        if stamp is None:
            continue
        values: dict[str, float | None] = {}
        for src, dst in mapping.items():
            values[dst] = _csv_float(raw.get(src))
        flight_key = None
        if flight_col and raw.get(flight_col) not in (None, ""):
            flight_key = str(raw.get(flight_col)).strip()
        rows.append((stamp, values, flight_key))
    if not rows:
        raise ParseError(f"{source_name} CSV header was recognized but no data rows parsed")

    groups: list[list[tuple[datetime, dict[str, float | None]]]] = []
    current: list[tuple[datetime, dict[str, float | None]]] = []
    prev_t: datetime | None = None
    prev_key: str | None = None
    for stamp, values, key in rows:
        split = False
        if key is not None and prev_key is not None and key != prev_key:
            split = True
        if prev_t is not None and (stamp - prev_t).total_seconds() > _GAP_SPLIT_S:
            split = True
        if split and current:
            groups.append(current)
            current = []
        current.append((stamp, values))
        prev_t = stamp
        prev_key = key
    if current:
        groups.append(current)

    flights: list[Flight] = []
    for i, group in enumerate(groups, start=1):
        samples = [Sample(t=stamp, values=values) for stamp, values in group]
        channels = _channels_present(samples)
        for sample in samples:
            sample.values = {k: sample.values.get(k) for k in channels}
        interval = 6
        if len(group) >= 2:
            dt = (group[1][0] - group[0][0]).total_seconds()
            if 1 <= dt <= 60:
                interval = int(round(dt))
        flights.append(
            Flight(
                flight_id=i,
                interval_s=interval,
                samples=samples,
                channels=channels,
                temp_unit="F",
                oat_unit="F",
            )
        )
    return Download(
        source_name=source_name,
        kind="csv",
        model=None,
        tail="",
        flights=flights,
        warnings=[
            "CSV temperatures are assumed Fahrenheit (EGT/CHT/TIT/OAT/CDT/IAT "
            "columns without a unit suffix). A binary $U download carries the unit flag."
        ],
        engine_temp_unit="F",
        oat_temp_unit="F",
        fuel_unit=0,
    )


def _norm_header(name: str | None) -> str:
    if name is None:
        return ""
    return "".join(ch for ch in name.strip().lower() if ch.isalnum())


def _csv_header_map(cells: list[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for cell in cells:
        if cell is None:
            continue
        norm = _norm_header(cell)
        # Allow EGT1_F / EGT1F already suffixed.
        unit = None
        stem = norm
        if stem.endswith("_f") or stem.endswith("f") and stem[:-1] in _CSV_TEMP:
            unit = "f"
            stem = stem[:-1].rstrip("_") if stem.endswith("_f") else stem[:-1]
        elif stem.endswith("_c") or (stem.endswith("c") and stem[:-1] in _CSV_TEMP):
            unit = "c"
            stem = stem[:-1].rstrip("_") if stem.endswith("_c") else stem[:-1]
        if stem in _CSV_TEMP:
            base = _CSV_TEMP[stem]
            suffix = unit or ("f" if base != "oat" else "f")
            if base == "oat":
                mapping[cell] = f"oat_{suffix}"
            else:
                mapping[cell] = f"{base}_{suffix}"
        elif stem in _CSV_ALIASES:
            mapping[cell] = _CSV_ALIASES[stem]
        elif norm in _CSV_ALIASES:
            mapping[cell] = _CSV_ALIASES[norm]
    # Require an engine identity column, not just a timestamp.
    useful = {"rpm", "map_inhg", "fuel_flow_gph", "egt1_f", "egt1_c", "tit1_f", "tit1_c"}
    if not any(v in useful or v.startswith("egt1") for v in mapping.values()):
        return {}
    return mapping


def _csv_time(
    raw: dict[str, str | None],
    datetime_col: str | None,
    date_col: str | None,
    time_col: str | None,
) -> datetime | None:
    if datetime_col and time_col and _norm_header(datetime_col) == "date":
        text = f"{(raw.get(datetime_col) or '').strip()} {(raw.get(time_col) or '').strip()}".strip()
    elif date_col and time_col and datetime_col is None:
        text = f"{(raw.get(date_col) or '').strip()} {(raw.get(time_col) or '').strip()}".strip()
    elif datetime_col:
        text = (raw.get(datetime_col) or "").strip()
        if time_col and _norm_header(datetime_col) == "date":
            text = f"{text} {(raw.get(time_col) or '').strip()}".strip()
    else:
        return None
    if not text:
        return None
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%y %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%m/%d/%Y %H:%M",
    ):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _csv_float(text: str | None) -> float | None:
    if text is None:
        return None
    s = str(text).strip()
    if s == "" or s.upper() in {"NA", "N/A", "NULL", "-"}:
        return None
    try:
        return float(s)
    except ValueError:
        return None
