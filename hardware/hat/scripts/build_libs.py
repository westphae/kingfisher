#!/usr/bin/env python3
"""Generate Kingfisher HAT custom symbols and footprints."""
from __future__ import annotations

import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "libs"
PRETTY = LIB / "Kingfisher.pretty"


def uid() -> str:
    return str(uuid.uuid4())


def write_bmp581_footprint() -> None:
    """LGA-10 2.0x2.0 mm — pad geometry from SparkFun BMP581 (CC-BY-SA 4.0)."""
    # SparkFun pad centres (mm), top view; cream rectangles in their lib are paste apertures.
    pads = {
        1: (0.25, 0.7625, 0.25, 0.275),
        2: (-0.25, 0.7625, 0.25, 0.275),
        3: (-0.7625, 0.5, 0.275, 0.25),
        4: (-0.7625, 0.0, 0.275, 0.25),
        5: (-0.7625, -0.5, 0.275, 0.25),
        6: (-0.25, -0.7625, 0.25, 0.275),
        7: (0.25, -0.7625, 0.25, 0.275),
        8: (0.7625, -0.5, 0.275, 0.25),
        9: (0.7625, 0.0, 0.275, 0.25),
        10: (0.7625, 0.5, 0.275, 0.25),
    }
    lines = [
        '(footprint "BMP581"',
        '\t(version 20241229)',
        '\t(generator "kingfisher-build_libs")',
        '\t(generator_version "1.0")',
        '\t(layer "F.Cu")',
        '\t(descr "Bosch BMP581 LGA-10 2.0x2.0mm; pad layout adapted from SparkFun Qwiic BMP581 (CC-BY-SA 4.0)")',
        '\t(tags "BMP581 Bosch pressure LGA-10")',
        '\t(attr smd)',
        f'\t(property "Reference" "U" (at 0 1.9 0) (layer "F.SilkS") (uuid "{uid()}")',
        '\t\t(effects (font (size 0.8 0.8) (thickness 0.12))))',
        f'\t(property "Value" "BMP581" (at 0 -1.9 0) (layer "F.Fab") (uuid "{uid()}")',
        '\t\t(effects (font (size 0.8 0.8) (thickness 0.12))))',
        f'\t(property "Datasheet" "https://www.bosch-sensortec.com/media/boschsensortec/downloads/datasheets/bst-bmp581-ds004.pdf" (at 0 0 0) (layer "F.Fab") (hide yes) (uuid "{uid()}")',
        '\t\t(effects (font (size 1.27 1.27) (thickness 0.15))))',
        f'\t(property "Description" "Barometric pressure sensor, I2C/SPI, LGA-10" (at 0 0 0) (layer "F.Fab") (hide yes) (uuid "{uid()}")',
        '\t\t(effects (font (size 1.27 1.27) (thickness 0.15))))',
        # courtyard / fab
        '\t(fp_rect (start -1.1 -1.1) (end 1.1 1.1) (stroke (width 0.05) (type solid)) (fill none) (layer "F.CrtYd")'
        f' (uuid "{uid()}"))',
        '\t(fp_rect (start -1.0 -1.0) (end 1.0 1.0) (stroke (width 0.1) (type solid)) (fill none) (layer "F.Fab")'
        f' (uuid "{uid()}"))',
        '\t(fp_circle (center 0.55 0.85) (end 0.65 0.85) (stroke (width 0.1) (type solid)) (fill none) (layer "F.SilkS")'
        f' (uuid "{uid()}"))',
    ]
    for num, (x, y, w, h) in pads.items():
        lines.append(
            f'\t(pad "{num}" smd rect (at {x} {y}) (size {w} {h}) '
            f'(layers "F.Cu" "F.Paste" "F.Mask") (uuid "{uid()}"))'
        )
    lines.append(")")
    (PRETTY / "BMP581.kicad_mod").write_text("\n".join(lines) + "\n")


def write_symbols() -> None:
    neo = Path("/tmp/neo_m9n_sym.txt").read_text()
    # Retarget NEO symbol into our library (indent as top-level symbol entries use one tab)
    neo_body = neo  # already starts with (symbol "NEO-M9N"

    icm = f'''\t(symbol "ICM-45686"
\t\t(exclude_from_sim no)
\t\t(in_bom yes)
\t\t(on_board yes)
\t\t(property "Reference" "U"
\t\t\t(at -12.7 15.24 0)
\t\t\t(effects (font (size 1.27 1.27))))
\t\t(property "Value" "ICM-45686"
\t\t\t(at 7.62 15.24 0)
\t\t\t(effects (font (size 1.27 1.27))))
\t\t(property "Footprint" "Package_LGA:Bosch_LGA-14_3x2.5mm_P0.5mm"
\t\t\t(at 0 0 0)
\t\t\t(effects (font (size 1.27 1.27)) (hide yes)))
\t\t(property "Datasheet" "https://invensense.tdk.com/download-pdf/ds-000577-icm-45686-datasheet/"
\t\t\t(at 0 0 0)
\t\t\t(effects (font (size 1.27 1.27)) (hide yes)))
\t\t(property "Description" "6-axis IMU, I2C/I3C/SPI, LGA-14 2.5x3.0mm"
\t\t\t(at 0 0 0)
\t\t\t(effects (font (size 1.27 1.27)) (hide yes)))
\t\t(property "ki_keywords" "IMU gyro accel TDK InvenSense"
\t\t\t(at 0 0 0)
\t\t\t(effects (font (size 1.27 1.27)) (hide yes)))
\t\t(symbol "ICM-45686_0_1"
\t\t\t(rectangle
\t\t\t\t(start -10.16 13.97)
\t\t\t\t(end 10.16 -13.97)
\t\t\t\t(stroke (width 0.254) (type default))
\t\t\t\t(fill (type background))))
\t\t(symbol "ICM-45686_1_1"
\t\t\t(pin bidirectional line (at -12.7 10.16 0) (length 2.54)
\t\t\t\t(name "AP_AD0" (effects (font (size 1.27 1.27))))
\t\t\t\t(number "1" (effects (font (size 1.27 1.27)))))
\t\t\t(pin no_connect line (at -12.7 7.62 0) (length 2.54)
\t\t\t\t(name "NC" (effects (font (size 1.27 1.27))))
\t\t\t\t(number "2" (effects (font (size 1.27 1.27)))))
\t\t\t(pin no_connect line (at -12.7 5.08 0) (length 2.54)
\t\t\t\t(name "NC" (effects (font (size 1.27 1.27))))
\t\t\t\t(number "3" (effects (font (size 1.27 1.27)))))
\t\t\t(pin output line (at 12.7 10.16 180) (length 2.54)
\t\t\t\t(name "INT1" (effects (font (size 1.27 1.27))))
\t\t\t\t(number "4" (effects (font (size 1.27 1.27)))))
\t\t\t(pin power_in line (at 0 16.51 270) (length 2.54)
\t\t\t\t(name "VDDIO" (effects (font (size 1.27 1.27))))
\t\t\t\t(number "5" (effects (font (size 1.27 1.27)))))
\t\t\t(pin power_in line (at 0 -16.51 90) (length 2.54)
\t\t\t\t(name "GND" (effects (font (size 1.27 1.27))))
\t\t\t\t(number "6" (effects (font (size 1.27 1.27)))))
\t\t\t(pin no_connect line (at -12.7 2.54 0) (length 2.54)
\t\t\t\t(name "NC" (effects (font (size 1.27 1.27))))
\t\t\t\t(number "7" (effects (font (size 1.27 1.27)))))
\t\t\t(pin power_in line (at 2.54 16.51 270) (length 2.54)
\t\t\t\t(name "VDD" (effects (font (size 1.27 1.27))))
\t\t\t\t(number "8" (effects (font (size 1.27 1.27)))))
\t\t\t(pin no_connect line (at 12.7 7.62 180) (length 2.54)
\t\t\t\t(name "INT2" (effects (font (size 1.27 1.27))))
\t\t\t\t(number "9" (effects (font (size 1.27 1.27)))))
\t\t\t(pin no_connect line (at -12.7 0 0) (length 2.54)
\t\t\t\t(name "NC" (effects (font (size 1.27 1.27))))
\t\t\t\t(number "10" (effects (font (size 1.27 1.27)))))
\t\t\t(pin no_connect line (at -12.7 -2.54 0) (length 2.54)
\t\t\t\t(name "NC" (effects (font (size 1.27 1.27))))
\t\t\t\t(number "11" (effects (font (size 1.27 1.27)))))
\t\t\t(pin input line (at -12.7 -5.08 0) (length 2.54)
\t\t\t\t(name "AP_CS" (effects (font (size 1.27 1.27))))
\t\t\t\t(number "12" (effects (font (size 1.27 1.27)))))
\t\t\t(pin bidirectional line (at -12.7 -7.62 0) (length 2.54)
\t\t\t\t(name "AP_SCL" (effects (font (size 1.27 1.27))))
\t\t\t\t(number "13" (effects (font (size 1.27 1.27)))))
\t\t\t(pin bidirectional line (at -12.7 -10.16 0) (length 2.54)
\t\t\t\t(name "AP_SDA" (effects (font (size 1.27 1.27))))
\t\t\t\t(number "14" (effects (font (size 1.27 1.27)))))
\t\t)
\t)'''

    bmp = f'''\t(symbol "BMP581"
\t\t(exclude_from_sim no)
\t\t(in_bom yes)
\t\t(on_board yes)
\t\t(property "Reference" "U"
\t\t\t(at -10.16 12.7 0)
\t\t\t(effects (font (size 1.27 1.27))))
\t\t(property "Value" "BMP581"
\t\t\t(at 5.08 12.7 0)
\t\t\t(effects (font (size 1.27 1.27))))
\t\t(property "Footprint" "Kingfisher:BMP581"
\t\t\t(at 0 0 0)
\t\t\t(effects (font (size 1.27 1.27)) (hide yes)))
\t\t(property "Datasheet" "https://www.bosch-sensortec.com/media/boschsensortec/downloads/datasheets/bst-bmp581-ds004.pdf"
\t\t\t(at 0 0 0)
\t\t\t(effects (font (size 1.27 1.27)) (hide yes)))
\t\t(property "Description" "Barometric pressure + temperature, I2C/SPI, LGA-10 2.0x2.0mm"
\t\t\t(at 0 0 0)
\t\t\t(effects (font (size 1.27 1.27)) (hide yes)))
\t\t(property "ki_keywords" "pressure barometer Bosch BMP581"
\t\t\t(at 0 0 0)
\t\t\t(effects (font (size 1.27 1.27)) (hide yes)))
\t\t(symbol "BMP581_0_1"
\t\t\t(rectangle
\t\t\t\t(start -7.62 11.43)
\t\t\t\t(end 7.62 -11.43)
\t\t\t\t(stroke (width 0.254) (type default))
\t\t\t\t(fill (type background))))
\t\t(symbol "BMP581_1_1"
\t\t\t(pin power_in line (at 0 13.97 270) (length 2.54)
\t\t\t\t(name "VDDIO" (effects (font (size 1.27 1.27))))
\t\t\t\t(number "1" (effects (font (size 1.27 1.27)))))
\t\t\t(pin bidirectional line (at -10.16 5.08 0) (length 2.54)
\t\t\t\t(name "SCK" (effects (font (size 1.27 1.27))))
\t\t\t\t(number "2" (effects (font (size 1.27 1.27)))))
\t\t\t(pin power_in line (at 0 -13.97 90) (length 2.54)
\t\t\t\t(name "VSS" (effects (font (size 1.27 1.27))))
\t\t\t\t(number "3" (effects (font (size 1.27 1.27)))))
\t\t\t(pin bidirectional line (at -10.16 2.54 0) (length 2.54)
\t\t\t\t(name "SDI" (effects (font (size 1.27 1.27))))
\t\t\t\t(number "4" (effects (font (size 1.27 1.27)))))
\t\t\t(pin bidirectional line (at -10.16 0 0) (length 2.54)
\t\t\t\t(name "SDO" (effects (font (size 1.27 1.27))))
\t\t\t\t(number "5" (effects (font (size 1.27 1.27)))))
\t\t\t(pin input line (at -10.16 -2.54 0) (length 2.54)
\t\t\t\t(name "CSB" (effects (font (size 1.27 1.27))))
\t\t\t\t(number "6" (effects (font (size 1.27 1.27)))))
\t\t\t(pin output line (at 10.16 5.08 180) (length 2.54)
\t\t\t\t(name "INT" (effects (font (size 1.27 1.27))))
\t\t\t\t(number "7" (effects (font (size 1.27 1.27)))))
\t\t\t(pin power_in line (at 2.54 -13.97 90) (length 2.54)
\t\t\t\t(name "VSS" (effects (font (size 1.27 1.27))))
\t\t\t\t(number "8" (effects (font (size 1.27 1.27)))))
\t\t\t(pin power_in line (at 5.08 -13.97 90) (length 2.54)
\t\t\t\t(name "VSS" (effects (font (size 1.27 1.27))))
\t\t\t\t(number "9" (effects (font (size 1.27 1.27)))))
\t\t\t(pin power_in line (at 2.54 13.97 270) (length 2.54)
\t\t\t\t(name "VDD" (effects (font (size 1.27 1.27))))
\t\t\t\t(number "10" (effects (font (size 1.27 1.27)))))
\t\t)
\t)'''

    # NEO symbol from KiCad RF_GPS uses one-tab indent; keep as-is inside file
    out = [
        "(kicad_symbol_lib",
        '\t(version 20241209)',
        '\t(generator "kingfisher-build_libs")',
        '\t(generator_version "1.0")',
        '\t(version 20241209)',
        icm,
        bmp,
        # neo already has leading tab from extract? check
        neo_body if neo_body.startswith("\t") else "\t" + neo_body.replace("\n", "\n\t").rstrip("\t"),
        ")",
    ]
    # Fix neo indentation: extracted block starts with (symbol
    if neo_body.startswith("(symbol"):
        neo_indented = "\t" + neo_body.replace("\n", "\n\t")
        # undo double-indent on last
        out = [
            "(kicad_symbol_lib",
            '\t(version 20241209)',
            '\t(generator "kingfisher-build_libs")',
            '\t(generator_version "1.0")',
            icm,
            bmp,
            neo_indented.rstrip() + "\n)",
        ]
        (LIB / "Kingfisher.kicad_sym").write_text("\n".join(out[:-1]) + "\n" + out[-1] if False else "")
        text = "(kicad_symbol_lib\n\t(version 20241209)\n\t(generator \"kingfisher-build_libs\")\n\t(generator_version \"1.0\")\n"
        text += icm + "\n" + bmp + "\n" + neo_indented
        if not text.endswith("\n"):
            text += "\n"
        text += ")\n"
        (LIB / "Kingfisher.kicad_sym").write_text(text)
    else:
        (LIB / "Kingfisher.kicad_sym").write_text("\n".join(out) + "\n")


def write_lib_tables() -> None:
    (ROOT / "fp-lib-table").write_text(
        "(fp_lib_table\n"
        '  (version 7)\n'
        '  (lib (name "Kingfisher")(type "KiCad")(uri "${KIPRJMOD}/libs/Kingfisher.pretty")'
        '(options "")(descr "Kingfisher HAT footprints"))\n'
        ")\n"
    )
    (ROOT / "sym-lib-table").write_text(
        "(sym_lib_table\n"
        '  (version 7)\n'
        '  (lib (name "Kingfisher")(type "KiCad")(uri "${KIPRJMOD}/libs/Kingfisher.kicad_sym")'
        '(options "")(descr "Kingfisher HAT symbols"))\n'
        ")\n"
    )


def main() -> None:
    PRETTY.mkdir(parents=True, exist_ok=True)
    write_bmp581_footprint()
    write_symbols()
    write_lib_tables()
    print("Wrote symbols/footprints to", LIB)


if __name__ == "__main__":
    main()
