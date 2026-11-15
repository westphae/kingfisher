"""AFMS-550 Rev K defaults for N456T's Whirlwind System III IO-550-B.

The numbers are the FAA-approved markings and normal procedures in
AFMS-550 Rev K (STC SA5223NM, FAA approved 2022-08-11), plus the
EDM-930-as-primary yellow/red MAP arcs on that same page. A few
review-only thresholds (how large a fuel-flow step counts as boost,
how fast a CHT fall is worth a note) are not printed in the AFMS.
Those are named as such and are the ones Eric is expected to edit.

Override by writing ``~/kingfisher/engine/limits.json`` with any subset
of these field names. Unknown keys are ignored.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class Limits:
    # --- phase detection (not AFMS limits; they only slice the flight) ---
    gs_airborne_kt: float = 40.0
    airborne_gap_s: float = 20.0
    airborne_agl_ft: float = 400.0
    engine_off_rpm: float = 400.0
    climb_vs_fpm: float = 300.0
    descent_vs_fpm: float = 250.0
    climb_rpm_min: float = 2500.0
    climb_map_min: float = 26.0
    climb_ff_min: float = 22.0
    takeoff_rpm_min: float = 2500.0
    takeoff_map_min: float = 27.0
    takeoff_ff_min: float = 24.0
    takeoff_min_s: float = 20.0
    takeoff_max_s: float = 75.0
    takeoff_agl_ft: float = 500.0
    level_off_lookback_s: float = 150.0
    landing_max_s: float = 240.0
    landing_pattern_ft: float = 1000.0
    phase_min_s: float = 15.0

    # --- taxi ---
    # Aggressive leaning is a low fuel flow at taxi RPM. Not an AFMS number.
    taxi_rpm_lo: float = 700.0
    taxi_rpm_hi: float = 1700.0
    taxi_lean_ff_gph: float = 6.0

    # --- mag check: AFMS before-takeoff, 1700 RPM ---
    mag_rpm_lo: float = 1550.0
    mag_rpm_hi: float = 1900.0
    mag_drop_min_rpm: float = 40.0
    mag_drop_max_rpm: float = 150.0  # AFMS: maximum drop 150 rpm
    mag_asym_rpm: float = 50.0  # AFMS: L vs R variance 50 rpm
    mag_egt_fall_f: float = 5.0  # a cylinder whose EGT falls on one mag
    mag_egt_mismatch_f: float = 40.0

    # --- takeoff: AFMS p.21–22 and the hot-weather table on p.31 ---
    takeoff_ff_below_80f: float = 35.0
    takeoff_ff_81_to_90f: float = 34.0
    takeoff_ff_above_90f: float = 33.0
    takeoff_ff_tolerance_gph: float = 1.5
    takeoff_ff_hi_boost_gph: float = 36.5  # above full-rich: HI boost is forbidden
    takeoff_tit_lo_f: float = 1250.0
    takeoff_tit_hi_f: float = 1320.0
    takeoff_map_normal_inhg: float = 29.6
    takeoff_map_yellow_inhg: float = 32.0  # EDM primary: yellow 29.6–32.0, red 32.0
    takeoff_map_red_inhg: float = 32.0
    takeoff_cold_oil_f: float = 170.0  # 30–32 in Hg acceptable briefly below this
    takeoff_cold_map_max_s: float = 90.0
    takeoff_oil_min_f: float = 75.0  # AFMS minimum recommended takeoff oil temp

    # --- climb: AFMS cruise-climb ---
    climb_rpm_target: float = 2700.0
    climb_rpm_lo: float = 2620.0  # 2650 is the noise-abatement setting
    climb_rpm_hi: float = 2720.0
    climb_ff_target_gph: float = 35.0
    climb_ff_lo_gph: float = 32.0
    climb_tit_lo_f: float = 1260.0
    climb_tit_hi_f: float = 1310.0  # do not exceed
    climb_ias_lo_kt: float = 115.0
    climb_ias_hi_kt: float = 120.0
    climb_ias_tolerance_kt: float = 8.0
    cht_yellow_f: float = 380.0
    cht_high_f: float = 410.0
    cht_red_f: float = 460.0

    # --- boost inference (Eric's 11/11 ground test, not an AFMS table) ---
    boost_lo_ff_gph: float = 0.5
    boost_lo_ff_lo: float = 0.30
    boost_lo_ff_hi: float = 1.3
    boost_lo_egt_f: float = 20.0
    boost_lo_egt_min_f: float = 8.0
    # The "LO lowered fuel flow" anomaly needs a clearer EGT rise than a
    # smooth mixture pull, which also drops FF a few tenths at a time.
    boost_lo_drop_egt_min_f: float = 15.0
    boost_hi_ff_gph: float = 3.5
    boost_hi_ff_lo: float = 2.0
    boost_hi_ff_hi: float = 6.0
    boost_hi_egt_f: float = 60.0
    boost_hi_egt_min_f: float = 30.0
    boost_hi_map_inhg: float = 0.2
    boost_da_lo_ft: float = 5000.0  # AFMS: LO above 5,000 ft density altitude
    boost_da_hi_ft: float = 10000.0  # AFMS: HI above 10,000 ft DA

    # --- vapor: review thresholds, not printed in the AFMS ---
    vapor_ff_std_gph: float = 0.35
    vapor_window_s: float = 30.0
    vapor_sag_gph: float = 1.0
    vapor_tit_rise_f: float = 30.0

    # --- level-off / leaning: AFMS LOP procedure ---
    level_off_min_s: float = 60.0
    level_off_max_s: float = 150.0  # 1–2 minutes; allow a little slack
    lean_min_s: float = 3.0
    lean_max_s: float = 8.0  # AFMS 4–6 seconds; slack for a 2 s sample
    cruise_rpm_floor: float = 2300.0
    cruise_rpm_floor_slack: float = 25.0  # 2 s samples sit a few rpm under the set
    cruise_ff_2300: tuple = (14.0, 16.0)
    cruise_ff_2500: tuple = (15.5, 17.5)
    cruise_wot_map_slack_inhg: float = 1.5
    lop_min_f: float = 60.0  # at least 60°F lean of peak
    lop_ideal_lo_f: float = 50.0
    lop_ideal_hi_f: float = 100.0
    peak_tit_typical_lo_f: float = 1580.0
    peak_tit_typical_hi_f: float = 1630.0
    lean_find_max_s: float = 120.0
    redbox_lop_f: float = 50.0
    redbox_rop_f: float = 125.0
    redbox_power_pct: float = 65.0
    rated_hp: float = 300.0
    lop_hp_per_gph: float = 14.9  # AFMS: HP = fuel flow × 14.9 when LOP
    rop_map_max_inhg: float = 25.0
    rop_rpm_max: float = 2500.0
    cruise_ff_stable_min: float = 30.0  # first 30 min; instability suggests LO boost
    tit_red_f: float = 1650.0

    # --- descent / landing / shutdown ---
    descent_cht_min_f: float = 240.0
    cht_cool_caution_f_per_min: float = 60.0
    descent_idle_rpm: float = 1200.0
    descent_idle_max_s: float = 60.0
    descent_map_step_inhg: float = 5.0
    descent_tit_near_peak_f: float = 80.0
    approach_ff_above_cruise_lo: float = 1.0
    approach_ff_above_cruise_hi: float = 2.0
    cooldown_idle_lo_rpm: float = 800.0
    cooldown_idle_hi_rpm: float = 1300.0
    cooldown_min_s: float = 10.0
    cooldown_max_s: float = 20.0
    high_power_ground_rpm: float = 1600.0

    # --- oil: AFMS instrument markings, EDM-as-primary ---
    oil_p_red_min: float = 10.0
    oil_p_yellow_hi: float = 30.0
    oil_p_green_hi: float = 60.0
    oil_p_red_max: float = 100.0
    oil_t_red_f: float = 240.0
    oil_t_green_lo_f: float = 100.0
    oil_t_caution_f: float = 220.0
    wastegate_oil_psi: float = 45.0  # about what the wastegate wants for rated MAP
    wastegate_alt_ft: float = 5000.0
    wastegate_map_min_inhg: float = 28.0

    # --- cylinders ---
    cht_spread_caution_f: float = 50.0
    egt_spread_caution_f: float = 150.0
    cht3_offset_caution_f: float = 30.0
    cht3_wiggle_caution_f: float = 12.0
    probe_wiggle_ratio: float = 3.0
    probe_wiggle_min_f: float = 8.0

    # --- duplicate / clock-jump flag (ingest, not an engine limit) ---
    duplicate_residual_note_m: float = 400.0


def default_limits_path() -> Path:
    return Path.home() / "kingfisher" / "engine" / "limits.json"


def load_limits(path: Path | None = None) -> Limits:
    """Load overrides. A missing file is the AFMS defaults, not an error."""
    lim = Limits()
    if path is None:
        candidate = default_limits_path()
        path = candidate if candidate.is_file() else None
    if path is None:
        return lim
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"limits: could not read {path}: {exc}", file=sys.stderr)
        return lim
    if not isinstance(data, dict):
        print(f"limits: {path} is not a JSON object", file=sys.stderr)
        return lim
    fields = asdict(lim)
    unknown = []
    for key, value in data.items():
        if key not in fields:
            unknown.append(key)
            continue
        current = getattr(lim, key)
        try:
            if isinstance(current, tuple):
                setattr(lim, key, tuple(float(v) for v in value))
            else:
                setattr(lim, key, type(current)(value))
        except (TypeError, ValueError) as exc:
            print(f"limits: ignored {key}: {exc}", file=sys.stderr)
    if unknown:
        print(f"limits: ignored unknown keys: {', '.join(unknown)}", file=sys.stderr)
    return lim


def limits_fingerprint(lim: Limits) -> str:
    return json.dumps(asdict(lim), sort_keys=True, default=list)


def takeoff_ff_target(oat_f: float | None, lim: Limits) -> float | None:
    """AFMS takeoff fuel flow for an OAT in °F. None when OAT is missing."""
    if oat_f is None:
        return None
    try:
        oat = float(oat_f)
    except (TypeError, ValueError):
        return None
    if oat != oat:  # NaN
        return None
    if oat <= 80.0:
        return lim.takeoff_ff_below_80f
    if oat <= 90.0:
        return lim.takeoff_ff_81_to_90f
    return lim.takeoff_ff_above_90f
