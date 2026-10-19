#!/usr/bin/env python3
"""Check a KiCad netlist against the rev 0.3 pin contract.

This table is independent of build_sch.py. Pin numbers are the datasheet
numbers (ICM-45686 DS-000577, BMP581, NEO-M9N, AP2112, RV-3028-C7).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path


def parse_nets(text: str) -> dict[str, set[tuple[str, str]]]:
    nets: dict[str, set[tuple[str, str]]] = {}
    current: str | None = None
    pattern = re.compile(
        r'\(net \(code "[^"]*"\) \(name "((?:\\.|[^"\\])*)"\)'
        r'|\(node \(ref "([^"]+)"\) \(pin "([^"]+)"\)'
    )
    for match in pattern.finditer(text):
        if match.group(1) is not None:
            current = match.group(1).replace(r"\"", '"')
            nets.setdefault(current, set())
        else:
            if current is None:
                raise SystemExit("node before net")
            nets[current].add((match.group(2), match.group(3)))
    return nets


def net_of(nets: dict[str, set[tuple[str, str]]], ref: str, pin: str) -> str:
    hits = [name for name, nodes in nets.items() if (ref, pin) in nodes]
    if len(hits) != 1:
        raise SystemExit(f"{ref} pin {pin} is on {hits or 'no net'}")
    return hits[0]


def same(nets: dict[str, set[tuple[str, str]]], pairs: list[tuple[str, str]]) -> str:
    names = {net_of(nets, ref, pin) for ref, pin in pairs}
    if len(names) != 1:
        raise SystemExit(f"{pairs} are on different nets: {names}")
    name = names.pop()
    if name.startswith("unconnected"):
        raise SystemExit(f"{pairs} are unconnected")
    return name


def different(nets: dict[str, set[tuple[str, str]]], a: tuple[str, str], b: tuple[str, str]) -> None:
    if net_of(nets, *a) == net_of(nets, *b):
        raise SystemExit(f"{a} and {b} were shorted")


def absent(nets: dict[str, set[tuple[str, str]]], ref: str, pin: str, forbidden: str) -> None:
    if net_of(nets, ref, pin) == forbidden:
        raise SystemExit(f"{ref} pin {pin} is on {forbidden}")


def main() -> None:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "kingfisher-hat.net")
    nets = parse_nets(path.read_text())
    gnd = same(nets, [("J1", "6"), ("U1", "6"), ("U1", "1"), ("U2", "3"), ("U4", "10"), ("U4", "12"), ("U4", "13"), ("U4", "24"), ("U5", "5"), ("BT1", "2"), ("BT2", "2")])
    v3 = same(nets, [("J1", "1"), ("J1", "17"), ("U1", "5"), ("U1", "8"), ("U1", "12"), ("U2", "1"), ("U2", "5"), ("U2", "6"), ("U2", "10"), ("U5", "7"), ("J3", "1"), ("R7", "1")])
    v5 = same(nets, [("J1", "2"), ("J1", "4"), ("U3", "1"), ("U3", "3"), ("C5", "1")])
    gps = same(nets, [("U3", "5"), ("U4", "23"), ("U4", "7"), ("C6", "1"), ("C8", "1"), ("D1", "2"), ("R3", "1")])
    vbckp = same(nets, [("U4", "22"), ("BT2", "1"), ("C9", "1"), ("R4", "2")])
    vbat = same(nets, [("BT1", "1"), ("U5", "6")])
    sda = same(nets, [("J1", "3"), ("U1", "14"), ("U2", "4"), ("U5", "4"), ("J3", "3"), ("R7", "2")])
    scl = same(nets, [("J1", "5"), ("U1", "13"), ("U2", "2"), ("U5", "3"), ("J3", "4"), ("R8", "2")])
    same(nets, [("J1", "11"), ("U1", "4")])
    same(nets, [("J1", "12"), ("U4", "3")])
    same(nets, [("J1", "31"), ("J3", "5")])
    same(nets, [("J1", "8"), ("R1", "1")])
    same(nets, [("R1", "2"), ("U4", "21")])
    same(nets, [("U4", "20"), ("R2", "1")])
    same(nets, [("R2", "2"), ("J1", "10")])
    same(nets, [("U4", "2"), ("R3", "2")])
    same(nets, [("D1", "1"), ("R4", "1")])
    rf = same(nets, [("U4", "11"), ("FB1", "1"), ("J2", "1")])
    bias = same(nets, [("FB1", "2"), ("R5", "2"), ("C10", "1")])
    vcc_rf = same(nets, [("U4", "9"), ("R5", "1")])
    same(nets, [("J2", "2"), ("J1", "6")])
    same(nets, [("J3", "2"), ("J1", "6")])
    different(nets, ("U4", "11"), ("U4", "9"))
    different(nets, ("D1", "1"), ("D1", "2"))
    different(nets, ("BT1", "1"), ("U4", "22"))
    different(nets, ("U1", "6"), ("U1", "5"))
    different(nets, ("J1", "17"), ("J1", "6"))
    # GPS I2C stays off the Pi bus. BMP581 INT is not strapped to ground.
    different(nets, ("U4", "18"), ("J1", "3"))
    different(nets, ("U2", "7"), ("J1", "6"))
    different(nets, ("U4", "14"), ("U4", "23"))
    for name in (gnd, v3, v5, gps, vbckp, vbat, sda, scl, rf, bias, vcc_rf):
        if name.startswith("unconnected"):
            raise SystemExit(name)
    # Primary cell has no charge path.
    for node in nets[vbat]:
        if node[0] in {"D1", "U3", "U4", "BT2"}:
            raise SystemExit(f"CR2032 net reaches {node}")
    print(f"rev 0.3 netlist OK ({len(nets)} nets)")


if __name__ == "__main__":
    main()
