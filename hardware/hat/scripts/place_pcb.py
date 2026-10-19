#!/usr/bin/env python3
"""Place rev 0.3 footprints on the HAT outline. Does not route copper.

J1 stays on the bottom, at the template coordinate that mates with the
extended Pi header. Everything else is on top. RF is intentionally unrouted.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pcbnew

ROOT = Path(__file__).resolve().parents[1]
BOARD = ROOT / "kingfisher-hat.kicad_pcb"
NETLIST = Path("/tmp/kingfisher-hat.net")
FP_ROOT = Path("/usr/share/kicad/footprints")
LOCAL_FP = ROOT / "libs" / "Kingfisher.pretty"

# ref -> (x_mm, y_mm, rotation_deg). All of these are on the top.
# J1 is the template bottom socket and is not in this table: moving it
# breaks the mate with the extended Pi header.
PLACE = {
    "U1": (112.0, 58.0, 0),
    "C1": (116.5, 57.2, 0),
    "C2": (116.5, 59.4, 0),
    "U5": (121.5, 58.0, 0),
    "C11": (125.5, 58.0, 0),
    "U3": (130.5, 58.0, 0),
    "C5": (136.2, 56.6, 0),
    "C6": (136.2, 59.2, 0),
    "C7": (139.4, 58.0, 0),
    "U2": (112.0, 65.5, 0),
    "C3": (116.5, 64.8, 0),
    "C4": (116.5, 66.6, 0),
    "R7": (119.6, 64.6, 90),
    "R8": (119.6, 67.0, 90),
    "BT2": (125.0, 66.0, 0),
    "C9": (130.2, 64.2, 90),
    "R4": (130.2, 68.0, 90),
    "D1": (134.5, 64.2, 0),
    "C8": (136.2, 61.8, 0),
    "D2": (133.8, 67.8, 0),
    "R6": (137.0, 67.8, 90),
    "R1": (139.2, 63.5, 90),
    "R2": (139.2, 67.6, 90),
    "U4": (148.5, 61.5, 180),
    # U.FL sits far enough right of the module for a short 50 ohm run, with the
    # bias ferrite's RF pad beside that run rather than several millimetres off it.
    "J2": (161.2, 55.6, 0),
    "FB1": (157.7, 57.05, -90),
    "R5": (157.4, 59.7, 0),
    "C10": (160.0, 60.5, 0),
    "R3": (158.2, 66.5, 0),
    "BT1": (124.0, 83.0, 0),
    "J3": (153.5, 78.0, 0),
    "TP1": (109.0, 53.3, 0),
    "TP2": (112.3, 53.3, 0),
    "TP3": (115.6, 53.3, 0),
    "TP4": (118.9, 53.3, 0),
    "TP5": (122.2, 53.3, 0),
    "TP6": (125.5, 53.3, 0),
    "TP7": (128.8, 53.3, 0),
    "TP8": (132.1, 53.3, 0),
    "TP9": (149.2, 78.0, 0),
    "TP10": (149.2, 81.2, 0),
    "TP11": (149.2, 84.4, 0),
    "TP12": (149.2, 87.6, 0),
}

# Board features the courtyards must stay clear of. Coordinates are mm.
NOTCH = (99.5, 63.2, 105.3, 80.8)
SLOT = (143.5, 79.5, 146.5, 97.5)
HEADER = (106.8, 44.9, 157.8, 50.5)
HOLES = ((103.5, 47.5), (161.5, 47.5), (103.5, 96.5), (161.5, 96.5))
HOLE_R = 2.2


def parse_netlist(path: Path) -> tuple[dict[str, tuple[str, str]], dict[tuple[str, str], str]]:
    text = path.read_text()
    parts: dict[str, tuple[str, str]] = {}
    section = text.split("(components", 1)[1].split("(libparts", 1)[0]
    for match in re.finditer(
        r'\(comp \(ref "([^"]+)"\)\s+\(value "([^"]*)"\)\s+\(footprint "([^"]*)"\)',
        section,
    ):
        parts[match.group(1)] = (match.group(2), match.group(3))
    pins: dict[tuple[str, str], str] = {}
    current = None
    for match in re.finditer(
        r'\(net \(code "[^"]*"\) \(name "((?:\\.|[^"\\])*)"\)|\(node \(ref "([^"]+)"\) \(pin "([^"]+)"\)',
        text,
    ):
        if match.group(1) is not None:
            current = match.group(1)
            if current.startswith("/"):
                current = current[1:]
        else:
            pins[(match.group(2), match.group(3))] = current
    return parts, pins


def load_footprint(fp_id: str):
    lib, name = fp_id.split(":", 1)
    path = LOCAL_FP if lib == "Kingfisher" else FP_ROOT / f"{lib}.pretty"
    plug = pcbnew.GetPluginForPath(str(path))
    fp = plug.FootprintLoad(str(path), name)
    if fp is None:
        raise SystemExit(f"could not load {fp_id} from {path}")
    return fp


def courtyard_mm(fp) -> tuple[float, float, float, float]:
    layer = pcbnew.B_CrtYd if fp.IsFlipped() else pcbnew.F_CrtYd
    box = fp.GetCourtyard(layer).BBox()
    return (
        pcbnew.ToMM(box.GetLeft()),
        pcbnew.ToMM(box.GetTop()),
        pcbnew.ToMM(box.GetRight()),
        pcbnew.ToMM(box.GetBottom()),
    )


def overlaps(a, b) -> bool:
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def hits_circle(box, cx, cy, radius) -> bool:
    nx = min(max(cx, box[0]), box[2])
    ny = min(max(cy, box[1]), box[3])
    return (nx - cx) ** 2 + (ny - cy) ** 2 < radius ** 2


def assign_nets(board, fp, pins) -> None:
    ref = fp.GetReference()
    for pad in fp.Pads():
        net_name = pins.get((ref, pad.GetNumber()))
        if not net_name:
            continue
        net = board.FindNet(net_name)
        if net is None:
            board.Add(pcbnew.NETINFO_ITEM(board, net_name))
            net = board.FindNet(net_name)
        pad.SetNet(net)


def main() -> int:
    parts, pins = parse_netlist(NETLIST)
    if len(parts) < 40:
        raise SystemExit(f"netlist parsed only {len(parts)} parts")
    missing = [ref for ref in parts if ref not in PLACE and ref != "J1"]
    extra = [ref for ref in PLACE if ref not in parts]
    if missing or extra:
        raise SystemExit(f"placement mismatch missing={missing} extra={extra}")

    board = pcbnew.LoadBoard(str(BOARD))
    # pcbnew frees a removed footprint if Python drops it, and that corrupts
    # the footprint-plugin object. Hold the old parts until after Save.
    removed = []
    for fp in list(board.GetFootprints()):
        ref = fp.GetReference()
        if ref.startswith("MH") or ref == "J1":
            continue
        board.Remove(fp)
        removed.append(fp)

    placed = []
    for ref, (value, footprint) in parts.items():
        if ref == "J1":
            continue
        x, y, rot = PLACE[ref]
        fp = load_footprint(footprint)
        fp.SetReference(ref)
        fp.SetValue(value)
        if not ref.startswith("TP"):
            fp.Value().SetVisible(False)
        if ref[0] in "CRT" or ref.startswith("FB"):
            fp.Reference().SetLayer(pcbnew.F_Fab)
        fp.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y)))
        fp.SetOrientationDegrees(rot)
        if ref in {"R7", "R8"}:
            fp.SetDNP(True)
        assign_nets(board, fp, pins)
        board.Add(fp)
        placed.append((ref, courtyard_mm(fp)))

    j1 = next(fp for fp in board.GetFootprints() if fp.GetReference() == "J1")
    assign_nets(board, j1, pins)
    pad1 = j1.FindPadByNumber("1")
    pad40 = j1.FindPadByNumber("40")
    print(
        "J1 pad1",
        round(pcbnew.ToMM(pad1.GetPosition().x), 2),
        round(pcbnew.ToMM(pad1.GetPosition().y), 2),
        "pad40",
        round(pcbnew.ToMM(pad40.GetPosition().x), 2),
        round(pcbnew.ToMM(pad40.GetPosition().y), 2),
        "layer",
        j1.GetLayer(),
    )

    problems = []
    for i, (ra, ba) in enumerate(placed):
        for rb, bb in placed[i + 1 :]:
            if overlaps(ba, bb):
                problems.append(f"courtyard {ra} vs {rb}")
        if overlaps(ba, NOTCH):
            problems.append(f"{ra} hits the left notch")
        if overlaps(ba, SLOT):
            problems.append(f"{ra} hits the GPIO slot")
        if overlaps(ba, HEADER):
            problems.append(f"{ra} hits the header keepout")
        if ba[0] < 100.4 or ba[2] > 164.6 or ba[1] < 44.4 or ba[3] > 99.6:
            problems.append(f"{ra} leaves the board {ba}")
        for hx, hy in HOLES:
            if hits_circle(ba, hx, hy, HOLE_R):
                problems.append(f"{ra} hits mounting hole ({hx}, {hy})")
    if problems:
        print("placement problems:")
        for line in problems:
            print(" ", line)
        return 1

    u4 = next(fp for fp in board.GetFootprints() if fp.GetReference() == "U4")
    j2 = next(fp for fp in board.GetFootprints() if fp.GetReference() == "J2")
    rf = u4.FindPadByNumber("11").GetPosition()
    ant = j2.FindPadByNumber("1").GetPosition()
    print(
        "RF pad",
        round(pcbnew.ToMM(rf.x), 2),
        round(pcbnew.ToMM(rf.y), 2),
        "U.FL",
        round(pcbnew.ToMM(ant.x), 2),
        round(pcbnew.ToMM(ant.y), 2),
        "gap",
        round(pcbnew.ToMM(ant.x - rf.x), 2),
    )
    board.Save(str(BOARD))
    text = BOARD.read_text()
    text2, n = re.subn(
        r'\(gr_text "[^"]*"\n\t\t\(at [\d.]+ [\d.]+ 0\)\n\t\t\(layer "F.SilkS"\)\n\t\t\(uuid "4abecebb-da74-4ca0-8dd0-6f4b35b17014"\)',
        '(gr_text "rev 0.3, placed, not routed"\n\t\t(at 124 96.6 0)\n\t\t(layer "F.SilkS")\n\t\t(uuid "4abecebb-da74-4ca0-8dd0-6f4b35b17014")',
        text,
        count=1,
    )
    if n != 1:
        raise SystemExit("silk title not found after save")
    BOARD.write_text(text2)
    print("saved", BOARD, "removed", len(removed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
