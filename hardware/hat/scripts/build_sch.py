#!/usr/bin/env python3
"""
Build a KiCad-9-loadable kingfisher-hat.kicad_sch.

Symbols are embedded in lib_symbols (same pattern as the RaspberryPi-HAT
template) so kicad-cli resolves components without relying on project tables.
"""
from __future__ import annotations

import re
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "kingfisher-hat.kicad_sch"
OURS = ROOT / "libs" / "Kingfisher.kicad_sym"


def uid() -> str:
    return str(uuid.uuid4())


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
    """Embed a library symbol into sch lib_symbols with correct unit names.

    KiCad requires:
      parent:  (symbol "Lib:Name" ...)
      units:   (symbol "Name_0_1" ...) / (symbol "Name_1_1" ...)
    Unit children must keep the *short* name (no Lib: prefix). Renaming them
    to Lib:Name_0_1 makes kicad-cli refuse to load the schematic.

    Indent: template style is symbol at 2 tabs, children at 3+.
    """
    lines = [ln.rstrip() for ln in sym.lstrip("\n").splitlines()]
    if not lines:
        raise SystemExit("empty symbol")

    def ntabs(ln: str) -> int:
        return len(ln) - len(ln.lstrip("\t"))

    # Normalize so header has 0 leading tabs and body is relative +1.
    d = ntabs(lines[0])
    lines = [ln[d:] if ln.startswith("\t" * d) else ln for ln in lines]

    s = "\n".join(lines)
    m = re.match(r'\(symbol "([^"]+)"', s)
    if not m:
        raise SystemExit(f"bad symbol start: {s[:80]!r}")
    old_name = m.group(1)
    short = lib_id.split(":", 1)[-1]

    # Parent only → Lib:Name. Units keep short name (or rename short→short).
    s = s.replace(f'(symbol "{old_name}"', f'(symbol "{lib_id}"', 1)
    if old_name != short:
        s = s.replace(f'(symbol "{old_name}_', f'(symbol "{short}_')
    return "\n".join(("\t\t" + line) if line.strip() else line for line in s.splitlines())


def prop(name: str, value: str, x: float, y: float, hide: bool = False) -> str:
    hide_s = "\n\t\t\t\t(hide yes)" if hide else ""
    return f'''\t\t(property "{name}" "{value}"
\t\t\t(at {x} {y} 0)
\t\t\t(effects
\t\t\t\t(font
\t\t\t\t\t(size 1.27 1.27)
\t\t\t\t){hide_s}
\t\t\t)
\t\t)'''


ROOT_UUID = "e63e39d7-6ac0-4ffd-8aa3-1841a4541b55"  # stable sheet path for instances


def instance(
    lib_id: str,
    ref: str,
    value: str,
    footprint: str,
    x: float,
    y: float,
    pins: list[str],
    rot: int = 0,
    dnp: bool = False,
) -> str:
    pin_block = "\n".join(f'\t\t(pin "{p}"\n\t\t\t(uuid "{uid()}")\n\t\t)' for p in pins)
    return f'''\t(symbol
\t\t(lib_id "{lib_id}")
\t\t(at {x} {y} {rot})
\t\t(unit 1)
\t\t(exclude_from_sim no)
\t\t(in_bom yes)
\t\t(on_board yes)
\t\t(dnp {"yes" if dnp else "no"})
\t\t(uuid "{uid()}")
{prop("Reference", ref, x, y + 5.08)}
{prop("Value", value, x, y - 5.08)}
{prop("Footprint", footprint, x, y, hide=True)}
{prop("Datasheet", "~", x, y, hide=True)}
{prop("Description", "", x, y, hide=True)}
{pin_block}
\t\t(instances
\t\t\t(project "kingfisher-hat"
\t\t\t\t(path "/{ROOT_UUID}"
\t\t\t\t\t(reference "{ref}")
\t\t\t\t\t(unit 1)
\t\t\t\t)
\t\t\t)
\t\t)
\t)'''


def power(lib_id: str, ref: str, value: str, x: float, y: float) -> str:
    return instance(lib_id, ref, value, "", x, y, ["1"])


def label(name: str, x: float, y: float, rot: int = 0) -> str:
    return f'''\t(label "{name}"
\t\t(at {x} {y} {rot})
\t\t(effects
\t\t\t(font
\t\t\t\t(size 1.27 1.27)
\t\t\t)
\t\t\t(justify left)
\t\t)
\t\t(uuid "{uid()}")
\t)'''


def wire(x1: float, y1: float, x2: float, y2: float) -> str:
    return f'''\t(wire
\t\t(pts
\t\t\t(xy {x1} {y1})
\t\t\t(xy {x2} {y2})
\t\t)
\t\t(stroke
\t\t\t(width 0)
\t\t\t(type default)
\t\t)
\t\t(uuid "{uid()}")
\t)'''


def text(s: str, x: float, y: float, size: float = 1.27) -> str:
    # KiCad text uses literal newlines as \\n in some versions; use multi text boxes
    return f'''\t(text "{s}"
\t\t(exclude_from_sim no)
\t\t(at {x} {y} 0)
\t\t(effects
\t\t\t(font
\t\t\t\t(size {size} {size})
\t\t\t)
\t\t\t(justify left bottom)
\t\t)
\t\t(uuid "{uid()}")
\t)'''


def main() -> None:
    # --- embed symbols ---
    embeds: list[tuple[str, Path | str, str]] = [
        ("Kingfisher:ICM-45686", OURS, "ICM-45686"),
        ("Kingfisher:BMP581", OURS, "BMP581"),
        ("Kingfisher:NEO-M9N", OURS, "NEO-M9N"),
        ("Connector_Generic:Conn_02x20_Odd_Even", "/usr/share/kicad/symbols/Connector_Generic.kicad_sym", "Conn_02x20_Odd_Even"),
        ("Device:C", "/usr/share/kicad/symbols/Device.kicad_sym", "C"),
        ("Device:D_Schottky", "/usr/share/kicad/symbols/Device.kicad_sym", "D_Schottky"),
        ("Device:Battery_Cell", "/usr/share/kicad/symbols/Device.kicad_sym", "Battery_Cell"),
        ("Connector:Conn_Coaxial", "/usr/share/kicad/symbols/Connector.kicad_sym", "Conn_Coaxial"),
        ("Timer_RTC:RV-3028-C7", "/usr/share/kicad/symbols/Timer_RTC.kicad_sym", "RV-3028-C7"),
        ("power:+3.3V", "/usr/share/kicad/symbols/power.kicad_sym", "+3.3V"),
        ("power:+5V", "/usr/share/kicad/symbols/power.kicad_sym", "+5V"),
        ("power:GND", "/usr/share/kicad/symbols/power.kicad_sym", "GND"),
    ]
    # AP2112K-3.3 extends AP2204K-1.5 in the stock lib — expand to a standalone embed.
    ldo = extract_symbol("/usr/share/kicad/symbols/Regulator_Linear.kicad_sym", "AP2204K-1.5")
    ldo = ldo.replace("AP2204K-1.5", "AP2112K-3.3")
    embedded = [embed_as(lib_id, extract_symbol(path, name)) for lib_id, path, name in embeds]
    embedded.append(embed_as("Regulator_Linear:AP2112K-3.3", ldo))

    lib_symbols = "\t(lib_symbols\n" + "\n".join(embedded) + "\n\t)\n"

    parts: list[str] = []
    parts.append(text("Kingfisher Sensor HAT rev 0.2", 25.4, 190.5, 2.54))
    parts.append(
        text(
            "ICM-45686 + BMP581 + NEO-M9N + RV-3028 RTC + CR2032 backup",
            25.4,
            185.42,
        )
    )

    # GPIO
    parts.append(
        instance(
            "Connector_Generic:Conn_02x20_Odd_Even",
            "J1",
            "GPIO_40",
            "Connector_PinSocket_2.54mm:PinSocket_2x20_P2.54mm_Vertical",
            43.18,
            114.3,
            [str(i) for i in range(1, 41)],
        )
    )

    def pin_xy(n: int) -> tuple[float, float]:
        row = (n - 1) // 2
        odd = n % 2 == 1
        x = 43.18 - 2.54 if odd else 43.18 + 2.54
        y = 114.3 + 24.13 - row * 2.54
        return x, y

    key = {
        1: "+3V3",
        2: "+5V",
        3: "SDA",
        4: "+5V",
        5: "SCL",
        6: "GND",
        8: "UART_TX",
        9: "GND",
        10: "UART_RX",
        11: "IMU_INT",
        12: "GPS_PPS",
        14: "GND",
        17: "GND",
        20: "GND",
        25: "GND",
        30: "GND",
        34: "GND",
        39: "GND",
    }
    for n, net in key.items():
        x, y = pin_xy(n)
        if n % 2 == 1:
            parts.append(wire(x, y, x - 7.62, y))
            parts.append(label(net, x - 7.62, y, 180))
        else:
            parts.append(wire(x, y, x + 7.62, y))
            parts.append(label(net, x + 7.62, y, 0))

    parts.append(power("power:+3.3V", "#PWR01", "+3V3", 76.2, 172.72))
    parts.append(wire(76.2, 172.72, 76.2, 165.1))
    parts.append(label("+3V3", 76.2, 165.1, 0))
    parts.append(power("power:+5V", "#PWR02", "+5V", 86.36, 172.72))
    parts.append(wire(86.36, 172.72, 86.36, 165.1))
    parts.append(label("+5V", 86.36, 165.1, 0))
    parts.append(power("power:GND", "#PWR03", "GND", 96.52, 172.72))
    parts.append(wire(96.52, 172.72, 96.52, 165.1))
    parts.append(label("GND", 96.52, 165.1, 0))

    # ICM — AP_AD0=GND → 0x68
    parts.append(text("U1 ICM-45686 I2C 0x68 (AP_AD0=GND)", 114.3, 172.72))
    parts.append(
        instance(
            "Kingfisher:ICM-45686",
            "U1",
            "ICM-45686",
            "Package_LGA:Bosch_LGA-14_3x2.5mm_P0.5mm",
            137.16,
            147.32,
            [str(i) for i in range(1, 15)],
        )
    )
    parts.append(label("+3V3", 137.16, 163.83, 90))
    parts.append(label("+3V3", 139.7, 163.83, 90))
    parts.append(label("GND", 137.16, 130.81, 270))
    parts.append(label("GND", 124.46, 157.48, 180))  # AP_AD0
    parts.append(label("IMU_INT", 149.86, 157.48, 0))
    parts.append(label("+3V3", 124.46, 142.24, 180))  # AP_CS
    parts.append(label("SCL", 124.46, 139.7, 180))
    parts.append(label("SDA", 124.46, 137.16, 180))
    for ref, x in (("C1", 160.02), ("C2", 170.18)):
        parts.append(
            instance(
                "Device:C",
                ref,
                "100nF",
                "Capacitor_SMD:C_0402_1005Metric",
                x,
                160.02,
                ["1", "2"],
            )
        )
        parts.append(label("+3V3", x, 162.56, 90))
        parts.append(label("GND", x, 157.48, 270))

    # BMP581 — 0x47
    parts.append(text("U2 BMP581 I2C 0x47 (SDO=1)", 114.3, 119.38))
    parts.append(
        instance(
            "Kingfisher:BMP581",
            "U2",
            "BMP581",
            "Kingfisher:BMP581",
            137.16,
            96.52,
            [str(i) for i in range(1, 11)],
        )
    )
    parts.append(label("+3V3", 137.16, 110.49, 90))
    parts.append(label("+3V3", 139.7, 110.49, 90))
    parts.append(label("GND", 137.16, 82.55, 270))
    parts.append(label("GND", 139.7, 82.55, 270))
    parts.append(label("GND", 142.24, 82.55, 270))
    parts.append(label("SCL", 127.0, 101.6, 180))
    parts.append(label("SDA", 127.0, 99.06, 180))
    parts.append(label("+3V3", 127.0, 96.52, 180))  # SDO
    parts.append(label("+3V3", 127.0, 93.98, 180))  # CSB
    parts.append(label("GND", 147.32, 101.6, 0))  # INT unused
    for ref, x in (("C3", 160.02), ("C4", 170.18)):
        parts.append(
            instance(
                "Device:C",
                ref,
                "100nF",
                "Capacitor_SMD:C_0402_1005Metric",
                x,
                104.14,
                ["1", "2"],
            )
        )
        parts.append(label("+3V3", x, 106.68, 90))
        parts.append(label("GND", x, 101.6, 270))

    # GPS LDO
    parts.append(text("U3 AP2112K-3.3  GPS rail from +5V", 195.58, 172.72))
    parts.append(
        instance(
            "Regulator_Linear:AP2112K-3.3",
            "U3",
            "AP2112K-3.3",
            "Package_TO_SOT_SMD:SOT-23-5",
            215.9,
            152.4,
            ["1", "2", "3", "4", "5"],
        )
    )
    parts.append(label("+5V", 203.2, 157.48, 180))
    parts.append(label("GND", 215.9, 142.24, 270))
    parts.append(label("+3V3_GPS", 228.6, 157.48, 0))
    parts.append(label("+5V", 215.9, 162.56, 90))  # EN high
    for ref, x, vin in (("C5", 200.66, "+5V"), ("C6", 231.14, "+3V3_GPS")):
        parts.append(
            instance(
                "Device:C",
                ref,
                "1uF",
                "Capacitor_SMD:C_0603_1608Metric",
                x,
                139.7,
                ["1", "2"],
            )
        )
        parts.append(label(vin, x, 142.24, 90))
        parts.append(label("GND", x, 137.16, 270))

    # NEO-M9N
    parts.append(text("U4 NEO-M9N  V_BCKP = diode-OR(+3V3_GPS, VBAT)", 195.58, 124.46))
    parts.append(
        instance(
            "Kingfisher:NEO-M9N",
            "U4",
            "NEO-M9N",
            "RF_GPS:ublox_NEO",
            228.6,
            96.52,
            [str(i) for i in range(1, 25)],
        )
    )
    parts.append(label("+3V3_GPS", 228.6, 114.3, 90))
    parts.append(label("V_BCKP", 241.3, 114.3, 90))
    parts.append(label("GND", 228.6, 78.74, 270))
    parts.append(label("UART_RX", 248.92, 101.6, 0))  # GPS TXD -> Pi RX
    parts.append(label("UART_TX", 248.92, 96.52, 0))  # GPS RXD <- Pi TX
    parts.append(label("GPS_PPS", 248.92, 106.68, 0))
    parts.append(label("RF_IN", 208.28, 96.52, 180))
    parts.append(
        instance(
            "Device:C",
            "C7",
            "100nF",
            "Capacitor_SMD:C_0402_1005Metric",
            259.08,
            114.3,
            ["1", "2"],
        )
    )
    parts.append(label("+3V3_GPS", 259.08, 116.84, 90))
    parts.append(label("GND", 259.08, 111.76, 270))

    parts.append(
        instance(
            "Connector:Conn_Coaxial",
            "J2",
            "U.FL",
            "Connector_Coaxial:U.FL_Hirose_U.FL-R-SMT-1_Vertical",
            195.58,
            96.52,
            ["1", "2"],
        )
    )
    parts.append(label("RF_IN", 200.66, 96.52, 0))
    parts.append(label("GND", 195.58, 91.44, 270))

    # Backup battery + diode OR for GPS V_BCKP
    parts.append(text("BT1 CR2032  RTC + GPS backup (primary, not charged)", 25.4, 55.88))
    parts.append(
        instance(
            "Device:Battery_Cell",
            "BT1",
            "CR2032",
            "Battery:BatteryHolder_Keystone_3002_1x2032",
            50.8,
            40.64,
            ["1", "2"],
        )
    )
    parts.append(label("VBAT", 50.8, 48.26, 90))
    parts.append(label("GND", 50.8, 33.02, 270))

    # D1: +3V3_GPS -> V_BCKP (anode at 3V3)
    parts.append(
        instance(
            "Device:D_Schottky",
            "D1",
            "BAT54",
            "Diode_SMD:D_SOD-123",
            86.36,
            48.26,
            ["1", "2"],
        )
    )
    parts.append(label("+3V3_GPS", 78.74, 48.26, 180))
    parts.append(label("V_BCKP", 93.98, 48.26, 0))
    # D2: VBAT -> V_BCKP
    parts.append(
        instance(
            "Device:D_Schottky",
            "D2",
            "BAT54",
            "Diode_SMD:D_SOD-123",
            86.36,
            35.56,
            ["1", "2"],
        )
    )
    parts.append(label("VBAT", 78.74, 35.56, 180))
    parts.append(label("V_BCKP", 93.98, 35.56, 0))
    # RTC RV-3028 @ 0x52
    parts.append(text("U5 RV-3028-C7 I2C 0x52  VBACKUP=VBAT", 121.92, 55.88))
    parts.append(
        instance(
            "Timer_RTC:RV-3028-C7",
            "U5",
            "RV-3028-C7",
            "Package_SON:MicroCrystal_C7_SON-8_1.5x3.2mm_P0.9mm",
            152.4,
            40.64,
            ["1", "2", "3", "4", "5", "6", "7", "8"],
        )
    )
    parts.append(label("SCL", 139.7, 45.72, 180))
    parts.append(label("SDA", 139.7, 43.18, 180))
    parts.append(label("+3V3", 152.4, 53.34, 90))
    parts.append(label("GND", 152.4, 27.94, 270))
    parts.append(label("VBAT", 165.1, 40.64, 0))  # VBACKUP
    parts.append(
        instance(
            "Device:C",
            "C8",
            "100nF",
            "Capacitor_SMD:C_0402_1005Metric",
            175.26,
            48.26,
            ["1", "2"],
        )
    )
    parts.append(label("+3V3", 175.26, 50.8, 90))
    parts.append(label("GND", 175.26, 45.72, 270))

    parts.append(
        text(
            "NOTES: ICM 0x68 / BMP581 0x47 / RV-3028 0x52 / UPS 0x36. "
            "CR2032 is primary — diode-OR must not charge it. "
            "Open in eeschema to tidy pin-label geometry before fab. "
            "See docs/DESIGN.md.",
            25.4,
            20.32,
        )
    )

    sch = f'''(kicad_sch
\t(version 20250114)
\t(generator "eeschema")
\t(generator_version "9.0")
\t(uuid "{ROOT_UUID}")
\t(paper "A3")
\t(title_block
\t\t(title "Kingfisher Sensor HAT")
\t\t(date "2026-07-26")
\t\t(rev "0.2")
\t\t(company "westphae/kingfisher")
\t\t(comment 1 "ICM-45686 + BMP581 + NEO-M9N + RV-3028 + CR2032")
\t\t(comment 2 "Mounts on extended GPIO above M.2 HAT+")
\t)
{lib_symbols}{"".join(p + chr(10) for p in parts)}\t(sheet_instances
\t\t(path "/"
\t\t\t(page "1")
\t\t)
\t)
\t(embedded_fonts no)
)
'''
    OUT.write_text(sch)
    print("Wrote", OUT, "bytes", len(sch))


if __name__ == "__main__":
    main()
