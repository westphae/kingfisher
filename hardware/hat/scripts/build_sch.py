#!/usr/bin/env python3
"""Build kingfisher-hat.kicad_sch rev 0.3.

Nets are attached by drawing a wire from each symbol pin's connection point.
Pin coordinates are read from the embedded symbol, so a label cannot land on
the wrong pin the way rev 0.2 did. Do not replace this with guessed coordinates.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "kingfisher-hat.kicad_sch"
OURS = ROOT / "libs" / "Kingfisher.kicad_sym"
MIL = Decimal("0.0254")
STUB = 100  # mils, 2.54 mm. Longer stubs on a tight sheet run into the next part.

ROOT_UUID = "e63e39d7-6ac0-4ffd-8aa3-1841a4541b55"


def uid() -> str:
    return str(uuid.uuid4())


def mm_to_mil(text: str) -> int:
    mils = Decimal(text) / MIL
    rounded = mils.quantize(Decimal("1"))
    if abs(mils - rounded) > Decimal("0.05"):
        raise SystemExit(f"coordinate {text} is not on the 1 mil grid")
    return int(rounded)


def fmt(mils: int) -> str:
    text = format(Decimal(mils) * MIL, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def extract_symbol(path: Path | str, name: str) -> str:
    text = Path(path).read_text()
    key = f'(symbol "{name}"'
    i = text.find(key)
    if i < 0:
        raise SystemExit(f"missing symbol {name} in {path}")
    depth = 0
    for k in range(i, len(text)):
        if text[k] == "(":
            depth += 1
        elif text[k] == ")":
            depth -= 1
            if depth == 0:
                return text[i : k + 1]
    raise SystemExit(f"unbalanced symbol {name}")


def embed_as(lib_id: str, sym: str) -> str:
    """Parent symbol is Lib:Name. Unit children stay Name_0_1."""
    lines = [ln.rstrip() for ln in sym.lstrip("\n").splitlines()]

    def ntabs(ln: str) -> int:
        return len(ln) - len(ln.lstrip("\t"))

    depth = ntabs(lines[0])
    lines = [ln[depth:] if ln.startswith("\t" * depth) else ln for ln in lines]
    text = "\n".join(lines)
    match = re.match(r'\(symbol "([^"]+)"', text)
    if not match:
        raise SystemExit(f"bad symbol start: {text[:80]!r}")
    old = match.group(1)
    short = lib_id.split(":", 1)[-1]
    text = text.replace(f'(symbol "{old}"', f'(symbol "{lib_id}"', 1)
    if old != short:
        text = text.replace(f'(symbol "{old}_', f'(symbol "{short}_')
    return "\n".join(("\t\t" + line) if line.strip() else line for line in text.splitlines())


@dataclass
class Pin:
    num: str
    name: str
    x: int
    y: int
    ang: int
    etype: str
    hidden: bool


def parse_pins(sym: str) -> dict[str, Pin]:
    pins: dict[str, Pin] = {}
    i = 0
    while True:
        j = sym.find("(pin ", i)
        if j < 0:
            break
        depth = 0
        end = None
        for k in range(j, len(sym)):
            if sym[k] == "(":
                depth += 1
            elif sym[k] == ")":
                depth -= 1
                if depth == 0:
                    end = k + 1
                    break
        if end is None:
            raise SystemExit("unbalanced pin")
        block = sym[j:end]
        i = end
        at = re.search(r"\(at ([-\d.]+) ([-\d.]+) ([-\d.]+)\)", block)
        name = re.search(r'\(name "([^"]*)"', block)
        num = re.search(r'\(number "([^"]*)"', block)
        if not (at and name and num):
            continue
        etype = block.split()[1]
        pins[num.group(1)] = Pin(
            num=num.group(1),
            name=name.group(1),
            x=mm_to_mil(at.group(1)),
            y=mm_to_mil(at.group(2)),
            ang=int(Decimal(at.group(3))),
            etype=etype,
            hidden="(hide yes)" in block,
        )
    if not pins:
        raise SystemExit("symbol has no pins")
    return pins


def _point_on_segment(x: int, y: int, x1: int, y1: int, x2: int, y2: int) -> bool:
    if x1 == x2:
        if x != x1:
            return False
        lo, hi = sorted((y1, y2))
        return lo <= y <= hi
    if y1 == y2:
        if y != y1:
            return False
        lo, hi = sorted((x1, x2))
        return lo <= x <= hi
    return False


def outward(ang: int) -> tuple[int, int]:
    """Stub delta in schematic coordinates.

    Symbol pin Y grows upward. Schematic Y grows downward, so a symbol-space
    +Y step is a schematic -Y step. Pin angle points into the body.
    """
    go = (ang + 180) % 360
    if go == 0:
        return STUB, 0
    if go == 180:
        return -STUB, 0
    if go == 90:
        return 0, -STUB
    if go == 270:
        return 0, STUB
    raise SystemExit(f"pin angle {ang} is not a right angle")


@dataclass
class Part:
    ref: str
    lib_id: str
    value: str
    footprint: str
    x: int
    y: int
    dnp: bool = False
    pins: dict[str, Pin] = field(default_factory=dict)

    def tip(self, num: str) -> tuple[int, int, int]:
        pin = self.pins[num]
        # Symbol libraries are Y-up. Schematic sheets are Y-down.
        return self.x + pin.x, self.y - pin.y, pin.ang


def prop(name: str, value: str, x: int, y: int, hide: bool = False) -> str:
    hide_s = "\n\t\t\t\t(hide yes)" if hide else ""
    return (
        f'\t\t(property "{name}" "{value}"\n'
        f"\t\t\t(at {fmt(x)} {fmt(y)} 0)\n"
        "\t\t\t(effects\n"
        "\t\t\t\t(font\n"
        "\t\t\t\t\t(size 1.27 1.27)\n"
        f"\t\t\t\t){hide_s}\n"
        "\t\t\t)\n"
        "\t\t)"
    )


def instance(part: Part) -> str:
    pin_block = "\n".join(
        f'\t\t(pin "{num}"\n\t\t\t(uuid "{uid()}")\n\t\t)' for num in part.pins
    )
    return (
        "\t(symbol\n"
        f'\t\t(lib_id "{part.lib_id}")\n'
        f"\t\t(at {fmt(part.x)} {fmt(part.y)} 0)\n"
        "\t\t(unit 1)\n"
        "\t\t(exclude_from_sim no)\n"
        "\t\t(in_bom yes)\n"
        "\t\t(on_board yes)\n"
        f'\t\t(dnp {"yes" if part.dnp else "no"})\n'
        f'\t\t(uuid "{uid()}")\n'
        f"{prop('Reference', part.ref, part.x, part.y + 200)}\n"
        f"{prop('Value', part.value, part.x, part.y - 200)}\n"
        f"{prop('Footprint', part.footprint, part.x, part.y, hide=True)}\n"
        f"{prop('Datasheet', '~', part.x, part.y, hide=True)}\n"
        f"{prop('Description', '', part.x, part.y, hide=True)}\n"
        f"{pin_block}\n"
        "\t\t(instances\n"
        '\t\t\t(project "kingfisher-hat"\n'
        f'\t\t\t\t(path "/{ROOT_UUID}"\n'
        f'\t\t\t\t\t(reference "{part.ref}")\n'
        "\t\t\t\t\t(unit 1)\n"
        "\t\t\t\t)\n"
        "\t\t\t)\n"
        "\t\t)\n"
        "\t)"
    )


def label(name: str, x: int, y: int, rot: int) -> str:
    return (
        f'\t(label "{name}"\n'
        f"\t\t(at {fmt(x)} {fmt(y)} {rot})\n"
        "\t\t(effects\n"
        "\t\t\t(font\n"
        "\t\t\t\t(size 1.27 1.27)\n"
        "\t\t\t)\n"
        "\t\t\t(justify left)\n"
        "\t\t)\n"
        f'\t\t(uuid "{uid()}")\n'
        "\t)"
    )


def wire(x1: int, y1: int, x2: int, y2: int) -> str:
    return (
        "\t(wire\n"
        "\t\t(pts\n"
        f"\t\t\t(xy {fmt(x1)} {fmt(y1)})\n"
        f"\t\t\t(xy {fmt(x2)} {fmt(y2)})\n"
        "\t\t)\n"
        "\t\t(stroke\n"
        "\t\t\t(width 0)\n"
        "\t\t\t(type default)\n"
        "\t\t)\n"
        f'\t\t(uuid "{uid()}")\n'
        "\t)"
    )


def no_connect(x: int, y: int) -> str:
    return f'\t(no_connect\n\t\t(at {fmt(x)} {fmt(y)})\n\t\t(uuid "{uid()}")\n\t)'


def text(body: str, x: int, y: int, size: str = "1.27") -> str:
    return (
        f'\t(text "{body}"\n'
        "\t\t(exclude_from_sim no)\n"
        f"\t\t(at {fmt(x)} {fmt(y)} 0)\n"
        "\t\t(effects\n"
        "\t\t\t(font\n"
        f"\t\t\t\t(size {size} {size})\n"
        "\t\t\t)\n"
        "\t\t\t(justify left bottom)\n"
        "\t\t)\n"
        f'\t\t(uuid "{uid()}")\n'
        "\t)"
    )


def require(part: Part, num: str, name_part: str) -> None:
    pin = part.pins[num]
    if name_part not in pin.name:
        raise SystemExit(f"{part.ref} pin {num} is {pin.name}, expected {name_part}")


def main() -> None:
    ldo = extract_symbol("/usr/share/kicad/symbols/Regulator_Linear.kicad_sym", "AP2204K-1.5")
    ldo = ldo.replace("AP2204K-1.5", "AP2112K-3.3")
    sources: list[tuple[str, str]] = [
        ("Kingfisher:ICM-45686", extract_symbol(OURS, "ICM-45686")),
        ("Kingfisher:BMP581", extract_symbol(OURS, "BMP581")),
        ("Kingfisher:NEO-M9N", extract_symbol(OURS, "NEO-M9N")),
        ("Connector_Generic:Conn_02x20_Odd_Even", extract_symbol("/usr/share/kicad/symbols/Connector_Generic.kicad_sym", "Conn_02x20_Odd_Even")),
        ("Connector_Generic:Conn_01x05", extract_symbol("/usr/share/kicad/symbols/Connector_Generic.kicad_sym", "Conn_01x05")),
        ("Device:C", extract_symbol("/usr/share/kicad/symbols/Device.kicad_sym", "C")),
        ("Device:R", extract_symbol("/usr/share/kicad/symbols/Device.kicad_sym", "R")),
        ("Device:D_Schottky", extract_symbol("/usr/share/kicad/symbols/Device.kicad_sym", "D_Schottky")),
        ("Device:LED", extract_symbol("/usr/share/kicad/symbols/Device.kicad_sym", "LED")),
        ("Device:FerriteBead", extract_symbol("/usr/share/kicad/symbols/Device.kicad_sym", "FerriteBead")),
        ("Device:Battery_Cell", extract_symbol("/usr/share/kicad/symbols/Device.kicad_sym", "Battery_Cell")),
        ("Connector:Conn_Coaxial", extract_symbol("/usr/share/kicad/symbols/Connector.kicad_sym", "Conn_Coaxial")),
        ("Connector:TestPoint", extract_symbol("/usr/share/kicad/symbols/Connector.kicad_sym", "TestPoint")),
        ("Timer_RTC:RV-3028-C7", extract_symbol("/usr/share/kicad/symbols/Timer_RTC.kicad_sym", "RV-3028-C7")),
        ("Regulator_Linear:AP2112K-3.3", ldo),
        ("power:PWR_FLAG", extract_symbol("/usr/share/kicad/symbols/power.kicad_sym", "PWR_FLAG")),
    ]
    libraries = {lib_id: parse_pins(sym) for lib_id, sym in sources}
    embedded = [embed_as(lib_id, sym) for lib_id, sym in sources]

    def add(ref, lib_id, value, footprint, x, y, dnp=False) -> Part:
        # x and y are integer mils so pin tips stay on KiCad's grid.
        part = Part(ref, lib_id, value, footprint, x, y, dnp, libraries[lib_id])
        parts[ref] = part
        return part

    parts: dict[str, Part] = {}
    add("J1", "Connector_Generic:Conn_02x20_Odd_Even", "GPIO_40", "Connector_PinSocket_2.54mm:PinSocket_2x20_P2.54mm_Vertical", 1600, 10000)
    add("J3", "Connector_Generic:Conn_01x05", "X1200", "Connector_PinHeader_2.54mm:PinHeader_1x05_P2.54mm_Horizontal", 1600, 4800)
    add("U1", "Kingfisher:ICM-45686", "ICM-45686", "Kingfisher:ICM-45686", 6000, 13600)
    add("C1", "Device:C", "100nF", "Capacitor_SMD:C_0402_1005Metric", 8400, 14800)
    add("C2", "Device:C", "100nF", "Capacitor_SMD:C_0402_1005Metric", 8400, 14000)
    add("U2", "Kingfisher:BMP581", "BMP581", "Kingfisher:BMP581", 6000, 10000)
    add("C3", "Device:C", "100nF", "Capacitor_SMD:C_0402_1005Metric", 8400, 10800)
    add("C4", "Device:C", "100nF", "Capacitor_SMD:C_0402_1005Metric", 8400, 10000)
    add("U5", "Timer_RTC:RV-3028-C7", "RV-3028-C7", "Package_SON:MicroCrystal_C7_SON-8_1.5x3.2mm_P0.9mm", 6000, 6400)
    add("C11", "Device:C", "100nF", "Capacitor_SMD:C_0402_1005Metric", 8400, 6400)
    add("U3", "Regulator_Linear:AP2112K-3.3", "AP2112K-3.3", "Package_TO_SOT_SMD:SOT-23-5", 12000, 13600)
    add("C5", "Device:C", "1uF", "Capacitor_SMD:C_0603_1608Metric", 10000, 12000)
    add("C6", "Device:C", "1uF", "Capacitor_SMD:C_0603_1608Metric", 14000, 12000)
    add("C7", "Device:C", "100nF", "Capacitor_SMD:C_0402_1005Metric", 14000, 11200)
    add("U4", "Kingfisher:NEO-M9N", "NEO-M9N", "RF_GPS:ublox_NEO", 12800, 7600)
    add("C8", "Device:C", "100nF", "Capacitor_SMD:C_0402_1005Metric", 16800, 11200)
    add("C9", "Device:C", "1uF", "Capacitor_SMD:C_0603_1608Metric", 16800, 10000)
    add("R1", "Device:R", "33", "Resistor_SMD:R_0603_1608Metric", 9200, 5600)
    add("R2", "Device:R", "33", "Resistor_SMD:R_0603_1608Metric", 9200, 4400)
    add("R3", "Device:R", "10k", "Resistor_SMD:R_0402_1005Metric", 16800, 8400)
    add("R4", "Device:R", "1k", "Resistor_SMD:R_0603_1608Metric", 16800, 5600)
    add("R5", "Device:R", "33", "Resistor_SMD:R_0402_1005Metric", 19200, 6800)
    add("FB1", "Device:FerriteBead", "120R", "Inductor_SMD:L_0402_1005Metric", 19200, 8000)
    add("C10", "Device:C", "47pF", "Capacitor_SMD:C_0402_1005Metric", 19200, 5600)
    add("D1", "Device:D_Schottky", "PMEG4005EJ", "Diode_SMD:D_SOD-323F", 14400, 4400)
    add("BT2", "Device:Battery_Cell", "ML414H", "Kingfisher:ML414H", 12000, 3200)
    add("BT1", "Device:Battery_Cell", "CR2032", "Battery:BatteryHolder_Keystone_3002_1x2032", 8000, 3200)
    add("J2", "Connector:Conn_Coaxial", "U.FL", "Connector_Coaxial:U.FL_Hirose_U.FL-R-SMT-1_Vertical", 21600, 7600)
    add("R6", "Device:R", "1k", "Resistor_SMD:R_0603_1608Metric", 14400, 2000)
    add("D2", "Device:LED", "Green", "LED_SMD:LED_0603_1608Metric", 16800, 2000)
    add("R7", "Device:R", "4.7k", "Resistor_SMD:R_0402_1005Metric", 3600, 6400, dnp=True)
    add("R8", "Device:R", "4.7k", "Resistor_SMD:R_0402_1005Metric", 3600, 5200, dnp=True)
    for n, (x, net) in enumerate(
        (
            (1600, "+3V3"),
            (2400, "+5V"),
            (3200, "GND"),
            (4000, "V_BCKP"),
            (4800, "VBAT"),
        ),
        start=1,
    ):
        add(f"#FLG0{n}", "power:PWR_FLAG", "PWR_FLAG", "", x, 16000)

    tp_nets = [
        "+3V3",
        "+5V",
        "+3V3_GPS",
        "V_BCKP",
        "VBAT",
        "SDA",
        "SCL",
        "UART_TX",
        "UART_RX",
        "GPS_PPS",
        "IMU_INT",
        "GND",
    ]
    for n, net in enumerate(tp_nets, start=1):
        add(
            f"TP{n}",
            "Connector:TestPoint",
            net,
            "TestPoint:TestPoint_Pad_D1.5mm",
            1600 + (n - 1) * 1000,
            800,
        )

    # Datasheet pin locks. A renamed symbol pin fails the build instead of wiring the wrong pad.
    require(parts["U1"], "1", "AP_AD0")
    require(parts["U1"], "4", "INT1")
    require(parts["U1"], "5", "VDDIO")
    require(parts["U1"], "6", "GND")
    require(parts["U1"], "8", "VDD")
    require(parts["U1"], "12", "AP_CS")
    require(parts["U1"], "13", "AP_SCL")
    require(parts["U1"], "14", "AP_SDA")
    require(parts["U2"], "1", "VDDIO")
    require(parts["U2"], "2", "SCK")
    require(parts["U2"], "4", "SDI")
    require(parts["U2"], "5", "SDO")
    require(parts["U2"], "6", "CSB")
    require(parts["U2"], "10", "VDD")
    require(parts["U3"], "1", "VIN")
    require(parts["U3"], "3", "EN")
    require(parts["U3"], "5", "VOUT")
    require(parts["U4"], "2", "D_SEL")
    require(parts["U4"], "3", "TIMEPULSE")
    require(parts["U4"], "7", "VDD_USB")
    require(parts["U4"], "9", "VCC_RF")
    require(parts["U4"], "11", "RF_IN")
    require(parts["U4"], "20", "TXD")
    require(parts["U4"], "21", "RXD")
    require(parts["U4"], "22", "V_BCKP")
    require(parts["U4"], "23", "VCC")
    require(parts["U5"], "3", "SCL")
    require(parts["U5"], "4", "SDA")
    require(parts["U5"], "5", "VSS")
    require(parts["U5"], "6", "VBACKUP")
    require(parts["U5"], "7", "VDD")
    require(parts["D1"], "1", "K")
    require(parts["D1"], "2", "A")
    require(parts["D2"], "1", "K")
    require(parts["D2"], "2", "A")
    require(parts["BT1"], "1", "+")
    require(parts["BT2"], "1", "+")
    require(parts["J2"], "1", "In")
    require(parts["J2"], "2", "Ext")

    gnd_header = ("6", "9", "14", "20", "25", "30", "34", "39")
    links: list[tuple[str, str, str]] = [
        ("J1", "1", "+3V3"),
        ("J1", "17", "+3V3"),
        ("J1", "2", "+5V"),
        ("J1", "4", "+5V"),
        ("J1", "3", "SDA"),
        ("J1", "5", "SCL"),
        ("J1", "8", "UART_TX"),
        ("J1", "10", "UART_RX"),
        ("J1", "11", "IMU_INT"),
        ("J1", "12", "GPS_PPS"),
        ("J1", "31", "PLD"),
        ("J3", "1", "+3V3"),
        ("J3", "2", "GND"),
        ("J3", "3", "SDA"),
        ("J3", "4", "SCL"),
        ("J3", "5", "PLD"),
        ("U1", "1", "GND"),
        ("U1", "4", "IMU_INT"),
        ("U1", "5", "+3V3"),
        ("U1", "6", "GND"),
        ("U1", "8", "+3V3"),
        ("U1", "12", "+3V3"),
        ("U1", "13", "SCL"),
        ("U1", "14", "SDA"),
        ("C1", "1", "+3V3"),
        ("C1", "2", "GND"),
        ("C2", "1", "+3V3"),
        ("C2", "2", "GND"),
        ("U2", "1", "+3V3"),
        ("U2", "2", "SCL"),
        ("U2", "3", "GND"),
        ("U2", "4", "SDA"),
        ("U2", "5", "+3V3"),
        ("U2", "6", "+3V3"),
        ("U2", "8", "GND"),
        ("U2", "9", "GND"),
        ("U2", "10", "+3V3"),
        ("C3", "1", "+3V3"),
        ("C3", "2", "GND"),
        ("C4", "1", "+3V3"),
        ("C4", "2", "GND"),
        ("U5", "3", "SCL"),
        ("U5", "4", "SDA"),
        ("U5", "5", "GND"),
        ("U5", "6", "VBAT"),
        ("U5", "7", "+3V3"),
        ("C11", "1", "+3V3"),
        ("C11", "2", "GND"),
        ("U3", "1", "+5V"),
        ("U3", "2", "GND"),
        ("U3", "3", "+5V"),
        ("U3", "5", "+3V3_GPS"),
        ("C5", "1", "+5V"),
        ("C5", "2", "GND"),
        ("C6", "1", "+3V3_GPS"),
        ("C6", "2", "GND"),
        ("C7", "1", "+3V3_GPS"),
        ("C7", "2", "GND"),
        ("U4", "2", "D_SEL"),
        ("U4", "3", "GPS_PPS"),
        ("U4", "7", "+3V3_GPS"),
        ("U4", "9", "VCC_RF"),
        ("U4", "10", "GND"),
        ("U4", "11", "RF"),
        ("U4", "12", "GND"),
        ("U4", "13", "GND"),
        ("U4", "20", "GPS_TX"),
        ("U4", "21", "GPS_RX"),
        ("U4", "22", "V_BCKP"),
        ("U4", "23", "+3V3_GPS"),
        ("U4", "24", "GND"),
        ("C8", "1", "+3V3_GPS"),
        ("C8", "2", "GND"),
        ("C9", "1", "V_BCKP"),
        ("C9", "2", "GND"),
        ("R1", "1", "UART_TX"),
        ("R1", "2", "GPS_RX"),
        ("R2", "1", "GPS_TX"),
        ("R2", "2", "UART_RX"),
        ("R3", "1", "+3V3_GPS"),
        ("R3", "2", "D_SEL"),
        ("D1", "2", "+3V3_GPS"),
        ("D1", "1", "CHG"),
        ("R4", "1", "CHG"),
        ("R4", "2", "V_BCKP"),
        ("BT2", "1", "V_BCKP"),
        ("BT2", "2", "GND"),
        ("BT1", "1", "VBAT"),
        ("BT1", "2", "GND"),
        ("R5", "1", "VCC_RF"),
        ("R5", "2", "BIAS"),
        ("FB1", "1", "RF"),
        ("FB1", "2", "BIAS"),
        ("C10", "1", "BIAS"),
        ("C10", "2", "GND"),
        ("J2", "1", "RF"),
        ("J2", "2", "GND"),
        ("R6", "1", "+3V3_GPS"),
        ("R6", "2", "LED_A"),
        ("D2", "2", "LED_A"),
        ("D2", "1", "GND"),
        ("R7", "1", "+3V3"),
        ("R7", "2", "SDA"),
        ("R8", "1", "+3V3"),
        ("R8", "2", "SCL"),
        ("#FLG01", "1", "+3V3"),
        ("#FLG02", "1", "+5V"),
        ("#FLG03", "1", "GND"),
        ("#FLG04", "1", "V_BCKP"),
        ("#FLG05", "1", "VBAT"),
    ]
    for num in gnd_header:
        links.append(("J1", num, "GND"))
    for n, net in enumerate(tp_nets, start=1):
        links.append((f"TP{n}", "1", net))

    # coordinate -> net, for stacked pins (NEO grounds share one point).
    claimed: dict[tuple[str, int, int], str] = {}
    segments: list[tuple[int, int, int, int, str]] = []
    drawings: list[str] = []
    for ref, num, net in links:
        part = parts[ref]
        x, y, ang = part.tip(num)
        key = (ref, x, y)
        previous = claimed.get(key)
        if previous not in (None, net):
            raise SystemExit(f"{ref} pin {num} at {key} is both {previous} and {net}")
        if previous == net:
            continue
        claimed[key] = net
        dx, dy = outward(ang)
        x2, y2 = x + dx, y + dy
        for sx1, sy1, sx2, sy2, other in segments:
            if _point_on_segment(x, y, sx1, sy1, sx2, sy2) and other != net:
                raise SystemExit(f"{ref} pin {num} ({net}) lands on the {other} wire")
            if _point_on_segment(x2, y2, sx1, sy1, sx2, sy2) and other != net:
                raise SystemExit(f"{ref} pin {num} stub ({net}) lands on the {other} wire")
        segments.append((x, y, x2, y2, net))
        drawings.append(wire(x, y, x2, y2))
        rot = 0 if dx > 0 else 180 if dx < 0 else 90 if dy < 0 else 270
        drawings.append(label(net, x2, y2, rot))

    for ref, part in parts.items():
        for num, pin in part.pins.items():
            x, y, _ang = part.tip(num)
            if (ref, x, y) in claimed:
                continue
            drawings.append(no_connect(x, y))

    # Two different nets must not share a point. Same-net stubs may meet.
    occupied: dict[tuple[int, int], str] = {}
    for ref, num, net in links:
        part = parts[ref]
        x, y, ang = part.tip(num)
        dx, dy = outward(ang)
        for point in ((x, y), (x + dx, y + dy)):
            other = occupied.get(point)
            if other not in (None, net):
                raise SystemExit(f"net {net} meets {other} at {point}")
            occupied[point] = net

    notes = [
        text("Kingfisher Sensor HAT rev 0.3", 1000, 17200, "2.54"),
        text("ICM-45686 0x68, BMP581 0x47, RV-3028-C7 0x52, NEO-M9N UART+PPS. No UPS.", 1000, 16800),
        text("GPS backup is ML414H directly on V_BCKP, charged from +3V3_GPS through PMEG4005EJ and 1k. CR2032 feeds only RV-3028 VBACKUP.", 1000, 16500),
        text("J3 X1200 harness, pin 1 to 5: +3V3, GND, SDA, SCL, GPIO6. R7 and R8 I2C pull-ups are DNP.", 1000, 16200),
        text("Antenna bias: VCC_RF, 33 ohm, 47pF to GND, 120R ferrite onto RF_IN. Ferrite is the bias choke, not in series with the RF path and not on VCC.", 1000, 15900),
    ]
    body = [instance(part) for part in parts.values()]
    body.extend(drawings)
    body.extend(notes)
    lib_symbols = "\t(lib_symbols\n" + "\n".join(embedded) + "\n\t)\n"
    sch = (
        "(kicad_sch\n"
        "\t(version 20250114)\n"
        '\t(generator "kingfisher-build_sch")\n'
        '\t(generator_version "0.3")\n'
        f'\t(uuid "{ROOT_UUID}")\n'
        '\t(paper "A1")\n'
        "\t(title_block\n"
        '\t\t(title "Kingfisher Sensor HAT")\n'
        '\t\t(date "2026-10-17")\n'
        '\t\t(rev "0.3")\n'
        '\t\t(company "westphae/kingfisher")\n'
        '\t\t(comment 1 "ICM-45686 + BMP581 + NEO-M9N + RV-3028")\n'
        '\t\t(comment 2 "ML414H GPS backup, CR2032 RTC, X1200 harness, no UPS")\n'
        "\t)\n"
        f"{lib_symbols}"
        + "\n".join(body)
        + "\n"
        "\t(sheet_instances\n"
        '\t\t(path "/"\n'
        '\t\t\t(page "1")\n'
        "\t\t)\n"
        "\t)\n"
        "\t(embedded_fonts no)\n"
        ")\n"
    )
    OUT.write_text(sch)
    print(f"Wrote {OUT} ({len(parts)} symbols)")


if __name__ == "__main__":
    main()
