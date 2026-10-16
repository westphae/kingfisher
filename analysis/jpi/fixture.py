"""Build a small protocol-2 ``.JPI`` download for tests.

The encoder is the inverse of :mod:`analysis.jpi.parse` for the subset the
tests cover (one or two flights, repeat count, additive checksums). It is
not a general writer.
"""

from __future__ import annotations

import struct
from datetime import datetime


def _nmea(body_after_dollar: str) -> str:
    acc = 0
    for ch in body_after_dollar:
        acc ^= ord(ch)
    return f"${body_after_dollar}*{acc:02X}\r\n"


def _pack_date(when: datetime) -> int:
    year2 = when.year - 2000
    return (year2 << 9) | (when.month << 5) | when.day


def _pack_time(when: datetime) -> int:
    return (when.hour << 11) | (when.minute << 5) | (when.second // 2)


def encode_record(changes: dict[int, int], repeat: int = 0) -> bytes:
    """One data record.

    ``changes`` maps a field index to a signed delta that fits in one byte
    (-255..255). GPS deltas use the same one-byte form (1/6000 degree).
    """
    mask = 0
    control = [0] * 16
    sign = [0] * 16
    payloads: list[tuple[int, int]] = []
    for index, delta in changes.items():
        if not -255 <= delta <= 255:
            raise ValueError(f"delta at {index} does not fit in a byte")
        group, bit = divmod(index, 8)
        mask |= 1 << group
        control[group] |= 1 << bit
        signbit = 1 if delta < 0 else 0
        if group not in (6, 7):
            sign[group] |= signbit << bit
        payloads.append((index, abs(delta) & 0xFF))
    if mask == 0:
        raise ValueError("a data record needs at least one delta")
    payloads.sort()
    out = bytearray()
    out += struct.pack(">HHB", mask, mask, repeat & 0xFF)
    for group in range(16):
        if mask & (1 << group):
            out.append(control[group])
    for group in range(16):
        if mask & (1 << group) and group not in (6, 7):
            out.append(sign[group])
    for _index, raw in payloads:
        out.append(raw)
    out.append((-sum(out)) & 0xFF)
    return bytes(out)


def encode_flight_header(
    flight_id: int,
    when: datetime,
    interval_s: int,
    *,
    lat_raw: int = 0,
    lon_raw: int = 0,
    fuel_unit: int = 0,
    rated_hp: int = 285,
) -> bytes:
    cfg0 = 0
    cfg1 = 0
    # Enable bits are informational; the parser keys off deltas. Set the
    # ones a 6-cylinder turbo single would advertise so the header is realistic.
    # EGT1-6 bits 2-7 of cfg0, CHT1-5 bits 11-15, CHT6 bit 0 of cfg1.
    cfg0 |= 0b11111100  # bits 2-7 EGT
    cfg0 |= 0xF800  # CHT1-5
    cfg1 |= 0x0001  # CHT6
    cfg1 |= (1 << 5) | (1 << 6)  # TIT1 TIT2
    cfg1 |= (1 << 4) | (1 << 13)  # oil temp, oil press
    cfg1 |= (1 << 7) | (1 << 8) | (1 << 9)  # CDT IAT OAT
    cfg1 |= (1 << 10) | (1 << 11) | (1 << 14)  # RPM/HP, FF, MAP
    cfg0 |= 0x0001  # volts
    words = [
        flight_id & 0xFFFF,
        cfg0 & 0xFFFF,
        cfg1 & 0xFFFF,
        0,
        0x64E2,  # OAT mask with °F bit, mirrors a real $C field
        0,
    ]
    buf = bytearray()
    for word in words:
        buf += struct.pack(">H", word)
    buf += struct.pack(">i", lat_raw)
    buf += struct.pack(">i", lon_raw)
    buf.append(fuel_unit & 0xFF)
    buf.append(rated_hp & 0xFF)
    buf += struct.pack(">HHH", interval_s & 0xFFFF, _pack_date(when), _pack_time(when))
    buf.append((-sum(buf)) & 0xFF)
    if len(buf) != 29:
        raise RuntimeError(f"flight header is {len(buf)} bytes, expected 29")
    return bytes(buf)


def encode_download(
    flights: list[tuple[int, datetime, int, list[bytes], int, int]],
    *,
    model: int = 930,
    tail: str = "N1234B",
) -> bytes:
    """``flights`` entries are (id, start, interval_s, records, lat_raw, lon_raw)."""
    lines = [
        _nmea(f"U, {tail}"),
        _nmea("A, 160, 120, 500, 450, 60, 1650, 230, 90"),
        _nmea("F,0,74,0,2990,2990"),
        _nmea(
            f"T, {flights[0][1].month}, {flights[0][1].day}, {flights[0][1].year % 100}, "
            f"{flights[0][1].hour}, {flights[0][1].minute}, 0"
        ),
        # field3 bit 0x1000 = engine °F, field5 bit 0x2000 = OAT °F
        _nmea(f"C,{model},0,4096,0,8192,0,0,1,2"),
        _nmea("P, 2"),
        _nmea("H,0"),
    ]
    bodies: list[bytes] = []
    for flight_id, when, interval, records, lat_raw, lon_raw in flights:
        header = encode_flight_header(
            flight_id, when, interval, lat_raw=lat_raw, lon_raw=lon_raw
        )
        body = header + b"".join(records)
        bodies.append(body)
        lines.append(_nmea(f"D, {flight_id}, {(len(body) + 1) // 2}"))
    lines.append(_nmea("L, 0"))
    text = "".join(lines).encode("ascii")
    end = _nmea("E,4")
    trailer = _nmea("V,Created by EDM930 VER 1.00 BLD 001 BETA 0")
    return text + b"".join(bodies) + end.encode("ascii") + trailer.encode("ascii")
