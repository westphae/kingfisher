"""Run mag and IMU-mount calibration over a flights directory.

Writes one JSON per session under ``~/kingfisher/analysis-cache/cal/`` and a
fleet summary. Recordings larger than ``MAX_MB`` (the recorder left running
for days) are skipped: a full 1 Hz scan of those is a different job.
"""

from __future__ import annotations

import json
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from analysis.cal_mag import analyze_mag, fit_ellipsoid, soak_quick
from analysis.cal_mount import analyze_mount, compass_vs_track
from analysis.db import connect_ro, list_tables
from analysis.epochs import load_epochs
MAX_MB = 1500.0


def clean(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return clean(obj.tolist())
    if isinstance(obj, (np.floating, float)):
        x = float(obj)
        return x if np.isfinite(x) else None
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.bool_, bool)):
        return bool(obj)
    return obj


def narrate(result: dict[str, Any]) -> list[str]:
    """Short sentences the browser and the report both show."""
    lines: list[str] = []
    mag = result.get("mag") or {}
    if mag.get("ok"):
        held = mag.get("heldout_heading_rms_deg")
        ins = mag.get("in_sample_heading_rms_deg")
        rms = held if held is not None else ins
        which = "held out by heading sector" if held is not None else "in-sample"
        if rms is not None:
            lag = mag.get("gps_lag_s") or 0
            lag_s = f" Track is shifted {lag:+d} s to line up with the mag." if lag else ""
            lines.append(
                f"Taxi heading error is {rms:.1f}° ({which}) across "
                f"{mag.get('heading_span_deg', 0):.0f}° of track, "
                f"{mag.get('taxi_s', 0) / 60:.1f} min in the 8–45 kt band.{lag_s}"
            )
        if mag.get("hard_iron_ut"):
            b = mag["hard_iron_ut"]
            lines.append(
                f"Hard iron is [{b[0]:+.1f}, {b[1]:+.1f}, {b[2]:+.1f}] µT. "
                f"Horizontal scale is {mag.get('scale_x', float('nan')):.2f} and "
                f"{mag.get('scale_y', float('nan')):.2f}, with the swing axes "
                f"{mag.get('axis_angle_deg', float('nan')):.0f}° apart (90° means no soft iron in the horizontal)."
            )
        flight_rms = mag.get("sphere_rms_flight_ut")
        taxi_rms = mag.get("sphere_rms_taxi_ut")
        if flight_rms is not None and taxi_rms is not None:
            lines.append(
                f"Sphere residual is {taxi_rms:.2f} µT on the taxi circle and "
                f"{flight_rms:.2f} µT in flight. A large flight number means the "
                "isotropic-scale assumption, or a transient, is showing up once the nose leaves level."
            )
    elif mag.get("reason"):
        lines.append(f"Magnetometer: {mag['reason']}.")

    events = mag.get("transients") or []
    motors = [e for e in events if "glitch" not in e["kind"] and "large" not in e["kind"]]
    large = [e for e in events if "large" in e["kind"] and "glitch" not in e["kind"]]
    n_glitch = sum("glitch" in e["kind"] for e in events)
    if motors:
        shaped = [e for e in motors if "blip" in e["kind"] or "step" in e["kind"]]
        n_rate = sum(e["kind"] == "rate" for e in motors)
        if shaped:
            top_s = max(shaped, key=lambda e: e["peak_ut"])
            n_blip = sum("blip" in e["kind"] for e in shaped)
            n_step = sum("step" in e["kind"] for e in shaped)
            lines.append(
                f"{len(shaped)} parked or straight-taxi field changes of 1–8 µT "
                f"are not the airplane rotating: {n_blip} return to the old field and "
                f"{n_step} leave a new offset. That shape fits a flap, gear, or trim motor. "
                f"The largest is {top_s['peak_ut']:.1f} µT ({top_s['kind']}) during {top_s['phase']}. "
                "Nothing in the log names which circuit it was."
            )
        if n_rate:
            top_r = max((e for e in motors if e["kind"] == "rate"), key=lambda e: e["peak_ut"])
            lines.append(
                f"{n_rate} stretches moved the field more than 4 µT/s faster than the cabin gyro. "
                f"The largest is {top_r['peak_ut']:.1f} µT during {top_r['phase']}. "
                "At 1 Hz that is usually taxi vibration or a heading change the gyro bin missed."
            )
    if large:
        top = max(large, key=lambda e: e["peak_ut"])
        gs = top.get("gs_kt")
        where = top["phase"] if gs is None else f"{top['phase']} at {gs:.0f} kt"
        lines.append(
            f"{len(large)} field changes are 8 µT or more, too big for a flap motor. "
            f"The largest is {top['peak_ut']:.1f} µT ({top['kind']}) during {where}. "
            "That size is hangar steel, a pod that moved, or a bad sample cluster."
        )
    if n_glitch:
        lines.append(
            f"{n_glitch} magnetometer spikes are larger than 15 µT and are treated as glitches, not motors."
        )

    mount = result.get("mount") or {}
    if mount.get("ok"):
        yaw = mount.get("yaw_deg")
        yaw_s = f", yaw {yaw:.1f}° from the nose" if yaw is not None else ", yaw not observable from this recording"
        lines.append(
            f"IMU relative to gravity: roll {mount['roll_deg']:+.1f}°, "
            f"pitch {mount['pitch_deg']:+.1f}°{yaw_s}. "
            "Pitch includes how nose-up the Bonanza sits on its gear, so compare flights rather than comparing to zero."
        )
    elif mount.get("reason"):
        lines.append(f"IMU mount: {mount['reason']}.")
    if mount.get("ahrs_bank_scatter_deg") is not None:
        lines.append(
            f"Live AHRS bank follows the coordinated-turn bank within "
            f"{mount['ahrs_bank_scatter_deg']:.1f}° once a {mount['ahrs_bank_bias_deg']:+.1f}° bias is removed "
            f"({mount.get('ahrs_bank_n', 0)} s of turning flight)."
        )
    if mount.get("ahrs_roll_taxi_deg") is not None:
        lines.append(
            f"During straight taxi the live AHRS reads roll {mount['ahrs_roll_taxi_deg']:+.1f}° "
            f"and pitch {mount['ahrs_pitch_taxi_deg']:+.1f}°. That is the horizon zero this install produced."
        )
    comp = result.get("compass") or {}
    if comp.get("ok") and comp.get("std_deg") is not None:
        within = comp.get("within_15_of_track")
        extra = (
            f" {within * 100:.0f}% of those seconds are within 15° of the track itself."
            if within is not None
            else ""
        )
        lines.append(
            f"Live compass versus GPS track on straight taxi: bias {comp['bias_deg']:+.1f}°, "
            f"scatter {comp['std_deg']:.1f}° around that bias ({comp['n']} s).{extra}"
        )
    for note in (mag.get("notes") or []) + (mount.get("notes") or []):
        if note not in lines:
            lines.append(note)
    return lines


def analyze_session(db_path: Path, *, allow_large: bool = False) -> dict[str, Any]:
    """Full 1 Hz mag + mount calibration of one flight DB."""
    t0 = time.perf_counter()
    size_mb = db_path.stat().st_size / 1e6
    base: dict[str, Any] = {
        "file": db_path.name,
        "size_mb": round(size_mb, 1),
        "ok": False,
    }
    if size_mb > MAX_MB and not allow_large:
        base["reason"] = f"larger than {MAX_MB:.0f} MB; skipped"
        return base
    conn = connect_ro(db_path)
    try:
        tables = set(list_tables(conn))
        if "mmc5983" not in tables and "icm45686_accel" not in tables:
            base["reason"] = "no mag and no cabin IMU"
            return base
        df, meta = load_epochs(conn)
        base["seconds_span"] = (
            int(df["ep"].iloc[-1] - df["ep"].iloc[0]) if len(df) else 0
        )
        base["declination_deg"] = meta.get("declination_deg")
        base["wmm_h_ut"] = meta.get("wmm_h_ut")
        base["wmm_z_ut"] = meta.get("wmm_z_ut")
        base["wmm_f_ut"] = meta.get("wmm_f_ut")
        if "mmc5983" in tables:
            base["mag"] = analyze_mag(df, meta)
        else:
            base["mag"] = {"ok": False, "reason": "no mmc5983"}
        if "icm45686_accel" in tables:
            base["mount"] = analyze_mount(df, meta)
        else:
            base["mount"] = {"ok": False, "reason": "no cabin IMU"}
        comp = compass_vs_track(df)
        if comp is not None:
            base["compass"] = comp
    finally:
        conn.close()
    base["ok"] = bool((base.get("mag") or {}).get("ok") or (base.get("mount") or {}).get("ok"))
    base["notes"] = narrate(base)
    base["elapsed_s"] = round(time.perf_counter() - t0, 2)
    return clean(base)


def analyze_soak(db_path: Path) -> dict[str, Any]:
    """Desktop / hangar session: mag wander, and an ellipsoid if it was tumbled."""
    t0 = time.perf_counter()
    size_mb = db_path.stat().st_size / 1e6
    out: dict[str, Any] = {"file": db_path.name, "size_mb": round(size_mb, 1), "kind": "soak"}
    if size_mb > 800:
        out["ok"] = False
        out["reason"] = "soak larger than 800 MB; skipped"
        return out
    conn = connect_ro(db_path)
    try:
        if "mmc5983" not in set(list_tables(conn)):
            out["ok"] = False
            out["reason"] = "no mmc5983"
            return out
        quick = soak_quick(conn)
        out["mag_still"] = quick
        out["ok"] = bool(quick.get("ok"))
        if quick.get("moved") and size_mb <= 400:
            df, meta = load_epochs(conn)
            if "mag_x_ut" in df.columns:
                mag = df[["mag_x_ut", "mag_y_ut", "mag_z_ut"]].to_numpy(dtype=float)
                mag = mag[np.isfinite(mag).all(axis=1)]
                if len(mag) > 4000:
                    step = int(len(mag) / 4000)
                    mag = mag[::step]
                ell = fit_ellipsoid(mag)
                if ell and ell["residual_rms"] < 0.08 and max(ell["radii_ut"]) < 200:
                    out["ellipsoid"] = ell
                del meta
    finally:
        conn.close()
    wander = (out.get("mag_still") or {}).get("wander_rms_ut")
    spread = (out.get("mag_still") or {}).get("spread_ut")
    if wander is not None:
        out["notes"] = [
            f"Desk mag wander {wander:.2f} µT rms, peak-to-peak spread {spread:.1f} µT."
        ]
        if out.get("ellipsoid"):
            c = out["ellipsoid"]["center_ut"]
            out["notes"].append(
                f"The sensor was rotated enough for an ellipsoid: center "
                f"[{c[0]:+.1f}, {c[1]:+.1f}, {c[2]:+.1f}] µT, "
                f"radii {[round(r, 1) for r in out['ellipsoid']['radii_ut']]}."
            )
    out["elapsed_s"] = round(time.perf_counter() - t0, 2)
    return clean(out)


def load_cached(db_path: Path, cache_dir: Path, *, refresh: bool = False) -> dict[str, Any]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    dest = cache_dir / f"{db_path.stem}.json"
    if dest.is_file() and not refresh:
        return json.loads(dest.read_text())
    result = analyze_session(db_path)
    dest.write_text(json.dumps(result))
    return result


def _span(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    return float(max(values) - min(values))


def _circ_span_deg(values: list[float]) -> float | None:
    """Smallest arc that covers every angle. Independent of sample order."""
    if len(values) < 2:
        return None
    a = np.sort(np.mod(np.asarray(values, dtype=float), 360.0))
    gaps = np.diff(a, append=a[0] + 360.0)
    return float(360.0 - float(gaps.max()))


def fleet_recommendation(rows: list[dict[str, Any]]) -> list[str]:
    roll_rows: list[tuple[float, float, str]] = []
    yaws: list[float] = []
    yaw_trusted = 0
    n_turns = 0
    mount_n = 0
    hdgs: list[float] = []
    irons: list[np.ndarray] = []
    banks: list[float] = []
    comp_std: list[float] = []
    comp_near = 0
    n_shaped = 0
    n_large = 0
    for r in rows:
        if r.get("kind") == "soak":
            continue
        mount = r.get("mount") or {}
        if mount.get("ok"):
            mount_n += 1
            # Straight taxi pins the up axis to the airplane. Parked-only
            # time can be a desk before the flight.
            on_airplane = int(mount.get("straight_s") or 0) >= 15
            if int(mount.get("turn_s") or 0) >= 12 and on_airplane:
                n_turns += 1
            if mount.get("quality") in ("good", "fair") and on_airplane:
                roll_rows.append((float(mount["roll_deg"]), float(mount["pitch_deg"]), str(r.get("file"))))
            if mount.get("yaw_observable") and mount.get("yaw_deg") is not None and on_airplane:
                yaw_trusted += 1
                yaws.append(float(mount["yaw_deg"]))
            if mount.get("ahrs_bank_scatter_deg") is not None:
                banks.append(float(mount["ahrs_bank_scatter_deg"]))
        mag = r.get("mag") or {}
        if mag.get("quality") in ("good", "fair") and mag.get("heldout_heading_rms_deg") is not None:
            hdgs.append(float(mag["heldout_heading_rms_deg"]))
        if mag.get("hard_iron_ut") and mag.get("quality") in ("good", "fair"):
            irons.append(np.array(mag["hard_iron_ut"], dtype=float))
        comp = r.get("compass") or {}
        if comp.get("ok") and comp.get("std_deg") is not None:
            comp_std.append(float(comp["std_deg"]))
            if (comp.get("within_15_of_track") or 0) >= 0.5:
                comp_near += 1
        for e in (mag.get("transients") or []):
            kind = e.get("kind", "")
            if "glitch" in kind:
                continue
            if "large" in kind:
                n_large += 1
            elif "blip" in kind or "step" in kind:
                n_shaped += 1

    lines: list[str] = []
    rolls = [t[0] for t in roll_rows]
    pitches = [t[1] for t in roll_rows]
    roll_span = _span(rolls)
    pitch_span = _span(pitches)
    if roll_span is None:
        lines.append("Not enough still-ground time to judge whether reinstalling the box moves the IMU.")
    else:
        lines.append(
            f"Across {len(rolls)} sessions with a usable gravity reference, ground roll spans "
            f"{roll_span:.1f}° and ground pitch spans {pitch_span:.1f}°. "
            "Pitch includes the airplane's sit angle, which should repeat at the same ramp, "
            "so most of a multi-degree pitch span is the box going back in differently."
        )
        if len(roll_rows) >= 4 and roll_span > 15:
            extreme = max(roll_rows, key=lambda t: abs(t[0]))
            others = [t[0] for t in roll_rows if t[2] != extreme[2]]
            other_span = _span(others)
            if other_span is not None:
                lines.append(
                    f"`{extreme[2]}` is the far one, at roll {extreme[0]:+.0f}°. "
                    "The live AHRS on that recording read about the same roll, so the box really was leaned. "
                    f"The other {len(others)} sessions span {other_span:.1f}° of roll."
                )
        if roll_span > 3.0 or (pitch_span or 0) > 3.0:
            lines.append(
                "The cabin box does not repeat. The live AHRS assumes the IMU axes are the "
                "aircraft axes, so the horizon zero moves by that span from flight to flight. "
                "Bolt the box to the airframe if you want the live horizon to mean the same "
                "thing every time you turn it on. Post-flight estimation fixes the recording. "
                "It does not change the horizon the AHRS showed in the cockpit that day. "
                f"Yaw was observable on {yaw_trusted} of {n_turns} sessions that had a taxi turn. "
                "On the others the two halves of the turn did not agree, so yaw was withheld. "
                "Roll and pitch only need the airplane sitting still, or taxiing straight, for about half a minute."
            )
        elif roll_span < 1.5 and (pitch_span or 0) < 1.5:
            lines.append(
                "Reinstalls are repeating to within a degree and a half. A rigid mount would "
                "buy less than that. Post-flight removal of the residual is worth doing for "
                "analysis and is not required to make the live horizon usable."
            )
        else:
            lines.append(
                "Repeatability is a few degrees. Bolt the box down if that horizon zero matters "
                "in the cockpit. The recordings are already good enough to take the offset out afterwards."
            )
    if yaws:
        yaw_span = _circ_span_deg(yaws)
        lines.append(
            f"IMU yaw relative to the nose spans {yaw_span:.1f}° across {len(yaws)} sessions "
            "where a taxi turn made it observable. That is a rotation about the vertical "
            "between installs. The live AHRS does not apply it, so it reads the IMU pitch "
            "axis as the aircraft pitch."
        )
    if hdgs:
        lines.append(
            f"Where the taxi swing covered enough headings, held-out magnetic heading error "
            f"has median {float(np.median(hdgs)):.1f}° (n={len(hdgs)}). "
            "That is the compass you can get from this pod after a per-flight taxi calibration, "
            "on the ground, with no wind correction."
        )
    if len(irons) >= 2:
        stack = np.vstack(irons)
        spread = stack.max(axis=0) - stack.min(axis=0)
        lines.append(
            f"Hard iron across {len(irons)} acceptable fits spans "
            f"[{spread[0]:.1f}, {spread[1]:.1f}, {spread[2]:.1f}] µT in the sensor frame. "
            + (
                "The vertical component changes sign across the set, which is what a rotated pod does even if the nearby iron did not move. "
                if (stack[:, 2] > 0).any() and (stack[:, 2] < 0).any()
                else ""
            )
            + (
                "Keep the calibration per flight."
                if float(np.linalg.norm(spread)) > 8.0
                else "The offset is fairly stable. A single calibration would have survived these flights, with the per-flight fit still the better record."
            )
        )
    if banks:
        lines.append(
            f"In turning flight the live AHRS bank matches a coordinated turn to "
            f"median {float(np.median(banks)):.1f}° once a per-flight bias is removed "
            f"(n={len(banks)}). That bias is the horizon zero of the install, and it is not the same number every flight."
        )
    if comp_std:
        lines.append(
            f"The live compass on straight taxi does not follow the track. "
            f"Scatter around its own bias has median {float(np.median(comp_std)):.0f}° "
            f"across {len(comp_std)} sessions, and {comp_near} of those stay within 15° of the track for half the time. "
            "The taxi calibration above is an offline correction. The cockpit compass does not use it."
        )
    if n_shaped or n_large:
        lines.append(
            f"{n_shaped} parked or straight-taxi changes between 2.5 and 8 µT have the shape of a flap, gear, "
            f"or trim motor (a blip that returns, or a step that stays). {n_large} changes are "
            "8 µT or larger, which is too big for those motors: hangar steel, the pod being moved, "
            "or a bad sample. Nothing in the log names the circuit. Clicking an event in the "
            "browser zooms the raw mag, track, and AHRS yaw."
        )
    n_soak = sum(1 for r in rows if r.get("kind") == "soak")
    n_ell = sum(1 for r in rows if r.get("kind") == "soak" and r.get("ellipsoid"))
    if n_soak and not n_ell:
        lines.append(
            f"None of the {n_soak} desktop sessions produced an ellipsoid that passed the residual gate. "
            "A spread of tens of µT there is a sensor that was picked up or a leftover spike, not a clean tumble."
        )
    lines.append(
        "A taxi swing is a circle, not a sphere. Vertical soft iron is not separable from a scale "
        "factor, and the fit assumes the vertical scale equals the mean horizontal scale. "
        "Tumble the pod once on the bench if you want a real 3-axis soft-iron matrix. "
        "The ellipsoid fit is wired for a desktop session whose mag vector actually moves."
    )
    return lines


def _fmt(v: Any, nd: int = 1) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


def write_report(rows: list[dict[str, Any]], path: Path, *, skipped: list[str]) -> None:
    flights = [r for r in rows if r.get("kind") != "soak"]
    soaks = [r for r in rows if r.get("kind") == "soak"]
    rec = fleet_recommendation(rows)
    lines = [
        "# Magnetometer and IMU mount",
        "",
        f"_Generated {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} "
        "by `analyze_flights.py cal`. Raw flight databases are not modified._",
        "",
        "## What was fit",
        "",
        "Groundspeed in the databases is m/s. Taxi is 4–23 m/s (about 8–45 kt): "
        "the GPS track is the heading, because there is no wind correction angle "
        "and the airplane is still on the surface. The wing-pod MMC5983 then swings "
        "the Earth field through a circle. The fit is",
        "",
        "```",
        "m = A · [cos ψ, sin ψ, 1]",
        "```",
        "",
        "with ψ the magnetic heading (true track minus `geo.declination`). "
        "Horizontal hard iron, horizontal soft-iron scale, and a yaw of the pod "
        "are in that matrix. The vertical part of the hard iron is separated from "
        "the Earth-field dip only by assuming the World Magnetic Model vertical "
        "component and a vertical scale equal to the mean horizontal scale. "
        "The sphere residual in flight is the check on that assumption.",
        "",
        "The cabin IMU is a separate rigid body. It is removed between flights. "
        "`R` maps an IMU vector into a local-level frame aligned with the ground "
        "track when a taxi turn was available. Roll and pitch come from gravity "
        "while the airplane is parked or taxiing straight. They include the "
        "deck angle of the airplane sitting on its gear. Yaw comes from the turn.",
        "",
        "Live AHRS is fed the IMU axes as though they were the aircraft axes. "
        "Its bank is compared with `atan(v · dψ/dt / g)` in turning flight. "
        "The bias is the horizon zero of that install. The scatter after removing "
        "the bias is how well the horizon tracks.",
        "",
        "## Recommendation",
        "",
    ]
    for s in rec:
        lines.append(s)
        lines.append("")
    if skipped:
        lines.append(
            "Skipped as too large (recorder left running): " + ", ".join(f"`{s}`" for s in skipped) + "."
        )
        lines.append("")
    lines += [
        "## Flights and taxi sessions",
        "",
        "| Session | Mag | Hdg RMS ° | Span ° | Hard iron µT | Scale | Events | Roll ° | Pitch ° | Yaw ° | AHRS bank scatter ° |",
        "|---|---|---:|---:|---|---|---:|---:|---:|---:|---:|",
    ]
    for r in flights:
        mag = r.get("mag") or {}
        mount = r.get("mount") or {}
        b = mag.get("hard_iron_ut")
        btxt = "—" if not b else f"[{b[0]:+.1f}, {b[1]:+.1f}, {b[2]:+.1f}]"
        scale = "—"
        if mag.get("scale_x") is not None:
            scale = f"{mag['scale_x']:.2f}/{mag['scale_y']:.2f}"
        lines.append(
            "| `{file}` | {q} | {rms} | {span} | {b} | {sc} | {ev} | {roll} | {pitch} | {yaw} | {bank} |".format(
                file=r.get("file"),
                q=mag.get("quality") or mag.get("reason") or "—",
                rms=_fmt(mag.get("heldout_heading_rms_deg")),
                span=_fmt(mag.get("heading_span_deg"), 0),
                b=btxt,
                sc=scale,
                ev=sum("glitch" not in e.get("kind", "") for e in (mag.get("transients") or [])),
                roll=_fmt(mount.get("roll_deg")),
                pitch=_fmt(mount.get("pitch_deg")),
                yaw=_fmt(mount.get("yaw_deg")) if mount.get("yaw_observable") else "—",
                bank=_fmt(mount.get("ahrs_bank_scatter_deg")),
            )
        )
    lines += ["", "## Desktop soaks", ""]
    if not soaks:
        lines.append("No desktop sessions were scanned.")
    else:
        lines += [
            "| Session | Wander µT | Spread µT | Ellipsoid center µT |",
            "|---|---:|---:|---|",
        ]
        for r in soaks:
            st = r.get("mag_still") or {}
            ell = r.get("ellipsoid") or {}
            c = ell.get("center_ut")
            ctxt = "—" if not c else f"[{c[0]:+.1f}, {c[1]:+.1f}, {c[2]:+.1f}]"
            lines.append(
                f"| `{r.get('file')}` | {_fmt(st.get('wander_rms_ut'), 2)} | {_fmt(st.get('spread_ut'), 1)} | {ctxt} |"
            )
    lines += [
        "",
        "## Notes for the next pass",
        "",
        "- `analysis/windows.py` compares raw `gps.gs` with 5 and 40 as if they were knots. "
        "The column is m/s, so those labels are about 10 kt and 78 kt. The calibration here "
        "does not use the window parquet. Fix the scale before trusting window labels for taxi.",
        "- Naming a transient as flap or gear needs either a pilot mark in howgozit or a current "
        "sense on that circuit. The shape is already in the table.",
        "- Engine RPM from the JPI dumps is not time-aligned yet. A steady mag offset that follows "
        "RPM would be alternator or ignition hard iron, distinct from the short motor events.",
        "- One bench tumble of the pod, the same six-face ritual as the cabin accel, would replace "
        "the vertical-scale assumption with a measured soft-iron matrix.",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")


def plot_fleet(rows: list[dict[str, Any]], out_dir: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    xs = []
    roll, pitch, yaw = [], [], []
    hdg, iron = [], []
    labels = []
    for r in rows:
        if r.get("kind") == "soak":
            continue
        name = r.get("file") or ""
        try:
            t = datetime.strptime(name[:15], "%Y%m%dT%H%M%S")
        except ValueError:
            continue
        mount = r.get("mount") or {}
        mag = r.get("mag") or {}
        if not mount.get("ok") and not mag.get("ok"):
            continue
        xs.append(t)
        labels.append(name[4:13])
        roll.append(mount.get("roll_deg"))
        pitch.append(mount.get("pitch_deg"))
        yaw.append(mount.get("yaw_deg") if mount.get("yaw_observable") else None)
        hdg.append(mag.get("heldout_heading_rms_deg"))
        b = mag.get("hard_iron_ut")
        iron.append(float(np.linalg.norm(b)) if b else None)
    if not xs:
        return
    out_dir.mkdir(parents=True, exist_ok=True)

    def _scatter(path: Path, series: list[tuple[list, str]], ylab: str, title: str) -> None:
        fig, ax = plt.subplots(figsize=(9.5, 4.2))
        for ys, lab in series:
            ax.plot(xs, ys, "o-", label=lab, markersize=4)
        ax.set_ylabel(ylab)
        ax.set_title(title)
        ax.grid(True, alpha=0.3)
        ax.legend(frameon=False)
        fig.autofmt_xdate()
        fig.tight_layout()
        fig.savefig(path, dpi=120)
        plt.close(fig)

    _scatter(
        out_dir / "mount_angles.png",
        [(roll, "roll"), (pitch, "pitch"), (yaw, "yaw")],
        "degrees",
        "IMU vs gravity (yaw only when a taxi turn observed it)",
    )
    _scatter(
        out_dir / "mag_heading.png",
        [(hdg, "held-out heading RMS"), (iron, "|hard iron| µT")],
        "degrees or µT",
        "Taxi magnetometer fit",
    )


def run_fleet(
    flights_dir: Path,
    catalog_path: Path,
    out_dir: Path,
    report_path: Path,
    *,
    only_file: str | None = None,
    allow_large: bool = False,
    soaks: bool = True,
) -> dict[str, Any]:
    """Calibrate every flight and taxi session, plus a light pass over soaks."""
    catalog = json.loads(catalog_path.read_text()) if catalog_path.is_file() else {"sessions": []}
    sessions = catalog.get("sessions") or []
    out_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    skipped: list[str] = []

    def _one(entry: dict[str, Any], soak: bool) -> None:
        name = entry["file"]
        path = flights_dir / name
        if not path.is_file():
            return
        print(f"{'soak' if soak else 'cal'} {name} ({entry.get('size_mb', 0):.0f} MB) …", flush=True)
        try:
            if soak:
                result = analyze_soak(path)
            else:
                if entry.get("size_mb", 0) > MAX_MB and not allow_large:
                    skipped.append(name)
                    result = {
                        "file": name,
                        "ok": False,
                        "reason": f"larger than {MAX_MB:.0f} MB; skipped",
                        "size_mb": entry.get("size_mb"),
                    }
                else:
                    result = analyze_session(path, allow_large=allow_large)
        except Exception as e:  # noqa: BLE001 — one bad DB should not stop the fleet
            traceback.print_exc()
            result = {"file": name, "ok": False, "reason": str(e)}
        result["class"] = entry.get("class")
        if soak:
            result["kind"] = "soak"
        (out_dir / f"{Path(name).stem}.json").write_text(json.dumps(result))
        rows.append(result)
        note = (result.get("notes") or [result.get("reason") or ""])[0]
        print(f"  {note}", flush=True)

    if only_file:
        name = Path(only_file).name
        entry = next((s for s in sessions if s["file"] == name), None)
        if entry is None:
            entry = {"file": name, "class": "unknown", "size_mb": (flights_dir / name).stat().st_size / 1e6}
        _one(entry, soak=False)
    else:
        for s in sessions:
            if s.get("class") not in ("flight", "taxi_only"):
                continue
            sens = set(s.get("sensors") or [])
            if "mmc5983" not in sens and "icm45686_accel" not in sens:
                continue
            _one(s, soak=False)
        if soaks:
            for s in sessions:
                if s.get("class") != "soak":
                    continue
                if "mmc5983" not in set(s.get("sensors") or []):
                    continue
                if (s.get("size_mb") or 0) > 800:
                    continue
                _one(s, soak=True)

    summary = {
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "n": len(rows),
        "skipped": skipped,
        "recommendation": fleet_recommendation(rows),
        "sessions": [
            {
                "file": r.get("file"),
                "class": r.get("class"),
                "kind": r.get("kind"),
                "ok": r.get("ok"),
                "mag_quality": (r.get("mag") or {}).get("quality"),
                "heading_rms_deg": (r.get("mag") or {}).get("heldout_heading_rms_deg"),
                "heading_span_deg": (r.get("mag") or {}).get("heading_span_deg"),
                "hard_iron_ut": (r.get("mag") or {}).get("hard_iron_ut"),
                "scale_x": (r.get("mag") or {}).get("scale_x"),
                "scale_y": (r.get("mag") or {}).get("scale_y"),
                "n_events": sum(
                    "glitch" not in e.get("kind", "")
                    for e in ((r.get("mag") or {}).get("transients") or [])
                ),
                "roll_deg": (r.get("mount") or {}).get("roll_deg"),
                "pitch_deg": (r.get("mount") or {}).get("pitch_deg"),
                "yaw_deg": (r.get("mount") or {}).get("yaw_deg"),
                "yaw_observable": (r.get("mount") or {}).get("yaw_observable"),
                "ahrs_bank_bias_deg": (r.get("mount") or {}).get("ahrs_bank_bias_deg"),
                "ahrs_bank_scatter_deg": (r.get("mount") or {}).get("ahrs_bank_scatter_deg"),
                "ahrs_roll_taxi_deg": (r.get("mount") or {}).get("ahrs_roll_taxi_deg"),
                "compass_std_deg": (r.get("compass") or {}).get("std_deg"),
                "soak_wander_ut": (r.get("mag_still") or {}).get("wander_rms_ut"),
                "notes": r.get("notes"),
            }
            for r in rows
        ],
    }
    (out_dir / "summary.json").write_text(json.dumps(clean(summary)))
    write_report(rows, report_path, skipped=skipped)
    try:
        plot_fleet(rows, out_dir)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
    return summary
