# `wing_pod_v2.py` — wing air-data pod (clamshell)

Parametric CadQuery model of the under-wing air-data pod. **v2** replaces the
v1 rectangular insert (`wing_pod_case.py` / `KingfisherPod.zip`) with a
left/right aerodynamic clamshell that *is* the outer shell.

**Non-negotiables:** see [`REQUIREMENTS.md`](REQUIREMENTS.md). After every
geometry change, regenerate and run the validator (must exit 0):

```bash
cd enclosures/pod && uv run --project .. python wing_pod_v2.py
cd enclosures/pod && uv run --project .. python validate_pod.py
```

- Printer/material: AnkerMake M5C, PETG. Slice recipe (supports, PETG starting
  points): [`PRINT_RECIPE.md`](PRINT_RECIPE.md). Exported STLs are already
  **flange / mating-face down**, curved outer up, and **rotated 45°** for the
  bed diagonal (10 mm margin vs 220 mm — script asserts the print AABB).
- Shape: **skinny tall ellipse** (~220 × 52 × 77 mm) chopped to a **flat top**
  (wing-fairing mate, square edge) and **flat bottom** (stands upright) with a
  **radiused bottom edge** for aero; **rounded ogive nose and tail**
  (multi-station loft, tip centres near the seam). Midsection uses the *same*
  ellipse as the fairing end-stations (held through a short overlap) so there
  is no rectangular freestream-facing step at the junction. Section is
  asymmetric about the seam (thin left cover, wider right electronics bay).
- Asymmetry: **board mounts / static bay / panel cluster are on `pod_right` only**;
- **Seal (S1/S2/S3):** outer skin opens only at the SUN tip mouth, static-hole
  array (into an **isolated BMP bay**), CAB-15464 panel USB, COM-08837 rocker,
  and two Ø8.2 LED-holder holes. No Pro Micro wall window; no exterior
  battery door — install the LiPo from the open mating face, then close. Mating
  faces: thin rubber strip / O-cord in the right-half groove, and/or a thin RTV
  coat on the flange. `pod_left` is the cover (pitot cradle + clearance holes).
  After heat-set, `static_cover.stl` closes the BMP bay tool window (foam/RTV).
- Fasteners: **12× M2.5** (6 X-stations × top+bottom). Brass heat-set inserts
  in the **right** flange (pilot = insert OD − melt allowance, plus screw-relief
  bore below the insert). Left cover is clearance Ø + counterbore only — screws
  go through the cover into the right-half inserts. **Boards wall-mount** on
  the right +Y inner land (XZ plane) on **raised standoffs** (PCB not flush on
  the wall); the same inserts are heat-set from the **open mating face** (iron
  along Y). Screw-relief continues past each insert into the land (hub-case
  scheme: long M2.5s can pass the brass). No floor posts.
- Internal blanks (flange, bulkheads, deck, posts, static-bay walls) are
  **intersected with the outer envelope** so rectangular stock cannot poke
  through the curved skin.

## Regenerating

```bash
uv sync --all-packages   # from repo root
cd enclosures/pod && uv run --project .. python wing_pod_v2.py   # also runs validate
cd enclosures/pod && uv run --project .. python validate_pod.py  # or re-check alone
# STL-only (fast):  uv run --project .. python validate_pod.py --stl-only
```

Exports (CWD):

| File | Role |
|------|------|
| `pod_left.stl` / `.step` | Left half, print-oriented |
| `pod_right.stl` / `.step` | Right half (electronics), print-oriented |
| `pod_assembly.step` | Assembled (model orientation) + SUN-B placeholder |
| `static_cover.stl` / `.step` | Closes the static-bay tool window after heat-set |
| `pm_tray.stl` / `.step` | Pro Micro clamp tray (no OEM holes) |
| `pod_v2_*_*.png` | Shape previews — **unique name per revision** |
| `pod_v2_*_panel_interior.png` | Ortho into open `pod_right` (standoffs + panel pad) |
| `pod_v2_*_panel_routing.png` | 2D X–Z / X–Y keepouts + hose/Qwiic/USB/switch/LEDs |

## Coordinate system & layout

Assembled frame: origin at outer nose tip on the seam / bottom. **+X aft**,
**+Y right**, **+Z up** (flat top at `OUTER_H`). Split plane is **Y = 0**.

Print STLs (`pod_left.stl` / `pod_right.stl`): drop on the bed as-exported —
no slicer rotation needed. Each STL is a **single solid** (AnkerMake rejects
multi-body compounds). STEP files stay in model orientation for Fusion.

**Left vs right:** only `pod_right` has the electronics **wall land**, raised
standoffs + M2.5 inserts (flange + board), isolated static bay, static holes,
and the +Y panel cluster (USB / rocker / LEDs on a local 2.5 mm planar pad).
`pod_left` is the cover (shell + pitot cradle + flange clearance holes).

Nose → aft:

1. Lofted nose fairing; **ESA SUN-B** cradle on the centerline (tip protrudes)
2. Constant midsection: MS4525 + boost **on the +Y wall** beside the cradle;
   battery slab on the seam; Babysitter + Pro Micro on the wall; BMP581 + mag
   in an isolated static bay (tool window + `static_cover.stl`)
3. Lofted tail fairing (empty taper)

## Pneumatics (decoupled)

| Line | Source | Destination |
|------|--------|-------------|
| Total | SUN-B **aft** barb (pitot) | 6 mm hose → COTS reducer → MS4525 `+` |
| Static (airspeed) | SUN-B **middle** barb | 6 mm hose → COTS reducer → MS4525 `−` |
| TE | SUN-B **forward** barb | Capped / unused (Prandtl) |
| Static (baro) | Pod multi-hole side array | Isolated BMP581 bay only |

Mount SUN with **barbs up** (ESA water tip). Hose escapes into the right half
toward the MS4525.

### Tubing sizes

- SUN-B barbs: **6 mm ID** silicone (stem OD ≈ 5.96 mm).
- TE/Holybro MS4525DO: **1/8″ barbed ports mate with 3/32″ ID tubing**
  (~2.38 mm ID). v1 calipers: tip Ø**2.1**, shoulder Ø**3.5**, spacing **4.3** mm.
- Step 6 mm → MS size with a **COTS reducer** (short hop of tiny tubing onto
  the sensor). Do not rely on nested hose alone.

## Pitot mount (ESA SUN-B)

Calipers: [`SUN_B_CALIPERS.md`](SUN_B_CALIPERS.md). Printed L/R cradle only
(no tubes-in-tube, no RTV plug):

- Outer nose **fairs into tip Ø8.93** with ≥1.5 mm radial PETG at the mouth
  (print-1 0.7 mm lip tore on the left half); tip-only bore through the nose
  bulkhead; Ø10.65 shoulder seats on that bulkhead’s **aft face** at
  `x=SHOULDER_BH_T` (forward stop)
- **Integral split clamp** on the knurled band (thick L/R land; printed bore
  uses FDM allowance — print-1 0.08 mm radial was too tight; 0.20 mm slip was
  OK on the aft barrel)
- Aft blind recess (Ø6.03 × 7.06) seats on a printed locating boss **shorter
  than the cup by ≥2.5 mm** (print-1 pin was ~2 mm too long and held the SUN
  off the shoulder / aft bulkhead)
- Upward barb bay; hose escapes into the right half
- Primary flight load is the Prandtl cantilever into the brass SUN

## Battery

Pocket sized for **50 × 6 × 70 mm** (X × Y × Z) plus **1 mm** clearance per
side; wedge with double-sided foam tape. Thickness is across the seam
(centerline slab). Install the pack (and foam) into the open right/left
halves **before** fastening and sealing the clamshell — the pocket does not
open to freestream. Charge in service via the **CAB-15464** panel Micro-B on the +Y skin (pigtail
to the Babysitter). RTV under the eared flange; M3 screws + nuts (the cable
includes 14 mm screws — trim or swap to M3×8 if they poke the bay). Snap in
the **COM-08837** rocker (SYSOFF) and two **5 mm LED holders** (Ø8.2 holes).

## Panel hardware (L6 locked)

| Role | P/N | Skin cut | Notes |
|------|-----|----------|-------|
| USB | SparkFun [CAB-15464](https://www.sparkfun.com/panel-mount-usb-micro-b-extension-cable-6.html) | 10.5 × 7.5 mm window + M3 Ø3.3 at **17 mm** | Pigtail to Baby micro-B; RTV under flange; nuts inside |
| Switch | SparkFun [COM-08837](https://www.sparkfun.com/rocker-switch-spst-right-angle.html) (E-Switch R1966A) | **19.6 × 13.0** mm snap-in | `WALL=2.5` is the 2.0–3.0 mm band. SPST to **JP12 (SYSOFF)** and GND; leave onboard S1 OFF |
| LED holders | 5 mm chrome ABS (e.g. [cnflin](http://cnflin.en.alibaba.com/product/315036510-212546229/ABS_Chrome_LED_Holder_for_5mm_led.html)) | **Ø8.2 mm** | Standard 8 mm mount. 5 mm LEDs: **red** = VOUT (pod powered), **blue** = parallel D1 / `!CHG!` |

Cluster is on a **local planar +Y pad** (`PANEL_Y` ≈ 39.4 mm, 2.5 mm thick) at
**z ≈ 66 mm** (above the boards). Forward→aft: blue charge LED ~x=72, rocker
~x=94, red power LED ~x=114, USB ~x=132. Orient the rocker so right-angle
terminals point **+Z** (ceiling), not down into the Boost/Baby keepout.

### Babysitter wiring (PRT-13777 schematic)

The onboard slide switch is **S1**: pole `P` = **SYSOFF**, throw `S` = **GND**.
ON pulls SYSOFF to ground (battery connected to VOUT). OFF lets SYSOFF float
high (BQ24075 disconnects the battery from the load). **USB still powers VOUT
with the switch OFF** (power-path). Charging also requires the switch ON.

There is **no 0.1″ “EXT SW” header**. There **is** an unsilk-screened PTH
**JP12** on SYSOFF. External SPST: JP12 ↔ GND, leave S1 **OFF**. Or desolder
S1 and wire the panel switch in its place.

Onboard LEDs (both anodes on **OUT** / VOUT):

| LED | Colour | Net | Meaning |
|-----|--------|-----|---------|
| D1 | Blue | `!CHG!` via R9 (180 Ω) | Charging (open-drain; blinks 2 Hz on timer fault) |
| D2 | Red | `!PGOOD!` via R10 / SJ5 | **Valid USB/VIN**, not “battery supplying the load” |

Remote **blue charge LED**: parallel D1 (or the CHG pad). Remote **red “pod
powered” LED**: VOUT → resistor → LED → GND (lit from battery **or** USB).

## Fairing / wing attach (deferred)

Flat top deck is the mate for a *separate* printed fairing that blends to
wing curvature and bolts to an inspection plate. Pod↔fairing latch (L-pins
etc.) and sailplane sealing tape are follow-ons — not in this script yet.

## U.FL antenna

Grommet / exit path deferred until an ESP32 module with U.FL is in use.
PCB antenna is fine for early RF testing.

## v1 archive

`../KingfisherPod.zip` and `wing_pod_case.py` are the old insert-style tray
(base + lid, socket stubs, sealed bay shared with MS4525). Do not evolve v1;
tune v2 params instead.
