#!/usr/bin/env python3
"""Regenerate the 1:1 paper-fit PDFs for the cabin HAT.

Reads hardware/hat/kingfisher-hat.kicad_pcb and writes:

  hardware/hat/jlcpcb/kingfisher-hat-rev0.3-paper-fit.pdf
  hardware/hat/jlcpcb/kingfisher-hat-rev0.3-paper-fit-kicad-A3.pdf

The letter sheet is the mechanical overlay (outline, mounting holes, J1 and
J3 drills) plus a 50 mm scale bar. The A3 sheet is a KiCad plot of the
outline, courtyards, fab, silk, and user drawings. Both are true scale on
their physical page size. Outline, footprint, and courtyard strokes are
#404040 at 0.22 mm. Print scaling is set to None.

Does not modify the board, schematic, or any fab output. Requires KiCad 9
(kicad-cli) and python3-reportlab.

    python3 hardware/hat/scripts/paper_fit.py
"""

from __future__ import annotations

import math
import re
import shutil
import subprocess
import tempfile
import zlib
from pathlib import Path

from reportlab.lib.colors import Color, black
from reportlab.pdfbase.pdfdoc import ViewerPreferencesPDFDictionary
from reportlab.pdfgen import canvas

HAT = Path(__file__).resolve().parents[1]
PCB_PATH = HAT / "kingfisher-hat.kicad_pcb"
OUT_DIR = HAT / "jlcpcb"
SIMPLE_PDF = OUT_DIR / "kingfisher-hat-rev0.3-paper-fit.pdf"
A3_PDF = OUT_DIR / "kingfisher-hat-rev0.3-paper-fit-kicad-A3.pdf"

# #404040. 64/255 = 0.25098039, written with five decimals.
LINE_GREY = 64 / 255
LINE_GREY_PDF = f"{LINE_GREY:.5f}"
LINE_WIDTH_MM = 0.22

PT_PER_MM = 72.0 / 25.4
DECI_MIL_MM = 0.00254  # KiCad PDF user unit (1/10000 inch)

# Letter-sheet placement recovered from the Oct 26 print so the board
# lands in the same place: header edge (smaller KiCad Y) toward the top.
SHEET_TX = 182.556
SHEET_TY = 144.0
SHEET_FLIP = 165.528
LOCAL_X0 = 99.95
LOCAL_Y0 = 41.6517

MECH_LAYERS = {
    "Edge.Cuts",
    "F.CrtYd",
    "B.CrtYd",
    "F.Fab",
    "B.Fab",
    "F.SilkS",
    "B.SilkS",
    "Dwgs.User",
    "Cmts.User",
}
PLOT_LAYERS = ",".join(
    [
        "Edge.Cuts",
        "F.CrtYd",
        "B.CrtYd",
        "F.Fab",
        "B.Fab",
        "F.SilkS",
        "B.SilkS",
        "Dwgs.User",
        "Cmts.User",
    ]
)
GRAPHIC_HEADS = (
    "gr_line",
    "gr_arc",
    "gr_circle",
    "gr_rect",
    "gr_poly",
    "fp_line",
    "fp_arc",
    "fp_circle",
    "fp_rect",
    "fp_poly",
)

GREY = Color(LINE_GREY, LINE_GREY, LINE_GREY)
WARN_RED = Color(0.75, 0, 0)


def scan_form(text: str, open_at: int) -> int:
    """Return the index just past the form that opens at open_at."""
    if text[open_at] != "(":
        raise ValueError("scan_form start is not a parenthesis")
    i = open_at
    depth = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == '"':
            i += 1
            while i < n:
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == '"':
                    i += 1
                    break
                i += 1
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise ValueError("unbalanced s-expression")


def iter_forms(text: str, head: str):
    pattern = re.compile(r"\(" + re.escape(head) + r"(?![A-Za-z0-9_])")
    i = 0
    while True:
        match = pattern.search(text, i)
        if match is None:
            return
        j = match.start()
        prev = text[j - 1] if j else " "
        if prev not in " \n\t\r(":
            i = j + 1
            continue
        end = scan_form(text, j)
        yield text[j:end]
        i = end


def thicken_board_graphics(text: str) -> tuple[str, int]:
    """Raise mechanical-layer stroke widths in a copy of the board text."""
    pattern = re.compile(r"\((" + "|".join(GRAPHIC_HEADS) + r")(?![A-Za-z0-9_])")
    spans = []
    for match in pattern.finditer(text):
        prev = text[match.start() - 1] if match.start() else " "
        if prev not in " \n\t\r(":
            continue
        spans.append((match.start(), scan_form(text, match.start())))
    changed = 0
    for start, end in reversed(spans):
        block = text[start:end]
        layer = re.search(r'\(layer\s+"([^"]+)"\)', block)
        if layer is None or layer.group(1) not in MECH_LAYERS:
            continue

        def repl(width_match: re.Match[str]) -> str:
            nonlocal changed
            width = float(width_match.group(1))
            if width + 1e-9 >= LINE_WIDTH_MM:
                return width_match.group(0)
            changed += 1
            return f"(width {LINE_WIDTH_MM:.2f})"

        new_block, _ = re.subn(r"\(width\s+([0-9.]+)\)", repl, block, count=1)
        text = text[:start] + new_block + text[end:]
    return text, changed


def floats(pattern: str, block: str) -> tuple[float, ...]:
    match = re.search(pattern, block)
    if match is None:
        raise ValueError(f"missing {pattern} in {block[:80]!r}")
    return tuple(float(group) for group in match.groups() if group is not None)


def kicad_rotate(px: float, py: float, deg: float) -> tuple[float, float]:
    """Rotate a footprint-local point. KiCad Y grows downward; +angle is CCW."""
    theta = math.radians(deg)
    cos_t = math.cos(theta)
    sin_t = math.sin(theta)
    return (px * cos_t + py * sin_t, -px * sin_t + py * cos_t)


def circumcircle(a, b, c):
    (x1, y1), (x2, y2), (x3, y3) = a, b, c
    det = 2 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
    if abs(det) < 1e-9:
        return None
    ux = (
        (x1 * x1 + y1 * y1) * (y2 - y3)
        + (x2 * x2 + y2 * y2) * (y3 - y1)
        + (x3 * x3 + y3 * y3) * (y1 - y2)
    ) / det
    uy = (
        (x1 * x1 + y1 * y1) * (x3 - x2)
        + (x2 * x2 + y2 * y2) * (x1 - x3)
        + (x3 * x3 + y3 * y3) * (x2 - x1)
    ) / det
    radius = math.hypot(x1 - ux, y1 - uy)
    return ux, uy, radius


def arc_polyline(start, mid, end, tol: float = 0.02) -> list[tuple[float, float]]:
    circle = circumcircle(start, mid, end)
    if circle is None:
        return [start, end]
    cx, cy, radius = circle
    if radius < 1e-6:
        return [start, end]
    a0 = math.atan2(start[1] - cy, start[0] - cx)
    am = math.atan2(mid[1] - cy, mid[0] - cx)
    a1 = math.atan2(end[1] - cy, end[0] - cx)

    def ccw(p: float, q: float) -> float:
        return (q - p) % (2 * math.pi)

    if ccw(a0, am) <= ccw(a0, a1) + 1e-8:
        sweep = ccw(a0, a1)
        direction = 1.0
    else:
        sweep = ccw(a1, a0)
        direction = -1.0
    cos_arg = max(-1.0, min(1.0, 1.0 - tol / radius))
    step = 2 * math.acos(cos_arg) if radius > tol else math.pi / 16
    steps = max(1, math.ceil(sweep / max(step, 1e-4)))
    points = []
    for i in range(steps + 1):
        angle = a0 + direction * sweep * i / steps
        points.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    points[0] = start
    points[-1] = end
    return points


def graphic_points(kind: str, block: str) -> list[tuple[float, float]]:
    def xy(key: str) -> tuple[float, float]:
        x, y = floats(rf"\({key}\s+(-?[0-9.]+)\s+(-?[0-9.]+)\)", block)
        return x, y

    if kind == "gr_line":
        return [xy("start"), xy("end")]
    if kind == "gr_arc":
        return arc_polyline(xy("start"), xy("mid"), xy("end"))
    if kind == "gr_circle":
        center = xy("center")
        end = xy("end")
        radius = math.hypot(end[0] - center[0], end[1] - center[1])
        steps = 48
        return [
            (
                center[0] + radius * math.cos(2 * math.pi * i / steps),
                center[1] + radius * math.sin(2 * math.pi * i / steps),
            )
            for i in range(steps + 1)
        ]
    if kind == "gr_rect":
        x0, y0 = xy("start")
        x1, y1 = xy("end")
        return [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
    raise ValueError(kind)


def parse_board(text: str) -> tuple[list[list[tuple[float, float]]], list[tuple[float, float, float, str]]]:
    edges = []
    for kind in ("gr_line", "gr_arc", "gr_circle", "gr_rect"):
        for block in iter_forms(text, kind):
            if '(layer "Edge.Cuts")' not in block:
                continue
            edges.append(graphic_points(kind, block))

    holes = []
    for block in iter_forms(text, "footprint"):
        name = re.match(r'\(footprint\s+"([^"]+)"', block).group(1)
        ref_match = re.search(r'\(property\s+"Reference"\s+"([^"]+)"', block)
        ref = ref_match.group(1) if ref_match else ""
        wanted = ref in {"J1", "J3"} or ref.startswith("MH") or name.startswith("MountingHole")
        if not wanted:
            continue
        head_end = len(block)
        for marker in ("(property", "(pad", "(fp_"):
            at = block.find(marker)
            if at != -1:
                head_end = min(head_end, at)
        at_match = re.search(
            r"\(at\s+(-?[0-9.]+)\s+(-?[0-9.]+)(?:\s+(-?[0-9.]+))?\)",
            block[:head_end],
        )
        if at_match is None:
            raise ValueError(f"{ref or name} has no placement")
        fx = float(at_match.group(1))
        fy = float(at_match.group(2))
        rot = float(at_match.group(3) or 0)
        for pad in iter_forms(block, "pad"):
            if not re.search(r"\(pad\s+\"[^\"]*\"\s+(?:thru_hole|np_thru_hole)\b", pad):
                continue
            drill = re.search(r"\(drill\s+([0-9.]+)\)", pad)
            if drill is None:
                continue
            px, py = floats(r"\(at\s+(-?[0-9.]+)\s+(-?[0-9.]+)", pad)
            ax, ay = kicad_rotate(px, py, rot)
            holes.append((fx + ax, fy + ay, float(drill.group(1)), ref))
    return edges, holes


def check_geometry(edges, holes) -> None:
    pts = [p for seg in edges for p in seg]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    width = max(xs) - min(xs)
    height = max(ys) - min(ys)
    if abs(width - 65.0) > 0.02 or abs(height - 56.0) > 0.02:
        raise SystemExit(f"edge bbox is {width:.3f} x {height:.3f} mm, expected 65 x 56")
    if abs(min(xs) - 100.0) > 0.02 or abs(max(xs) - 165.0) > 0.02:
        raise SystemExit(f"edge X span {min(xs):.3f}..{max(xs):.3f}, expected 100..165")
    slot = [
        seg
        for seg in edges
        if len(seg) == 2
        and abs(seg[0][0] - seg[1][0]) < 0.02
        and abs(seg[0][0] - 144.0) < 0.05
        and abs(abs(seg[0][1] - seg[1][1]) - 15.0) < 0.05
    ]
    if not slot:
        raise SystemExit("internal slot edge at x=144 mm was not found")

    by_ref: dict[str, list[tuple[float, float, float]]] = {}
    for x, y, drill, ref in holes:
        by_ref.setdefault(ref, []).append((x, y, drill))
    j1 = by_ref.get("J1", [])
    j3 = by_ref.get("J3", [])
    mounts = [h for ref, group in by_ref.items() if ref.startswith("MH") for h in group]
    if len(j1) != 40 or len(j3) != 5 or len(mounts) != 4:
        raise SystemExit(f"hole counts J1={len(j1)} J3={len(j3)} MH={len(mounts)}")
    pin1 = min(j1, key=lambda h: (abs(h[0] - 108.37) + abs(h[1] - 48.77), h[1]))
    # Pin 1 is the pad at the footprint origin.
    p1 = [h for h in j1 if abs(h[0] - 108.37) < 0.02 and abs(h[1] - 48.77) < 0.02]
    p2 = [h for h in j1 if abs(h[0] - 108.37) < 0.02 and abs(h[1] - 46.23) < 0.02]
    if len(p1) != 1 or len(p2) != 1:
        raise SystemExit(f"J1 pin 1/2 not at the header origin, nearest {pin1}")
    j3_pin1 = [h for h in j3 if abs(h[0] - 153.5) < 0.02 and abs(h[1] - 78.0) < 0.02]
    if len(j3_pin1) != 1:
        raise SystemExit(f"J3 pin 1 not at (153.5, 78): {j3}")
    mx = sorted({round(h[0], 2) for h in mounts})
    my = sorted({round(h[1], 2) for h in mounts})
    if len(mx) != 2 or len(my) != 2:
        raise SystemExit(f"mounting holes are not a rectangle: {mounts}")
    if abs((mx[1] - mx[0]) - 58.0) > 0.02 or abs((my[1] - my[0]) - 49.0) > 0.02:
        raise SystemExit(f"mounting pitch {mx[1]-mx[0]:.3f} x {my[1]-my[0]:.3f}, expected 58 x 49")
    if any(abs(h[2] - 2.7) > 0.02 for h in mounts):
        raise SystemExit(f"mounting drill is not 2.7 mm: {mounts}")


def kicad_to_page(kx: float, ky: float) -> tuple[float, float]:
    local_x = kx - LOCAL_X0
    local_y = ky - LOCAL_Y0
    return (
        SHEET_TX + PT_PER_MM * local_x,
        SHEET_TY + SHEET_FLIP - PT_PER_MM * local_y,
    )


def draw_simple(edges, holes, path: Path) -> None:
    sheet = canvas.Canvas(str(path), pagesize=(612, 792))
    prefs = ViewerPreferencesPDFDictionary()
    prefs["PrintScaling"] = "None"
    sheet._doc.Catalog.ViewerPreferences = prefs
    sheet.setTitle("Kingfisher cabin HAT rev 0.3 — paper fit")
    sheet.setAuthor("westphae/kingfisher")
    sheet.setSubject("1:1 scale. Print at 100%. Do not fit to page.")

    sheet.setFillColor(black)
    sheet.setFont("Helvetica-Bold", 14)
    sheet.drawString(50.4, 745.2, "Kingfisher cabin HAT rev 0.3 — paper fit")
    sheet.setFillColor(WARN_RED)
    sheet.setFont("Helvetica-Bold", 11)
    sheet.drawString(50.4, 727.2, "PRINT AT 100% SCALE — do not “fit to page” or shrink.")
    sheet.setFillColor(black)
    sheet.setFont("Helvetica", 9)
    sheet.drawString(
        50.4,
        709.2,
        "Cut on the outline (notch + internal slot). Place on the real stack: X1200 → Pi 5 → Active Cooler →",
    )
    sheet.drawString(
        50.4,
        697.2,
        "M.2 HAT+ → extended GPIO (+9 mm) → this HAT (socket on bottom). Check hole alignment and clearance.",
    )
    sheet.drawString(
        50.4,
        685.2,
        "Official Pi HAT outline: 65 × 56 mm. M2.5 holes on 58 × 49 mm centers.",
    )

    sheet.setStrokeColor(GREY)
    sheet.setFillColor(GREY)
    sheet.setLineWidth(LINE_WIDTH_MM * PT_PER_MM)
    sheet.setLineCap(1)
    sheet.setLineJoin(1)
    for seg in edges:
        path_obj = sheet.beginPath()
        x0, y0 = kicad_to_page(*seg[0])
        path_obj.moveTo(x0, y0)
        for point in seg[1:]:
            x, y = kicad_to_page(*point)
            path_obj.lineTo(x, y)
        sheet.drawPath(path_obj, stroke=1, fill=0)
    for x, y, drill, _ref in holes:
        px, py = kicad_to_page(x, y)
        sheet.circle(px, py, (drill / 2) * PT_PER_MM, stroke=0, fill=1)

    bar_x = 50.4
    bar_y = 111.6
    bar_end = bar_x + 50.0 * PT_PER_MM
    sheet.setStrokeColor(black)
    sheet.setLineWidth(1.2)
    sheet.setLineCap(0)
    sheet.line(bar_x, bar_y, bar_end, bar_y)
    sheet.setFillColor(black)
    sheet.setFont("Helvetica", 8)
    sheet.drawString(50.4, 99.6, "50 mm scale bar — after printing this must measure 50.0 mm")
    sheet.drawString(
        50.4,
        79.6,
        "Mechanical checks this paper supports (DESIGN.md): outline, notch, slot, M2.5 hole positions vs the stack.",
    )
    sheet.drawString(
        50.4,
        68.6,
        "Still confirm on hardware: 9 mm pin engagement in the female socket; CR2032+Keystone 3002 lid clearance (~5–6 mm).",
    )
    sheet.save()


def iter_pdf_objects(data: bytes):
    i = 0
    while True:
        match = re.search(rb"(\d+)\s+0\s+obj\b", data[i:])
        if match is None:
            return
        start = i + match.start()
        num = int(match.group(1))
        j = i + match.end()
        window = data[j : j + 300]
        if b"stream" in window:
            stream_at = data.find(b"stream", j)
            endstream = data.find(b"endstream", stream_at)
            endobj = data.find(b"endobj", endstream)
        else:
            endobj = data.find(b"endobj", j)
        end = endobj + len(b"endobj")
        yield num, start, end, data[start:end]
        i = end


def pdf_stream_bytes(obj: bytes) -> bytes:
    match = re.search(rb"stream\r?\n", obj)
    if match is None:
        raise ValueError("object has no stream")
    start = match.end()
    end = obj.find(b"endstream", start)
    raw = obj[start:end]
    if raw.endswith(b"\r\n"):
        raw = raw[:-2]
    elif raw.endswith(b"\n"):
        raw = raw[:-1]
    if b"/FlateDecode" in obj[: match.start()]:
        return zlib.decompress(raw)
    return raw


def recolor_operators(content: str) -> str:
    grey = LINE_GREY_PDF

    def repl(match: re.Match[str]) -> str:
        return f"{grey} {grey} {grey} {match.group(1)}"

    return re.sub(
        r"(?<![\d.])0(?:\.0+)?\s+0(?:\.0+)?\s+0(?:\.0+)?\s+(rg|RG)\b",
        repl,
        content,
    )


def thicken_pdf_strokes(content: str) -> str:
    """Raise the width in effect for stroke operators to 0.22 mm.

    KiCad's PDF user unit is 1/10000 inch. Hairline widths used only to
    fill a shape are left alone because a later width overrides them
    before the paint operator; this edits the width that stroke uses.
    """
    token_re = re.compile(r"\s+|\((?:\\.|[^\\)])*\)|[^ \t\r\n()]+")
    spans = [(m.group(), m.start(), m.end()) for m in token_re.finditer(content)]
    last_num = None
    edits = []
    target = f"{LINE_WIDTH_MM / DECI_MIL_MM:.5f}"
    for i, (tok, _a, _b) in enumerate(spans):
        if tok == "w":
            j = i - 1
            while j >= 0 and spans[j][0].strip() == "":
                j -= 1
            last_num = j
        elif tok in {"S", "s"} and last_num is not None:
            num_tok, a, b = spans[last_num]
            try:
                width_mm = float(num_tok) * DECI_MIL_MM
            except ValueError:
                continue
            if 0.005 <= width_mm < LINE_WIDTH_MM - 1e-4:
                edits.append((a, b, target))
    for a, b, new in reversed(edits):
        content = content[:a] + new + content[b:]
    return content


def pdf_literal(text: str) -> str:
    raw = text.encode("cp1252").decode("latin1")
    return "(" + raw.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") + ")"


def scale_bar_ops() -> str:
    x0 = 20.0 * PT_PER_MM
    x1 = x0 + 50.0 * PT_PER_MM
    y = 25.0 * PT_PER_MM
    tick = 3.0 * PT_PER_MM
    return (
        "%SCALE\n"
        "0 0 0 RG\n"
        "1.2 w\n"
        "0 J\n"
        f"{x0:.4f} {y:.4f} m {x1:.4f} {y:.4f} l S\n"
        f"{x0:.4f} {y - tick / 2:.4f} m {x0:.4f} {y + tick / 2:.4f} l S\n"
        f"{x1:.4f} {y - tick / 2:.4f} m {x1:.4f} {y + tick / 2:.4f} l S\n"
        "BT\n"
        "/KicadFont 9 Tf\n"
        "0 0 0 rg\n"
        f"1 0 0 1 {x0:.4f} {y + 8:.4f} Tm\n"
        + pdf_literal("50 mm scale bar — after printing this must measure 50.0 mm")
        + " Tj\n"
        "ET\n"
        "BT\n"
        "/KicadFontB 11 Tf\n"
        "0.75 0 0 rg\n"
        f"1 0 0 1 {x0:.4f} {y - 18:.4f} Tm\n"
        + pdf_literal("PRINT AT 100% SCALE — do not fit to page.")
        + " Tj\n"
        "ET\n"
    )


def incremental_update(data: bytes, updates: dict[int, bytes]) -> bytes:
    start_match = re.search(rb"startxref\s+(\d+)\s+%%EOF\s*$", data)
    if start_match is None:
        raise ValueError("PDF has no final startxref")
    old_start = int(start_match.group(1))
    trailer = data[data.rfind(b"trailer") : data.rfind(b"startxref")]
    size = int(re.search(rb"/Size\s+(\d+)", trailer).group(1))
    info = re.search(rb"/Info\s+(\d+\s+\d+\s+R)", trailer)
    root = int(re.search(rb"/Root\s+(\d+)\s+0\s+R", trailer).group(1))
    out = bytearray(data)
    if not out.endswith(b"\n"):
        out += b"\n"
    offsets = {}
    for num in sorted(updates):
        offsets[num] = len(out)
        body = updates[num]
        out += f"{num} 0 obj\n".encode()
        out += body
        if not body.endswith(b"\n"):
            out += b"\n"
        out += b"endobj\n"
    xref_at = len(out)
    out += b"xref\n"
    for num in sorted(offsets):
        out += f"{num} 1\n".encode()
        out += f"{offsets[num]:010d} 00000 n \n".encode()
    out += b"trailer\n<<\n"
    out += f"/Size {size}\n".encode()
    out += f"/Prev {old_start}\n".encode()
    out += f"/Root {root} 0 R\n".encode()
    if info:
        out += b"/Info " + info.group(1) + b"\n"
    out += b">>\n"
    out += f"startxref\n{xref_at}\n%%EOF\n".encode()
    return bytes(out)


def postprocess_a3(pdf: bytes) -> bytes:
    objects = {num: body for num, _start, _end, body in iter_pdf_objects(pdf)}
    page_num = None
    contents_num = None
    catalog_num = None
    catalog_body = None
    for num, body in objects.items():
        if re.search(rb"/Type\s*/Page\b", body) and b"/Contents" in body and page_num is None:
            page_num = num
            contents_num = int(re.search(rb"/Contents\s+(\d+)\s+0\s+R", body).group(1))
        if re.search(rb"/Type\s*/Catalog\b", body):
            catalog_num = num
            catalog_body = body
    stream_body = objects.get(contents_num) if contents_num is not None else None
    if page_num is None or contents_num is None or catalog_num is None or stream_body is None:
        raise ValueError("KiCad PDF is missing a page, contents, or catalog")
    content = pdf_stream_bytes(stream_body).decode("latin1")
    if not content.startswith("0.0072 0 0 0.0072"):
        raise ValueError("KiCad PDF scale is not 1 deci-mil per user unit")
    content = thicken_pdf_strokes(recolor_operators(content))
    content = "q\n" + content + "\nQ\n" + scale_bar_ops()
    compressed = zlib.compress(content.encode("latin1"))
    stream = (
        f"<< /Length {len(compressed)} /Filter /FlateDecode >>\n".encode()
        + b"stream\n"
        + compressed
        + b"\nendstream"
    )
    catalog_dict = catalog_body.split(b"obj", 1)[1]
    catalog_dict = catalog_dict.rsplit(b"endobj", 1)[0].strip()
    text = catalog_dict.decode("latin1")
    text = text.replace("/Version /1.5", "/Version /1.6")
    if "/ViewerPreferences" not in text:
        text = text[:-2] + (
            " /ViewerPreferences << /PrintScaling /None >>"
            f" /OpenAction [{page_num} 0 R /XYZ null null 1] >>"
        )
    return incremental_update(pdf, {contents_num: stream, catalog_num: text.encode("latin1")})


def plot_a3(board_text: str, path: Path) -> None:
    kicad = shutil.which("kicad-cli")
    if kicad is None:
        raise SystemExit("kicad-cli is not installed")
    with tempfile.TemporaryDirectory(prefix="kingfisher-paper-fit-") as tmp:
        pcb = Path(tmp) / "kingfisher-hat.kicad_pcb"
        thickened, count = thicken_board_graphics(board_text)
        if count < 50:
            raise SystemExit(f"only {count} mechanical strokes were thickened")
        pcb.write_text(thickened)
        raw = Path(tmp) / "a3.pdf"
        proc = subprocess.run(
            [
                kicad,
                "pcb",
                "export",
                "pdf",
                "--black-and-white",
                "--mode-single",
                "--drill-shape-opt",
                "2",
                "--layers",
                PLOT_LAYERS,
                "-o",
                str(raw),
                str(pcb),
            ],
            cwd=tmp,
            check=False,
            text=True,
            capture_output=True,
        )
        if proc.returncode != 0:
            raise SystemExit(f"kicad-cli failed:\n{proc.stdout}\n{proc.stderr}")
        path.write_bytes(postprocess_a3(raw.read_bytes()))


def decode_reportlab_streams(data: bytes) -> str:
    from reportlab.pdfbase.pdfutils import asciiBase85Decode

    texts = []
    for match in re.finditer(rb"stream\r?\n", data):
        start = match.end()
        end = data.find(b"endstream", start)
        raw = data[start:end]
        if raw.endswith(b"\r\n"):
            raw = raw[:-2]
        elif raw.endswith(b"\n"):
            raw = raw[:-1]
        head = data[max(0, match.start() - 200) : match.start()]
        try:
            if b"ASCII85Decode" in head:
                raw = asciiBase85Decode(raw)
            if b"FlateDecode" in head:
                raw = zlib.decompress(raw)
        except Exception:
            continue
        if raw.startswith(b"q") or b" TJ" in raw or b" Tj" in raw or b" m" in raw or raw.startswith(b"BT"):
            texts.append(raw.decode("latin1", errors="replace"))
    if not texts:
        raise ValueError("no drawing stream in the letter PDF")
    return "\n".join(texts)


def decode_kicad_content(data: bytes) -> str:
    # Prefer the updated contents object from the incremental section: the
    # last Contents reference still points at the same object number, and
    # iter_pdf_objects yields the original then the update. Keep the last.
    contents_num = None
    streams: dict[int, bytes] = {}
    for num, _s, _e, body in iter_pdf_objects(data):
        if re.search(rb"/Type\s*/Page\b", body) and b"/Contents" in body:
            contents_num = int(re.search(rb"/Contents\s+(\d+)\s+0\s+R", body).group(1))
        if b"stream" in body[:400]:
            try:
                streams[num] = pdf_stream_bytes(body)
            except Exception:
                continue
    if contents_num is None or contents_num not in streams:
        raise ValueError("A3 PDF contents stream was not found")
    return streams[contents_num].decode("latin1")


def segments(content: str) -> list[tuple[float, float, float, float]]:
    found = []
    nums = r"(-?[0-9.]+)"
    for match in re.finditer(
        rf"{nums}\s+{nums}\s+m\s+{nums}\s+{nums}\s+l",
        content,
    ):
        found.append(tuple(float(match.group(i)) for i in range(1, 5)))
    return found


def verify_simple(data: bytes) -> dict[str, float]:
    if b"/PrintScaling /None" not in data:
        raise SystemExit("letter PDF is missing PrintScaling /None")
    if b"/MediaBox [ 0 0 612 792 ]" not in data and b"/MediaBox [0 0 612 792]" not in data:
        raise SystemExit("letter PDF MediaBox is not US Letter")
    content = decode_reportlab_streams(data)
    width_match = re.search(r"([0-9.]+)\s+w", content)
    if width_match is None:
        raise SystemExit("letter PDF has no stroke width")
    stroke_mm = float(width_match.group(1)) / PT_PER_MM
    if not 0.20 <= stroke_mm <= 0.25:
        raise SystemExit(f"letter outline stroke is {stroke_mm:.3f} mm, expected 0.22")
    if LINE_GREY_PDF not in content and f"{LINE_GREY:.3f}" not in content:
        # ReportLab may round the channel. Accept anything near #404040.
        channels = re.findall(r"([0-9.]+)\s+([0-9.]+)\s+([0-9.]+)\s+RG", content)
        greys = [tuple(float(c) for c in ch) for ch in channels if ch[0] == ch[1] == ch[2]]
        if not any(abs(g[0] - LINE_GREY) < 0.01 for g in greys):
            raise SystemExit(f"letter PDF stroke color is not #404040: {greys[:4]}")
    bars = [
        (x1, y1, x2, y2)
        for x1, y1, x2, y2 in segments(content)
        if abs(y1 - y2) < 0.05 and abs(y1 - 111.6) < 0.2
    ]
    if not bars:
        raise SystemExit("letter PDF scale bar was not found")
    bar_mm = abs(bars[0][2] - bars[0][0]) / PT_PER_MM
    if abs(bar_mm - 50.0) > 0.05:
        raise SystemExit(f"letter scale bar is {bar_mm:.3f} mm")
    # Board paths sit above the scale bar. Their X span is the 65 mm outline.
    xs = []
    ys = []
    for x1, y1, x2, y2 in segments(content):
        if min(y1, y2) > 130:
            xs.extend((x1, x2))
            ys.extend((y1, y2))
    # Bezier control points of the arcs are also in the stream as 'c' coords.
    for match in re.finditer(
        r"(-?[0-9.]+)\s+(-?[0-9.]+)\s+m|"
        r"(-?[0-9.]+)\s+(-?[0-9.]+)\s+l|"
        r"(-?[0-9.]+)\s+(-?[0-9.]+)\s+(-?[0-9.]+)\s+(-?[0-9.]+)\s+(-?[0-9.]+)\s+(-?[0-9.]+)\s+c",
        content,
    ):
        nums = [float(g) for g in match.groups() if g is not None]
        pairs = list(zip(nums[0::2], nums[1::2]))
        for x, y in pairs:
            if y > 130:
                xs.append(x)
                ys.append(y)
    if not xs:
        raise SystemExit("letter PDF board geometry was not found")
    board_w = (max(xs) - min(xs)) / PT_PER_MM
    board_h = (max(ys) - min(ys)) / PT_PER_MM
    if abs(board_w - 65.0) > 0.08 or abs(board_h - 56.0) > 0.08:
        raise SystemExit(f"letter board measures {board_w:.3f} x {board_h:.3f} mm, expected 65 x 56")
    return {"stroke_mm": stroke_mm, "scale_bar_mm": bar_mm, "board_w_mm": board_w, "board_h_mm": board_h}


def verify_a3(data: bytes) -> dict[str, float]:
    if b"/PrintScaling /None" not in data:
        raise SystemExit("A3 PDF is missing PrintScaling /None")
    if b"/MediaBox [0 0 1190.52 841.896]" not in data and b"/MediaBox [ 0 0 1190.52 841.896 ]" not in data:
        raise SystemExit("A3 PDF MediaBox is not KiCad's A3 page")
    content = decode_kicad_content(data)
    board, _scale = content.split("%SCALE\n", 1)
    if "0 0 0 RG" in board or "0 0 0 rg" in board:
        raise SystemExit("A3 board drawing still has black paint")
    if LINE_GREY_PDF not in board:
        raise SystemExit("A3 board drawing is missing #404040")
    stroke_mm = []
    token_re = re.compile(r"\s+|\((?:\\.|[^\\)])*\)|[^ \t\r\n()]+")
    spans = [m.group() for m in token_re.finditer(board)]
    last = None
    for i, tok in enumerate(spans):
        if tok == "w":
            j = i - 1
            while j >= 0 and spans[j].strip() == "":
                j -= 1
            last = spans[j]
        elif tok in {"S", "s"} and last is not None:
            try:
                stroke_mm.append(float(last) * DECI_MIL_MM)
            except ValueError:
                pass
    real = [w for w in stroke_mm if w >= 0.005]
    if not real or min(real) < 0.20 - 1e-3:
        raise SystemExit(f"A3 stroke widths include a hairline: {sorted(set(round(w, 4) for w in real))[:8]}")
    if not any(abs(w - LINE_WIDTH_MM) < 0.015 for w in real):
        raise SystemExit(f"A3 strokes are not 0.22 mm: {sorted(set(round(w, 4) for w in real))}")
    # Right edge centerline: x = 165 mm, straight run y = 47..97 mm (50 mm).
    # PDF Y is page-bottom-up, so KiCad y=97 and y=47 land 50 mm apart.
    x_165 = 165.0 / DECI_MIL_MM
    vertical = [
        abs(y2 - y1) * DECI_MIL_MM
        for x1, y1, x2, y2 in segments(board)
        if abs(x1 - x2) < 1 and abs(x1 - x_165) < (0.2 / DECI_MIL_MM) and abs(y2 - y1) * DECI_MIL_MM > 10
    ]
    if not any(abs(length - 50.0) < 0.08 for length in vertical):
        raise SystemExit(f"A3 right edge is not 50 mm: {vertical[:6]}")
    # Top edge of the outline, KiCad y=100, from x=103 to x=162 (59 mm).
    horizontal = [
        abs(x2 - x1) * DECI_MIL_MM
        for x1, _y1, x2, _y2 in segments(board)
        if abs(_y1 - _y2) < 1 and abs(abs(x2 - x1) * DECI_MIL_MM - 59.0) < 0.08
    ]
    if not horizontal:
        raise SystemExit("A3 top edge (59 mm) was not found")
    bars = [
        abs(x2 - x1) / PT_PER_MM
        for x1, y1, x2, y2 in segments(content.split("%SCALE\n", 1)[1])
        if abs(y1 - y2) < 0.05 and abs(abs(x2 - x1) / PT_PER_MM - 50) < 1
    ]
    if not bars or abs(bars[0] - 50.0) > 0.05:
        raise SystemExit(f"A3 scale bar is {bars}")
    return {
        "stroke_mm": min(w for w in real if abs(w - LINE_WIDTH_MM) < 0.015),
        "edge_50_mm": min(vertical, key=lambda length: abs(length - 50)),
        "scale_bar_mm": bars[0],
    }


def main() -> None:
    board = PCB_PATH.read_text()
    edges, holes = parse_board(board)
    check_geometry(edges, holes)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    draw_simple(edges, holes, SIMPLE_PDF)
    plot_a3(board, A3_PDF)
    simple = verify_simple(SIMPLE_PDF.read_bytes())
    a3 = verify_a3(A3_PDF.read_bytes())
    print(f"wrote {SIMPLE_PDF}")
    print(f"wrote {A3_PDF}")
    print(
        "line color #404040 "
        f"({LINE_GREY_PDF} {LINE_GREY_PDF} {LINE_GREY_PDF}) "
        f"width {LINE_WIDTH_MM:.2f} mm"
    )
    print(
        "letter: "
        f"outline stroke {simple['stroke_mm']:.3f} mm, "
        f"board {simple['board_w_mm']:.3f} x {simple['board_h_mm']:.3f} mm, "
        f"scale bar {simple['scale_bar_mm']:.3f} mm"
    )
    print(
        "A3: "
        f"stroke {a3['stroke_mm']:.3f} mm, "
        f"right edge {a3['edge_50_mm']:.3f} mm, "
        f"scale bar {a3['scale_bar_mm']:.3f} mm"
    )


if __name__ == "__main__":
    main()
