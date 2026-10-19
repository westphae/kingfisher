#!/usr/bin/env python3
"""Route the rev 0.3 HAT and write the JLCPCB 4-layer stackup.

The RF net is a straight microstrip on top over a solid In1 ground plane,
with a short bias stub up to the ferrite. Its width is the Hammerstad-Jensen
50 ohm result for the dielectric JLCPCB specifies for an outer trace on
JLC04161H-7628: 7.1 mil, Er 4.4, 1 oz. The laminated prepreg is still the
0.2104 mm sheet in the stackup; 7.1 mil is the pressed height they tell you
to design to. Every other net is a clearance-checked orthogonal route.
Ground is the two inner planes plus outer pours, pulled 0.3 mm off the edge.

Run place_pcb.py first. This script deletes tracks and its own zones, then
routes again, so it can be repeated.
"""
from __future__ import annotations

import heapq
import math
from pathlib import Path

import pcbnew

ROOT = Path(__file__).resolve().parents[1]
BOARD = ROOT / "kingfisher-hat.kicad_pcb"

# JLCPCB JLC04161H-7628, 1 oz outer / 0.5 oz inner, as published on
# https://jlcpcb.com/impedance for a 1.6 mm 4-layer board. Copper + dielectric
# sums to 1.5862 mm; JLCPCB sells that build as 1.6 mm.
STACK_SUM_MM = 0.035 + 0.2104 + 0.0152 + 1.065 + 0.0152 + 0.2104 + 0.035

# Outer microstrip design dielectric. The 7628 sheet is 0.2104 mm nominal.
# JLCPCB's impedance page says to use 7.1 mil (0.18034 mm) and Er 4.4 for a
# trace on the top or the bottom of this stackup.
RF_H_MM = 7.1 * 0.0254
RF_ER = 4.4
RF_T_MM = 1.4 * 0.0254
RF_W_MM, RF_Z0 = None, None  # filled by microstrip_width()

CLEARANCE = 0.15
VIA_D = 0.6
VIA_DRILL = 0.3
POWER = {"+3V3", "+5V", "+3V3_GPS"}
# Kept free in front of a fanout via so a later net cannot cross the only
# way off the land. Wide enough for a 0.35 mm track to clear a 0.2 mm one.
FANOUT_RESERVE = 1.6
FANOUT_RESERVE_HALF = 0.45

X0, Y0 = 99.0, 43.0
X1, Y1 = 166.0, 101.0
STEP = 0.05
NX = int(round((X1 - X0) / STEP))
NY = int(round((Y1 - Y0) / STEP))

# Official HAT template keepout over the Pi PoE pads. Bottom only.
POE = (159.0, 51.14, 164.0, 56.14)


def microstrip_z0(width: float) -> float:
    """Hammerstad-Jensen 1980, with their mild thickness correction. No soldermask.

    Mask over this geometry lowers the impedance by about 1–2 ohm, which is
    inside a normal GPS match and inside JLCPCB's ±10% controlled-impedance
    tolerance. The width is chosen so the bare trace is 50 ohm.
    """
    eta = 376.730313461
    u = width / RF_H_MM
    du = (RF_T_MM / (2 * math.pi * RF_H_MM)) * math.log(
        1 + (4 * math.e * RF_H_MM / RF_T_MM) * math.tanh(math.sqrt(6.517 * u)) ** 2
    )
    u += du
    a = (
        1
        + (1 / 49) * math.log((u**4 + (u / 52) ** 2) / (u**4 + 0.432))
        + (1 / 18.7) * math.log(1 + (u / 18.1) ** 3)
    )
    b = 0.564 * ((RF_ER - 0.9) / (RF_ER + 3)) ** 0.053
    eeff = (RF_ER + 1) / 2 + (RF_ER - 1) / 2 * (1 + 10 / u) ** (-a * b)
    f = 6 + (2 * math.pi - 6) * math.exp(-(30.666 / u) ** 0.7528)
    return eta / (2 * math.pi * math.sqrt(eeff)) * math.log(f / u + math.sqrt(1 + (2 / u) ** 2))


def microstrip_width(target: float = 50.0) -> tuple[float, float]:
    lo, hi = 0.15, 0.8
    for _ in range(50):
        mid = (lo + hi) / 2
        if microstrip_z0(mid) > target:
            lo = mid
        else:
            hi = mid
    width = round((lo + hi) / 2, 2)  # 0.01 mm, finer than JLCPCB's etch tolerance
    return width, microstrip_z0(width)


def apply_stackup(path: Path) -> None:
    text = path.read_text()
    start = text.find("\t(stackup")
    if start < 0:
        raise SystemExit("stackup block not found")
    block = """\t(stackup
\t\t\t(layer "F.SilkS"
\t\t\t\t(type "Top Silk Screen")
\t\t\t)
\t\t\t(layer "F.Paste"
\t\t\t\t(type "Top Solder Paste")
\t\t\t)
\t\t\t(layer "F.Mask"
\t\t\t\t(type "Top Solder Mask")
\t\t\t\t(color "Green")
\t\t\t\t(thickness 0.015)
\t\t\t)
\t\t\t(layer "F.Cu"
\t\t\t\t(type "copper")
\t\t\t\t(thickness 0.035)
\t\t\t)
\t\t\t(layer "dielectric 1"
\t\t\t\t(type "prepreg")
\t\t\t\t(thickness 0.2104)
\t\t\t\t(material "7628")
\t\t\t\t(epsilon_r 4.4)
\t\t\t\t(loss_tangent 0.02)
\t\t\t)
\t\t\t(layer "In1.Cu"
\t\t\t\t(type "copper")
\t\t\t\t(thickness 0.0152)
\t\t\t)
\t\t\t(layer "dielectric 2"
\t\t\t\t(type "core")
\t\t\t\t(thickness 1.065)
\t\t\t\t(material "FR4")
\t\t\t\t(epsilon_r 4.6)
\t\t\t\t(loss_tangent 0.02)
\t\t\t)
\t\t\t(layer "In2.Cu"
\t\t\t\t(type "copper")
\t\t\t\t(thickness 0.0152)
\t\t\t)
\t\t\t(layer "dielectric 3"
\t\t\t\t(type "prepreg")
\t\t\t\t(thickness 0.2104)
\t\t\t\t(material "7628")
\t\t\t\t(epsilon_r 4.4)
\t\t\t\t(loss_tangent 0.02)
\t\t\t)
\t\t\t(layer "B.Cu"
\t\t\t\t(type "copper")
\t\t\t\t(thickness 0.035)
\t\t\t)
\t\t\t(layer "B.Mask"
\t\t\t\t(type "Bottom Solder Mask")
\t\t\t\t(color "Green")
\t\t\t\t(thickness 0.015)
\t\t\t)
\t\t\t(layer "B.Paste"
\t\t\t\t(type "Bottom Solder Paste")
\t\t\t)
\t\t\t(layer "B.SilkS"
\t\t\t\t(type "Bottom Silk Screen")
\t\t\t)
\t\t\t(copper_finish "None")
\t\t\t(dielectric_constraints no)
\t\t)
"""
    # Replace the whole stackup, including a previously rewritten one.
    end = text.find("\n\t\t(pad_to_mask_clearance", start)
    if end < 0:
        raise SystemExit("stackup block not found")
    text = text[:start] + block + text[end:]
    text = text.replace("(thickness 1.6)", f"(thickness {STACK_SUM_MM:.4f})", 1)
    path.write_text(text)


def ij(x: float, y: float) -> tuple[int, int]:
    return (
        max(0, min(NX - 1, int((x - X0) / STEP))),
        max(0, min(NY - 1, int((y - Y0) / STEP))),
    )


def mm(i: int, j: int) -> tuple[float, float]:
    return (X0 + (i + 0.5) * STEP, Y0 + (j + 0.5) * STEP)


def idx(i: int, j: int) -> int:
    return j * NX + i


class Grid:
    def __init__(self) -> None:
        self.blocked = [bytearray(NX * NY) for _ in range(2)]

    def mark_rect(self, layer: int, x0: float, y0: float, x1: float, y1: float) -> None:
        i0, j0 = ij(min(x0, x1), min(y0, y1))
        i1, j1 = ij(max(x0, x1), max(y0, y1))
        row = self.blocked[layer]
        for j in range(j0, j1 + 1):
            base = j * NX
            for i in range(i0, i1 + 1):
                row[base + i] = 1

    def mark_circle(self, layer: int, x: float, y: float, r: float) -> None:
        i0, j0 = ij(x - r, y - r)
        i1, j1 = ij(x + r, y + r)
        r2 = r * r
        row = self.blocked[layer]
        for j in range(j0, j1 + 1):
            cy = Y0 + (j + 0.5) * STEP
            base = j * NX
            for i in range(i0, i1 + 1):
                cx = X0 + (i + 0.5) * STEP
                if (cx - x) ** 2 + (cy - y) ** 2 <= r2:
                    row[base + i] = 1

    def clear_pad(self, layer: int, pad) -> None:
        box = pad.GetBoundingBox()
        x0, y0 = pcbnew.ToMM(box.GetLeft()), pcbnew.ToMM(box.GetTop())
        x1, y1 = pcbnew.ToMM(box.GetRight()), pcbnew.ToMM(box.GetBottom())
        i0, j0 = ij(x0, y0)
        i1, j1 = ij(x1, y1)
        row = self.blocked[layer]
        for j in range(j0, j1 + 1):
            cy = Y0 + (j + 0.5) * STEP
            base = j * NX
            for i in range(i0, i1 + 1):
                cx = X0 + (i + 0.5) * STEP
                if pad.HitTest(pcbnew.VECTOR2I(pcbnew.FromMM(cx), pcbnew.FromMM(cy))):
                    row[base + i] = 0

    def copy(self) -> Grid:
        g = Grid()
        g.blocked = [bytearray(layer) for layer in self.blocked]
        return g


def pad_layers(pad) -> list[int]:
    # 0 = top, 1 = bottom. Through-hole copper is on both.
    if pad.GetAttribute() == pcbnew.PAD_ATTRIB_SMD:
        return [1] if pad.IsOnLayer(pcbnew.B_Cu) and not pad.IsOnLayer(pcbnew.F_Cu) else [0]
    return [0, 1]


def block_pad(grid: Grid, pad, extra: float) -> None:
    """Block cells closer than `extra` to the land. Corners are round, so a
    neighbor pad does not seal the straight escape off a fine-pitch land."""
    box = pad.GetBoundingBox()
    x0 = pcbnew.ToMM(box.GetLeft())
    y0 = pcbnew.ToMM(box.GetTop())
    x1 = pcbnew.ToMM(box.GetRight())
    y1 = pcbnew.ToMM(box.GetBottom())
    for layer in pad_layers(pad):
        if pad.GetShape() == pcbnew.PAD_SHAPE_CIRCLE:
            pos = pad.GetPosition()
            r = pcbnew.ToMM(pad.GetSizeX()) / 2 + extra
            grid.mark_circle(layer, pcbnew.ToMM(pos.x), pcbnew.ToMM(pos.y), r)
            continue
        i0, j0 = ij(x0 - extra, y0 - extra)
        i1, j1 = ij(x1 + extra, y1 + extra)
        for j in range(max(0, j0), min(NY - 1, j1) + 1):
            cy = Y0 + (j + 0.5) * STEP
            dy = max(y0 - cy, 0.0, cy - y1)
            for i in range(max(0, i0), min(NX - 1, i1) + 1):
                cx = X0 + (i + 0.5) * STEP
                dx = max(x0 - cx, 0.0, cx - x1)
                if dx * dx + dy * dy <= extra * extra:
                    grid.blocked[layer][idx(i, j)] = 1


def forbid_vias(via_ok: bytearray, x1: float, y1: float, x2: float, y2: float, radius: float) -> None:
    steps = max(1, int(math.hypot(x2 - x1, y2 - y1) / (STEP * 0.5)))
    r2 = radius * radius
    for s in range(steps + 1):
        t = s / steps
        cx = x1 + (x2 - x1) * t
        cy = y1 + (y2 - y1) * t
        i0, j0 = ij(cx - radius, cy - radius)
        i1, j1 = ij(cx + radius, cy + radius)
        for j in range(max(0, j0), min(NY - 1, j1) + 1):
            y = Y0 + (j + 0.5) * STEP
            for i in range(max(0, i0), min(NX - 1, i1) + 1):
                x = X0 + (i + 0.5) * STEP
                if (x - cx) * (x - cx) + (y - cy) * (y - cy) <= r2:
                    via_ok[idx(i, j)] = 0


def block_committed_track(grid: Grid, via_ok: bytearray, layer: int, x1, y1, x2, y2, width: float) -> None:
    # Next track stays a clearance away. Vias, which are wider, are refused
    # separately so a thin trace does not wall off a nearby land.
    block_segment(grid, layer, x1, y1, x2, y2, width / 2 + CLEARANCE + 0.35 / 2)
    forbid_vias(via_ok, x1, y1, x2, y2, width / 2 + CLEARANCE + VIA_D / 2)


def block_segment(grid: Grid, layer: int, x1: float, y1: float, x2: float, y2: float, radius: float) -> None:
    steps = max(1, int(math.hypot(x2 - x1, y2 - y1) / (STEP * 0.5)))
    for s in range(steps + 1):
        t = s / steps
        grid.mark_circle(layer, x1 + (x2 - x1) * t, y1 + (y2 - y1) * t, radius)


def route_astar(grid: Grid, sources: list[tuple[int, int, int]], goals: set[tuple[int, int, int]], via_ok: bytearray):
    """4-connected search. A via costs more than a straight run."""
    if not sources or not goals:
        return None
    goal = set(goals)
    start_set = set(sources)
    # Heuristic target is the goal's center cell, not every land cell.
    gi = sum(c[0] for c in goals) // len(goals)
    gj = sum(c[1] for c in goals) // len(goals)
    gl = next(iter(goals))[2]

    def priority(node, cost):
        i, j, layer = node
        return cost + abs(i - gi) + abs(j - gj) + (0 if layer == gl else 3)

    heap = []
    parent: dict[tuple[int, int, int], tuple[int, int, int] | None] = {}
    best: dict[tuple[int, int, int], int] = {}
    # Last via on the path that reached this cell, so a second via in the
    # same route cannot land closer than the hole-to-hole rule.
    last_via: dict[tuple[int, int, int], tuple[int, int] | None] = {}
    for s in sources:
        parent[s] = None
        best[s] = 0
        last_via[s] = None
        heapq.heappush(heap, (priority(s, 0), 0, s))
    seen = 0
    dirs = ((1, 0), (-1, 0), (0, 1), (0, -1))
    while heap:
        _, cost, node = heapq.heappop(heap)
        if best.get(node, 1 << 30) != cost:
            continue
        if node in goal and node not in start_set:
            path = []
            cur: tuple[int, int, int] | None = node
            while cur is not None:
                path.append(cur)
                cur = parent[cur]
            path.reverse()
            return path
        seen += 1
        if seen > 2000000:
            print("  search cap")
            return None
        i, j, layer = node
        for di, dj in dirs:
            ni, nj = i + di, j + dj
            if not (0 <= ni < NX and 0 <= nj < NY):
                continue
            if grid.blocked[layer][idx(ni, nj)]:
                continue
            nxt = (ni, nj, layer)
            ncost = cost + 1
            if ncost < best.get(nxt, 1 << 30):
                best[nxt] = ncost
                parent[nxt] = node
                last_via[nxt] = last_via.get(node)
                heapq.heappush(heap, (priority(nxt, ncost), ncost, nxt))
        other = 1 - layer
        # A via is 0.6 mm across. Only drop one where the ring clears pads,
        # and not within 0.65 mm of the previous via in this same route.
        prev = last_via.get(node)
        if prev is not None and (i - prev[0]) ** 2 + (j - prev[1]) ** 2 < 13 * 13:
            continue
        if via_ok[idx(i, j)] and not grid.blocked[other][idx(i, j)]:
            nxt = (i, j, other)
            ncost = cost + 3
            if ncost < best.get(nxt, 1 << 30):
                best[nxt] = ncost
                parent[nxt] = node
                last_via[nxt] = (i, j)
                heapq.heappush(heap, (priority(nxt, ncost), ncost, nxt))
    return None


def cells_of(pad, layer: int) -> list[tuple[int, int, int]]:
    box = pad.GetBoundingBox()
    i0, j0 = ij(pcbnew.ToMM(box.GetLeft()), pcbnew.ToMM(box.GetTop()))
    i1, j1 = ij(pcbnew.ToMM(box.GetRight()), pcbnew.ToMM(box.GetBottom()))
    out = []
    for j in range(j0, j1 + 1):
        cy = Y0 + (j + 0.5) * STEP
        for i in range(i0, i1 + 1):
            cx = X0 + (i + 0.5) * STEP
            if pad.HitTest(pcbnew.VECTOR2I(pcbnew.FromMM(cx), pcbnew.FromMM(cy))):
                out.append((i, j, layer))
    return out


def add_track(board, net, layer_id, x1, y1, x2, y2, width) -> None:
    if math.hypot(x2 - x1, y2 - y1) < 0.001:
        return
    track = pcbnew.PCB_TRACK(board)
    track.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(x1), pcbnew.FromMM(y1)))
    track.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(x2), pcbnew.FromMM(y2)))
    track.SetWidth(pcbnew.FromMM(width))
    track.SetLayer(layer_id)
    track.SetNet(net)
    board.Add(track)


def add_via(board, net, x, y):
    via = pcbnew.PCB_VIA(board)
    via.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y)))
    via.SetDrill(pcbnew.FromMM(VIA_DRILL))
    via.SetWidth(pcbnew.F_Cu, pcbnew.FromMM(VIA_D))
    via.SetWidth(pcbnew.B_Cu, pcbnew.FromMM(VIA_D))
    via.SetWidth(pcbnew.In1_Cu, pcbnew.FromMM(VIA_D))
    via.SetWidth(pcbnew.In2_Cu, pcbnew.FromMM(VIA_D))
    via.SetViaType(pcbnew.VIATYPE_THROUGH)
    via.SetNet(net)
    board.Add(via)
    return via


def compress(path: list[tuple[int, int, int]]) -> list[tuple[float, float, int]]:
    pts = [(mm(i, j)[0], mm(i, j)[1], layer) for i, j, layer in path]
    if len(pts) < 3:
        return pts
    out = [pts[0]]
    for k in range(1, len(pts) - 1):
        ax, ay, al = out[-1]
        bx, by, bl = pts[k]
        cx, cy, cl = pts[k + 1]
        straight = (al == bl == cl) and abs((bx - ax) * (cy - by) - (by - ay) * (cx - bx)) < 1e-9
        if not straight:
            out.append(pts[k])
    out.append(pts[-1])
    return out


def copy_outline(zone, poly) -> None:
    """Copy a polygon, including holes, onto a zone.

    NewHole() plus Append() writes the hole vertices onto the outline, so
    the slot becomes part of the contour and the pour misses most of the
    board. AddOutline/AddHole keep the slot as a hole.
    """
    outline = zone.Outline()
    outline.RemoveAllContours()
    for n in range(poly.OutlineCount()):
        outline.AddOutline(poly.Outline(n))
        for h in range(poly.HoleCount(n)):
            outline.AddHole(poly.Hole(n, h))


def make_zone(board, poly, layer, net, keepout: bool = False):
    zone = pcbnew.ZONE(board)
    zone.SetLayer(layer)
    if keepout:
        zone.SetIsRuleArea(True)
        zone.SetDoNotAllowCopperPour(True)
        zone.SetDoNotAllowVias(True)
        zone.SetDoNotAllowTracks(False)
        zone.SetDoNotAllowPads(False)
        zone.SetDoNotAllowFootprints(False)
    else:
        zone.SetNet(net)
        # Solid connection. Thermal spokes on the inner planes left the
        # header and the module ground pads off the net, and the same
        # relief left 0402 grounds as their own islands.
        zone.SetPadConnection(pcbnew.ZONE_CONNECTION_FULL)
        zone.SetThermalReliefGap(pcbnew.FromMM(0))
        zone.SetMinThickness(pcbnew.FromMM(0.15))
        zone.SetLocalClearance(pcbnew.FromMM(0.15))
        zone.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_ALWAYS)
    copy_outline(zone, poly)
    board.Add(zone)
    return zone


def rect_poly(x0, y0, x1, y1):
    poly = pcbnew.SHAPE_POLY_SET()
    poly.NewOutline()
    for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
        poly.Append(pcbnew.FromMM(x), pcbnew.FromMM(y))
    return poly


def main() -> int:
    global RF_W_MM, RF_Z0
    RF_W_MM, RF_Z0 = microstrip_width()
    print(f"RF width {RF_W_MM:.2f} mm, bare Z0 {RF_Z0:.2f} ohm at {RF_H_MM:.3f} mm, Er {RF_ER}")
    apply_stackup(BOARD)
    board = pcbnew.LoadBoard(str(BOARD))
    removed = []
    for track in list(board.GetTracks()):
        board.Remove(track)
        removed.append(track)
    for zone in list(board.Zones()):
        # The template PoE keepout and footprint keepouts (the U.FL one) stay.
        # Board zones from an earlier run are removed. Comparing the parent
        # object to the board does not work: pcbnew hands back a different
        # wrapper, so that test used to leave every previous pour in place.
        if zone.GetZoneName() == "PoE":
            continue
        parent = zone.GetParent()
        if parent is not None and parent.GetClass() == "FOOTPRINT":
            continue
        board.Remove(zone)
        removed.append(zone)

    gnd = board.FindNet("GND")
    pads_by_net: dict[str, list] = {}
    all_pads = []
    owner = {}
    for fp in board.GetFootprints():
        ref = fp.GetReference()
        for pad in fp.Pads():
            all_pads.append(pad)
            pads_by_net.setdefault(pad.GetNetname(), []).append(pad)
            owner[id(pad)] = ref

    outline = pcbnew.SHAPE_POLY_SET()
    board.GetBoardPolygonOutlines(outline)
    region = pcbnew.SHAPE_POLY_SET(outline)
    # 0.55 mm keeps a 0.6 mm via plus the 0.2 mm copper-to-edge rule inside the outline.
    region.Deflate(pcbnew.FromMM(0.55), pcbnew.CORNER_STRATEGY_CHAMFER_ALL_CORNERS, pcbnew.FromMM(0.05))

    print("rasterizing board", NX, NY)
    grid = Grid()
    for j in range(NY):
        y = Y0 + (j + 0.5) * STEP
        for i in range(NX):
            x = X0 + (i + 0.5) * STEP
            pt = pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y))
            if not region.Contains(pt):
                grid.blocked[0][idx(i, j)] = 1
                grid.blocked[1][idx(i, j)] = 1
    # PoE keepout, bottom copper.
    grid.mark_rect(1, *POE)

    # No tracks under the cans or the LGA bodies. Pads themselves are opened
    # again per net, so an escape still leaves from the outside of the land.
    for ref, shrink in (("U1", 0.7), ("U2", 0.45), ("U4", 1.6), ("U5", 0.5), ("BT2", 0.4)):
        fp = next(f for f in board.GetFootprints() if f.GetReference() == ref)
        box = fp.GetCourtyard(pcbnew.F_CrtYd).BBox()
        grid.mark_rect(
            0,
            pcbnew.ToMM(box.GetLeft()) + shrink,
            pcbnew.ToMM(box.GetTop()) + shrink,
            pcbnew.ToMM(box.GetRight()) - shrink,
            pcbnew.ToMM(box.GetBottom()) - shrink,
        )
    bt1 = next(f for f in board.GetFootprints() if f.GetReference() == "BT1")
    box = bt1.GetCourtyard(pcbnew.F_CrtYd).BBox()
    # Leave a channel on the left of the holder so VBAT can escape the clip.
    grid.mark_rect(
        0,
        max(pcbnew.ToMM(box.GetLeft()) + 1.0, 115.0),
        pcbnew.ToMM(box.GetTop()) + 0.4,
        pcbnew.ToMM(box.GetRight()) - 0.4,
        pcbnew.ToMM(box.GetBottom()) - 0.4,
    )

    base_extra = 0.2 / 2 + CLEARANCE
    # Pads are blocked per net (foreign lands only). Blocking every land up
    # front wraps each pad in its own keepout and strands the through-hole
    # header, which has copper on both layers.

    # 50 ohm run. Pad centers, so the track ends inside the lands.
    u4 = next(f for f in board.GetFootprints() if f.GetReference() == "U4")
    j2 = next(f for f in board.GetFootprints() if f.GetReference() == "J2")
    fb1 = next(f for f in board.GetFootprints() if f.GetReference() == "FB1")
    rf_net = board.FindNet("RF")
    p_mod = u4.FindPadByNumber("11").GetPosition()
    p_ant = j2.FindPadByNumber("1").GetPosition()
    p_fb = fb1.FindPadByNumber("1").GetPosition()
    x_mod, y_rf = pcbnew.ToMM(p_mod.x), pcbnew.ToMM(p_mod.y)
    x_ant = pcbnew.ToMM(p_ant.x)
    x_fb, y_fb = pcbnew.ToMM(p_fb.x), pcbnew.ToMM(p_fb.y)
    add_track(board, rf_net, pcbnew.F_Cu, x_mod, y_rf, x_ant, y_rf, RF_W_MM)
    add_track(board, rf_net, pcbnew.F_Cu, x_fb, y_rf, x_fb, y_fb, RF_W_MM)
    # Keep other nets and their vias off the microstrip, on both copper layers,
    # so a barrel cannot slot the ground plane under the trace.
    for layer in (0, 1):
        block_segment(grid, layer, x_mod, y_rf, x_ant, y_rf, 1.0)
        block_segment(grid, layer, x_fb, y_rf, x_fb, y_fb, 0.8)
    via_ok = build_via_ok(all_pads)
    print(f"RF {x_mod:.2f},{y_rf:.2f} -> {x_ant:.2f},{y_rf:.2f}  stub to {x_fb:.2f},{y_fb:.2f}")

    failed = []
    fanout_end: dict[tuple[str, str], tuple[float, float, float, float]] = {}
    n_gnd = n_sig = n_fail = 0
    for ref in ("U1", "U2", "U5"):
        fp = next(f for f in board.GetFootprints() if f.GetReference() == ref)
        for pad in list(fp.Pads()):
            name = pad.GetNetname()
            num = str(pad.GetNumber())
            if name in ("", "RF") or name.startswith("unconnected"):
                continue
            end = place_fanout(board, grid, via_ok, all_pads, fp, pad)
            if end is None:
                n_fail += 1
                print(f"FAIL fanout {ref}.{num} {name}")
                failed.append(f"fanout {ref}.{num}")
            elif end == "gnd":
                n_gnd += 1
            else:
                n_sig += 1
                fanout_end[(ref, num)] = end
    print(f"fanout stubs {n_sig} gnd {n_gnd} fail {n_fail}")
    # Power is wider and will fill a narrow gutter. Give the signal nets
    # that gutter first; the BMP581 SDA via has no other way off the chip.
    net_items = sorted(pads_by_net.items(), key=lambda kv: (kv[0] in POWER, kv[0]))
    for name, pads in net_items:
        if name in ("", "GND", "RF") or name.startswith("unconnected"):
            continue
        if len(pads) < 2:
            continue
        width = 0.35 if name in POWER else 0.2
        # Rebuild a working grid: base obstacles plus this net's pads opened,
        # and a slightly wider keepout when the track itself is wider.
        work = grid.copy()
        extra = width / 2 + CLEARANCE
        for pad in all_pads:
            if pad.GetNetname() == name:
                continue
            block_pad(work, pad, extra)
        groups = []
        for pad in pads:
            key = (owner[id(pad)], str(pad.GetNumber()))
            if key in fanout_end:
                x, y, dx, dy = fanout_end[key]
                # The stub leaves the land on a diagonal. The maze is
                # orthogonal, so open the aisle on the dominant axis, by
                # grid index. Stepping in millimetres and truncating skips
                # about every fifth cell, and those holes seal the via
                # inside its own keepout.
                if abs(dx) >= abs(dy):
                    adx, ady = (1 if dx >= 0 else -1), 0
                else:
                    adx, ady = 0, (1 if dy >= 0 else -1)
                i, j = ij(x, y)
                # Same length as the corridor reserved at fanout time.
                # Anything shorter ends inside that reservation.
                span = int(round(FANOUT_RESERVE / STEP))
                for s in range(span + 1):
                    ci, cj = i + adx * s, j + ady * s
                    if not (0 <= ci < NX and 0 <= cj < NY):
                        break
                    work.blocked[0][idx(ci, cj)] = 0
                    work.blocked[1][idx(ci, cj)] = 0
                groups.append((pad, [(i, j, 0), (i, j, 1)]))
                continue
            for layer in pad_layers(pad):
                work.clear_pad(layer, pad)
            cells = []
            for layer in pad_layers(pad):
                cells.extend(cells_of(pad, layer))
            if cells:
                groups.append((pad, cells))
        if len(groups) < 2:
            failed.append(name)
            continue
        groups.sort(key=lambda item: -sum(1 for c in item[1] if _exits(work, c)))
        for pad, cells in groups:
            exits = sum(1 for c in cells if _exits(work, c))
            if exits == 0:
                print(f"  sealed {owner[id(pad)]}.{pad.GetNumber()} ({len(cells)} cells)")
        connected = list(groups[0][1])
        net = pads[0].GetNet()
        pending = []
        ok = True
        for pad, cells in groups[1:]:
            path = route_astar(work, connected, set(cells), via_ok)
            if not path:
                exits = sum(1 for c in cells if _exits(work, c))
                print(f"  no path to {owner[id(pad)]}.{pad.GetNumber()} ({len(cells)} cells, {exits} exits)")
                ok = False
                break
            pts = compress(path)
            for a, b in zip(pts, pts[1:]):
                pending.append((a, b))
            for cell in path:
                connected.append(cell)
                work.blocked[cell[2]][idx(cell[0], cell[1])] = 0
        if ok:
            for a, b in pending:
                layer_id = pcbnew.F_Cu if a[2] == 0 else pcbnew.B_Cu
                if a[2] == b[2]:
                    add_track(board, net, layer_id, a[0], a[1], b[0], b[1], width)
                    block_committed_track(grid, via_ok, a[2], a[0], a[1], b[0], b[1], width)
                else:
                    add_via(board, net, a[0], a[1])
                    # Block the working grid too, or the next pad of this
                    # same net will drop another via on top of this one.
                    occupy_via(grid, a[0], a[1])
                    occupy_via(work, a[0], a[1])
        print(("ok  " if ok else "FAIL") + f" {name} ({len(pads)} pads, {width:.2f} mm)")
        if not ok:
            failed.append(name)

    # Stitch the ground planes. Skip anything close to a pad or the RF run.
    n_via = 0
    for x in [i * 3.5 + 104 for i in range(18)]:
        for y in [j * 3.5 + 50 for j in range(14)]:
            if not _via_site(grid, all_pads, x, y, board):
                continue
            add_via(board, gnd, x, y)
            occupy_via(grid, x, y)
            n_via += 1
    for x, y in ((156.4, 54.15), (158.6, 54.15), (156.4, 58.35), (159.3, 54.15), (163.5, 54.12), (163.5, 57.08)):
        if _via_site(grid, all_pads, x, y, board):
            add_via(board, gnd, x, y)
            n_via += 1
    # Fence the microstrip. Centers sit outside the top-pour keepout so the
    # via barrels do not slot the In1 plane under the trace.
    fence = 1.35
    x = x_mod + 1.4
    while x < x_ant - 0.6:
        for y in (y_rf - fence, y_rf + fence):
            if _via_site(grid, all_pads, x, y, board):
                add_via(board, gnd, x, y)
                occupy_via(grid, x, y)
                n_via += 1
        x += 1.8
    print("ground vias", n_via)
    merged = merge_close_vias(board)
    removed.extend(merged)
    if merged:
        print("merged close vias", len(merged))

    poly = pcbnew.SHAPE_POLY_SET()
    board.GetBoardPolygonOutlines(poly)
    pour = pcbnew.SHAPE_POLY_SET(poly)
    # JLCPCB wants copper 0.3 mm inside the outline, including the slot and notch.
    pour.Deflate(pcbnew.FromMM(0.3), pcbnew.CORNER_STRATEGY_CHAMFER_ALL_CORNERS, pcbnew.FromMM(0.05))
    for layer in (pcbnew.In1_Cu, pcbnew.In2_Cu, pcbnew.F_Cu, pcbnew.B_Cu):
        make_zone(board, pour, layer, gnd)
    # Pour keepout tight around the microstrip, top only. In1 under it stays solid.
    make_zone(board, rect_poly(x_mod - 0.2, y_rf - 0.85, x_ant + 0.2, y_rf + 0.85), pcbnew.F_Cu, gnd, keepout=True)
    make_zone(board, rect_poly(x_fb - 0.7, y_rf, x_fb + 0.7, y_fb + 0.3), pcbnew.F_Cu, gnd, keepout=True)

    print("filling zones")
    filler = pcbnew.ZONE_FILLER(board)
    filler.Fill(board.Zones())
    stitched = stitch_orphan_islands(board, gnd)
    if stitched:
        print("island stitches", len(stitched))
        filler.Fill(board.Zones())

    tb = board.GetTitleBlock()
    tb.SetComment(0, "JLC04161H-7628, routed")
    board.Save(str(BOARD))
    # pcbnew rewrites the file; put the stackup back if the save dropped it.
    saved = BOARD.read_text()
    if 'material "7628"' not in saved:
        apply_stackup(BOARD)
        saved = BOARD.read_text()
        print("stackup restored after save")
    else:
        print("stackup kept")
    saved = saved.replace('rev 0.3, placed, not routed', 'rev 0.3', 1)
    BOARD.write_text(saved)
    zones = sum(1 for line in saved.splitlines() if line == "\t(zone")
    print("board zone blocks", zones)
    print("failed", failed)
    print("kept objects", len(removed))
    if zones != 7:
        print("expected 4 ground pours, 2 RF keepouts, and the PoE keepout")
        return 1
    return 1 if failed else 0


def stitch_orphan_islands(board, gnd):
    """Tie an SMD ground island to the inner plane.

    The header pins are plated through and already reach In1. A pour
    fragment that only touches an SMD pad does not, and DRC reports that
    fragment as the ground zone unconnected to itself.
    """
    added = []
    vias = [t for t in board.GetTracks() if t.Type() == pcbnew.PCB_VIA_T and t.GetNetname() == "GND"]
    pth = []
    pads = []
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            pads.append(pad)
            if pad.GetNetname() == "GND" and pad.GetAttribute() == pcbnew.PAD_ATTRIB_PTH:
                pth.append(pad)
    keepouts = [z for z in board.Zones() if z.GetIsRuleArea() and z.GetDoNotAllowVias()]

    def inside(poly, items) -> bool:
        return any(poly.Contains(item.GetPosition()) for item in items)

    for zone in list(board.Zones()):
        if zone.GetIsRuleArea() or zone.GetNetname() != "GND":
            continue
        layer = zone.GetLayer()
        if layer not in (pcbnew.F_Cu, pcbnew.B_Cu):
            continue
        polys = zone.GetFilledPolysList(layer)
        for i in range(polys.OutlineCount()):
            sub = pcbnew.SHAPE_POLY_SET()
            sub.AddOutline(polys.Outline(i))
            for h in range(polys.HoleCount(i)):
                sub.AddHole(polys.Hole(i, h))
            if inside(sub, vias) or inside(sub, pth):
                continue
            site = _island_via_site(sub, pads, vias, keepouts)
            if site is None:
                print("no stitch site on layer", layer)
                continue
            via = add_via(board, gnd, site[0], site[1])
            vias.append(via)
            added.append(via)
    return added


def _island_via_site(poly, pads, vias, keepouts):
    """A point whose 0.6 mm ring sits inside the island and clears other holes."""
    box = poly.BBox()
    x0, y0 = pcbnew.ToMM(box.GetLeft()), pcbnew.ToMM(box.GetTop())
    x1, y1 = pcbnew.ToMM(box.GetRight()), pcbnew.ToMM(box.GetBottom())
    margin = 0.35
    y = y0 + 0.4
    while y < y1:
        x = x0 + 0.4
        while x < x1:
            if _island_site_ok(poly, pads, vias, keepouts, x, y, margin):
                return (x, y)
            x += 0.4
        y += 0.4
    return None


def _island_site_ok(poly, pads, vias, keepouts, x, y, margin) -> bool:
    for dx, dy in ((0, 0), (margin, 0), (-margin, 0), (0, margin), (0, -margin)):
        pt = pcbnew.VECTOR2I(pcbnew.FromMM(x + dx), pcbnew.FromMM(y + dy))
        if not poly.Contains(pt):
            return False
    if not _point_clears(pads, None, x, y, VIA_D / 2 + CLEARANCE):
        return False
    for via in vias:
        vx, vy = pcbnew.ToMM(via.GetPosition().x), pcbnew.ToMM(via.GetPosition().y)
        if math.hypot(vx - x, vy - y) < 0.65:
            return False
    pt = pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y))
    for zone in keepouts:
        if zone.Outline().Contains(pt):
            return False
    return True


def merge_close_vias(board) -> int:
    """Drop a via that sits within the hole-to-hole limit of another.

    Two layer changes in one route can land 0.45 mm apart. Keep the first
    via and move the other's track ends onto it. Same net only; a foreign
    via this close would already have failed the grid.
    """
    limit = 0.65
    removed = []
    vias = [t for t in board.GetTracks() if t.Type() == pcbnew.PCB_VIA_T]
    for i, via in enumerate(vias):
        if via in removed:
            continue
        x, y = pcbnew.ToMM(via.GetPosition().x), pcbnew.ToMM(via.GetPosition().y)
        for other in vias[i + 1 :]:
            if other in removed or other.GetNetname() != via.GetNetname():
                continue
            ox, oy = pcbnew.ToMM(other.GetPosition().x), pcbnew.ToMM(other.GetPosition().y)
            if math.hypot(ox - x, oy - y) >= limit:
                continue
            for track in list(board.GetTracks()):
                if track.Type() == pcbnew.PCB_VIA_T or track.GetNetname() != via.GetNetname():
                    continue
                start, end = track.GetStart(), track.GetEnd()
                sx, sy = pcbnew.ToMM(start.x), pcbnew.ToMM(start.y)
                ex, ey = pcbnew.ToMM(end.x), pcbnew.ToMM(end.y)
                if math.hypot(sx - ox, sy - oy) < 0.05:
                    track.SetStart(via.GetPosition())
                if math.hypot(ex - ox, ey - oy) < 0.05:
                    track.SetEnd(via.GetPosition())
            board.Remove(other)
            removed.append(other)
    return removed


def occupy_via(grid: Grid, x: float, y: float) -> None:
    """Keep the next track or via clear of this barrel on both layers."""
    radius = VIA_D / 2 + CLEARANCE + VIA_D / 2
    grid.mark_circle(0, x, y, radius)
    grid.mark_circle(1, x, y, radius)


def build_via_ok(pads) -> bytearray:
    """Cells where a 0.6 mm via clears every land by the design clearance."""
    blocked = Grid()
    for pad in pads:
        block_pad(blocked, pad, VIA_D / 2 + CLEARANCE)
    ok = bytearray(NX * NY)
    for n in range(NX * NY):
        if not blocked.blocked[0][n] and not blocked.blocked[1][n]:
            ok[n] = 1
    return ok


def place_fanout(board, grid: Grid, via_ok: bytearray, pads, fp, pad):
    """Short top-side stub from a fine-pitch land out to open copper.

    Ground stubs end on a via into the inner planes. Other stubs stop in the
    clear so the maze can pick them up without squeezing between the lands.
    Returns "gnd", an (x, y, dx, dy) tip, or None when no tip fits.
    """
    px, py = pcbnew.ToMM(pad.GetPosition().x), pcbnew.ToMM(pad.GetPosition().y)
    fx, fy = pcbnew.ToMM(fp.GetPosition().x), pcbnew.ToMM(fp.GetPosition().y)
    dx, dy = px - fx, py - fy
    norm = math.hypot(dx, dy) or 1.0
    dx, dy = dx / norm, dy / norm
    half = max(pcbnew.ToMM(pad.GetSizeX()), pcbnew.ToMM(pad.GetSizeY())) / 2
    gnd_pad = pad.GetNetname() == "GND"
    for extra in (0.8, 1.2, 1.6, 2.1, 2.7):
        x = px + dx * (half + extra)
        y = py + dy * (half + extra)
        ix, iy = ij(x, y)
        ox, oy = ij(x + dx * 0.55, y + dy * 0.55)
        if not (0 <= ix < NX and 0 <= iy < NY and 0 <= ox < NX and 0 <= oy < NY):
            continue
        if grid.blocked[0][idx(ix, iy)] or grid.blocked[0][idx(ox, oy)]:
            continue
        if not _point_clears(pads, pad, x, y, 0.25):
            continue
        if not _via_site(grid, pads, x, y, board):
            continue
        add_track(board, pad.GetNet(), pcbnew.F_Cu, px, py, x, y, 0.2)
        block_committed_track(grid, via_ok, 0, px, py, x, y, 0.2)
        add_via(board, pad.GetNet(), x, y)
        occupy_via(grid, x, y)
        if gnd_pad:
            return "gnd"
        reserve_fanout(grid, x, y, dx, dy)
        return (x, y, dx, dy)
    return None


def reserve_fanout(grid: Grid, x: float, y: float, dx: float, dy: float) -> None:
    """Block a short corridor in front of a fanout via on the base grid.

    The owning net opens the centerline later. Other nets stay out, so a
    track drawn first cannot wall the via in.
    """
    if abs(dx) >= abs(dy):
        adx, ady = (1 if dx >= 0 else -1), 0
    else:
        adx, ady = 0, (1 if dy >= 0 else -1)
    i, j = ij(x, y)
    span = int(round(FANOUT_RESERVE / STEP))
    half = int(round(FANOUT_RESERVE_HALF / STEP))
    for s in range(span + 1):
        for w in range(-half, half + 1):
            ci = i + adx * s - ady * w
            cj = j + ady * s + adx * w
            if not (0 <= ci < NX and 0 <= cj < NY):
                continue
            grid.blocked[0][idx(ci, cj)] = 1
            grid.blocked[1][idx(ci, cj)] = 1


def _point_clears(pads, own, x: float, y: float, gap: float) -> bool:
    for pad in pads:
        if pad is own:
            continue
        box = pad.GetBoundingBox()
        x0, y0 = pcbnew.ToMM(box.GetLeft()), pcbnew.ToMM(box.GetTop())
        x1, y1 = pcbnew.ToMM(box.GetRight()), pcbnew.ToMM(box.GetBottom())
        dx = max(x0 - x, 0.0, x - x1)
        dy = max(y0 - y, 0.0, y - y1)
        if dx * dx + dy * dy < gap * gap:
            return False
    return True


def escape_gnd(board, grid: Grid, via_ok: bytearray, pads, pad, gnd) -> bool:
    """Short track from a fine-pitch ground land to a via, away from the body."""
    fp = pad.GetParent()
    px, py = pcbnew.ToMM(pad.GetPosition().x), pcbnew.ToMM(pad.GetPosition().y)
    fx, fy = pcbnew.ToMM(fp.GetPosition().x), pcbnew.ToMM(fp.GetPosition().y)
    dx, dy = px - fx, py - fy
    norm = math.hypot(dx, dy) or 1.0
    dx, dy = dx / norm, dy / norm
    half = max(pcbnew.ToMM(pad.GetSizeX()), pcbnew.ToMM(pad.GetSizeY())) / 2
    for step in range(40):
        dist = half + 0.2 + step * 0.05
        x, y = px + dx * dist, py + dy * dist
        if not _via_site(grid, pads, x, y, board):
            continue
        add_track(board, gnd, pcbnew.F_Cu, px, py, x, y, 0.2)
        add_via(board, gnd, x, y)
        block_committed_track(grid, via_ok, 0, px, py, x, y, 0.2)
        occupy_via(grid, x, y)
        return True
    return False


def _exits(grid: Grid, cell: tuple[int, int, int]) -> bool:
    i, j, layer = cell
    for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        ni, nj = i + di, j + dj
        if 0 <= ni < NX and 0 <= nj < NY and not grid.blocked[layer][idx(ni, nj)]:
            return True
    return False


def _via_site(grid: Grid, pads, x: float, y: float, board=None) -> bool:
    i, j = ij(x, y)
    if not (0 <= i < NX and 0 <= j < NY):
        return False
    if grid.blocked[0][idx(i, j)] or grid.blocked[1][idx(i, j)]:
        return False
    # Copper clearance to lands. Rectangle distance covers square header pads,
    # whose corners stick out past a circle of the same width.
    if not _point_clears(pads, None, x, y, VIA_D / 2 + CLEARANCE):
        return False
    if board is None:
        return True
    need_via = VIA_D + CLEARANCE
    for item in board.GetTracks():
        if item.Type() == pcbnew.PCB_VIA_T:
            vx, vy = pcbnew.ToMM(item.GetPosition().x), pcbnew.ToMM(item.GetPosition().y)
            if math.hypot(vx - x, vy - y) < need_via:
                return False
            continue
        x1, y1 = pcbnew.ToMM(item.GetStart().x), pcbnew.ToMM(item.GetStart().y)
        x2, y2 = pcbnew.ToMM(item.GetEnd().x), pcbnew.ToMM(item.GetEnd().y)
        half = pcbnew.ToMM(item.GetWidth()) / 2
        if _dist_seg(x, y, x1, y1, x2, y2) < half + VIA_D / 2 + CLEARANCE:
            return False
    return True


def _dist_seg(px, py, x1, y1, x2, y2) -> float:
    dx, dy = x2 - x1, y2 - y1
    length2 = dx * dx + dy * dy
    if length2 < 1e-12:
        return math.hypot(px - x1, py - y1)
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / length2))
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))


if __name__ == "__main__":
    raise SystemExit(main())
