"""Air-data reduction for one kingfisher flight database.

The equations match ``internal/derive`` (compressible calibrated airspeed,
isentropic true airspeed, ISA density altitude). Nothing here is a validated
model of a Bonanza. Sensor temperature is used as written; it is not a
recovery correction. Flights recorded before the compressible derive change
should be recomputed from impact pressure rather than trusted as ``ias_kt``.

Required inputs for a full pass, all on the Pi ``ts_ns`` clock
(see ``docs/timestamps.md``):

- ``gps.gs`` (m/s), ``gps.track`` (deg true, only while moving)
- ``ms4525.airspeed_dp_pa`` and/or ``airspeed.airspeed_dp_cal_pa`` (Pa)
- ``bmp581.static_pressure_pa`` (Pa) and ``bmp581.static_temp_c`` (°C)
- ``press_alt.vs_ms`` (m/s) for rate of climb
- ``compass.heading_true_deg`` for a wind triangle

Missing tables are reported. The function does not invent them.
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass, field

from analysis.db import list_tables, table_columns

# Same constants as internal/derive/airspeed.go and altitude.go.
MPS_PER_KT = 1.0 / 1.94384
GAMMA = 1.4
DRY_AIR_R = 287.053
ISA_P0_PA = 101325.0
ISA_T0_K = 288.15
PRESS_ALT_SCALE_FT = 145366.45
PRESS_ALT_EXP = 0.190284
ISA_P0_HPA = 1013.25
TROPOPAUSE_M = 11000.0
LAPSE_K_PER_M = 0.0065
G = 9.80665
M_PER_FT = 0.3048

FLIGHT_GS_KT = 40.0
CRUISE_VS_MPS = 0.5
CLIMB_VS_MPS = 1.5
JOIN_NS = 1_000_000_000


def cas_kt(qc_pa: float) -> float | None:
    """Calibrated airspeed from impact pressure. Sea-level ISA ASI equation."""
    return _isentropic_kt(qc_pa, ISA_P0_PA, ISA_T0_K)


def tas_kt(qc_pa: float, static_pa: float, temp_c: float) -> float | None:
    """Isentropic true airspeed at the given static pressure and temperature."""
    if not _plausible_static_pa(static_pa) or not _plausible_temp_c(temp_c):
        return None
    return _isentropic_kt(qc_pa, static_pa, temp_c + 273.15)


def density_alt_ft(static_pa: float, temp_c: float) -> float | None:
    """ISA density altitude. Troposphere, then isothermal stratosphere."""
    if not _plausible_static_pa(static_pa) or not _plausible_temp_c(temp_c):
        return None
    t_k = temp_c + 273.15
    rho = static_pa / (DRY_AIR_R * t_k)
    rho0 = ISA_P0_PA / (DRY_AIR_R * ISA_T0_K)
    if rho <= 0 or rho0 <= 0:
        return None
    sigma = rho / rho0
    exp = G / (DRY_AIR_R * LAPSE_K_PER_M) - 1.0
    t_trop = ISA_T0_K - LAPSE_K_PER_M * TROPOPAUSE_M
    sigma_trop = (t_trop / ISA_T0_K) ** exp
    if sigma >= sigma_trop:
        theta = sigma ** (1.0 / exp)
        h_m = (ISA_T0_K / LAPSE_K_PER_M) * (1.0 - theta)
    else:
        rho_trop = rho0 * sigma_trop
        h_m = TROPOPAUSE_M + (DRY_AIR_R * t_trop / G) * math.log(rho_trop / rho)
    if math.isnan(h_m):
        return None
    return h_m / M_PER_FT


def pressure_alt_ft(static_pa: float) -> float | None:
    """US standard-atmosphere pressure altitude, feet. Matches bmp280.CalcAltitude."""
    if not _plausible_static_pa(static_pa):
        return None
    hpa = static_pa / 100.0
    return PRESS_ALT_SCALE_FT * (1.0 - (hpa / ISA_P0_HPA) ** PRESS_ALT_EXP)


def specific_excess_power_mps(roc_mps: float, tas_mps: float, dtas_dt: float) -> float:
    """Ps = dh/dt + (V/g) dV/dt, meters per second. Steady speed → Ps = ROC."""
    return roc_mps + (tas_mps / G) * dtas_dt


def wind_ned_kt(
    tas_kt_: float, heading_true_deg: float, gs_kt: float, track_true_deg: float
) -> tuple[float, float]:
    """Wind from north and east, knots. Wind = ground velocity − air velocity.

    Heading and track are degrees true, clockwise from north. This is geometry.
    It is a wind estimate only when heading, TAS, and track are simultaneous
    and the airplane is not accelerating.
    """
    h = math.radians(heading_true_deg)
    t = math.radians(track_true_deg)
    vn = gs_kt * math.cos(t) - tas_kt_ * math.cos(h)
    ve = gs_kt * math.sin(t) - tas_kt_ * math.sin(h)
    return vn, ve


@dataclass
class PerfSummary:
    notes: list[str] = field(default_factory=list)
    n_gps: int = 0
    max_gs_kt: float | None = None
    n_flight: int = 0
    n_cruise: int = 0
    n_climb: int = 0
    cruise_gs_kt: float | None = None
    cruise_cas_kt: float | None = None
    cruise_tas_kt: float | None = None
    cruise_da_ft: float | None = None
    cruise_pa_ft: float | None = None
    climb_roc_fpm: float | None = None
    climb_ps_mps: float | None = None
    climb_ps_note: str = ""
    wind_n_kt: float | None = None
    wind_e_kt: float | None = None
    n_wind: int = 0

    def text(self) -> str:
        lines = [
            "Kingfisher performance reduction",
            "Equations only — not a validated Bonanza model.",
            "Temperature is the logged sensor value, not recovery-corrected OAT.",
        ]
        lines.extend(f"- {n}" for n in self.notes)
        lines.append(f"GPS samples: {self.n_gps}")
        if self.max_gs_kt is not None:
            lines.append(f"Max groundspeed: {self.max_gs_kt:.1f} kt")
        lines.append(
            f"Samples gs≥{FLIGHT_GS_KT:.0f} kt: {self.n_flight} "
            f"(cruise |vs|<{CRUISE_VS_MPS} m/s: {self.n_cruise}, "
            f"climb vs>{CLIMB_VS_MPS} m/s: {self.n_climb})"
        )
        lines.append(
            "Cruise medians: "
            + _fmt(
                [
                    ("GS kt", self.cruise_gs_kt),
                    ("CAS kt", self.cruise_cas_kt),
                    ("TAS kt", self.cruise_tas_kt),
                    ("PA ft", self.cruise_pa_ft),
                    ("DA ft", self.cruise_da_ft),
                ]
            )
        )
        if self.climb_roc_fpm is not None:
            lines.append(f"Climb median ROC: {self.climb_roc_fpm:.0f} ft/min")
        if self.climb_ps_mps is not None:
            lines.append(
                f"Climb specific excess power: {self.climb_ps_mps:.2f} m/s"
                + (f" ({self.climb_ps_note})" if self.climb_ps_note else "")
            )
        if self.n_wind:
            lines.append(
                f"Cruise wind (north, east): {self.wind_n_kt:.1f}, {self.wind_e_kt:.1f} kt "
                f"over {self.n_wind} samples — geometry, not a checked wind box"
            )
        return "\n".join(lines) + "\n"


def summarize(conn: sqlite3.Connection, dp_zero_pa: float = 0.0) -> PerfSummary:
    """Reduce one flight. ``dp_zero_pa`` is subtracted from raw MS4525 ΔP only."""
    out = PerfSummary()
    tables = set(list_tables(conn))
    gps = _load(conn, tables, "gps", ("ts_ns", "gs", "track"))
    out.n_gps = len(gps)
    if not gps:
        out.notes.append("No gps rows. Groundspeed, track, and airborne time are unavailable.")
        return out
    # gps.gs is m/s. MPS_PER_KT is m/s per knot (1/1.94384).
    gs_kt = [r["gs"] / MPS_PER_KT for r in gps if r.get("gs") is not None and r["gs"] >= 0]
    if gs_kt:
        out.max_gs_kt = max(gs_kt)

    dp = _load_dp(conn, tables, dp_zero_pa, out)
    static = _load(conn, tables, "bmp581", ("ts_ns", "static_pressure_pa", "static_temp_c"))
    if not static:
        out.notes.append(
            "No bmp581 static pressure/temperature. TAS and density altitude need both."
        )
    press = _load(conn, tables, "press_alt", ("ts_ns", "vs_ms", "pressure_alt_ft"))
    if not press:
        out.notes.append("No press_alt.vs_ms. Climb rate is unavailable.")
    compass = _load(conn, tables, "compass", ("ts_ns", "heading_true_deg"))
    if not compass:
        out.notes.append(
            "No compass.heading_true_deg. A wind triangle needs true heading."
        )

    cruise_gs: list[float] = []
    cruise_cas: list[float] = []
    cruise_tas: list[float] = []
    cruise_da: list[float] = []
    cruise_pa: list[float] = []
    climb_roc: list[float] = []
    climb_tas: list[tuple[int, float]] = []
    wind_n: list[float] = []
    wind_e: list[float] = []

    for row in gps:
        gs = row.get("gs")
        if gs is None:
            continue
        gsk = gs / MPS_PER_KT
        if gsk < FLIGHT_GS_KT:
            continue
        out.n_flight += 1
        ts = row["ts_ns"]
        vs = _near(press, ts, "vs_ms")
        qc = _near(dp, ts, "qc_pa")
        p = _near(static, ts, "static_pressure_pa")
        temp = _near(static, ts, "static_temp_c")
        cas = tas = da = pa = None
        if qc is not None and qc > 0:
            cas = cas_kt(qc)
        if qc is not None and p is not None and temp is not None:
            tas = tas_kt(qc, p, temp)
            da = density_alt_ft(p, temp)
            pa = pressure_alt_ft(p)
        if vs is not None and abs(vs) < CRUISE_VS_MPS:
            out.n_cruise += 1
            cruise_gs.append(gsk)
            _append(cruise_cas, cas)
            _append(cruise_tas, tas)
            _append(cruise_da, da)
            _append(cruise_pa, pa)
            hdg = _near(compass, ts, "heading_true_deg")
            trk = row.get("track")
            if tas is not None and hdg is not None and trk is not None:
                vn, ve = wind_ned_kt(tas, hdg, gsk, trk)
                wind_n.append(vn)
                wind_e.append(ve)
        elif vs is not None and vs > CLIMB_VS_MPS:
            out.n_climb += 1
            climb_roc.append(vs)
            if tas is not None:
                climb_tas.append((ts, tas / MPS_PER_KT))

    out.cruise_gs_kt = _median(cruise_gs)
    out.cruise_cas_kt = _median(cruise_cas)
    out.cruise_tas_kt = _median(cruise_tas)
    out.cruise_da_ft = _median(cruise_da)
    out.cruise_pa_ft = _median(cruise_pa)
    if climb_roc:
        med = _median(climb_roc)
        assert med is not None
        out.climb_roc_fpm = med / M_PER_FT * 60.0
        if len(climb_tas) >= 2:
            t0, v0 = climb_tas[0]
            t1, v1 = climb_tas[-1]
            dt = (t1 - t0) / 1e9
            if dt > 1.0:
                dtas = (v1 - v0) / dt
                mean_v = 0.5 * (v0 + v1)
                out.climb_ps_mps = specific_excess_power_mps(med, mean_v, dtas)
                out.climb_ps_note = "ROC plus segment dTAS/dt; noisy if the climb is not steady"
            else:
                out.climb_ps_mps = med
                out.climb_ps_note = "short climb; kinetic term omitted"
        else:
            out.climb_ps_mps = med
            out.climb_ps_note = "no TAS; Ps reported as ROC only"
    if wind_n:
        out.n_wind = len(wind_n)
        out.wind_n_kt = _median(wind_n)
        out.wind_e_kt = _median(wind_e)
    if out.n_flight == 0:
        out.notes.append(
            f"No samples at or above {FLIGHT_GS_KT:.0f} kt groundspeed. "
            "This session has no airborne segment under that rule."
        )
    if out.n_cruise and out.cruise_cas_kt is None:
        out.notes.append(
            "Cruise samples have no impact pressure within 1 s. "
            "CAS/TAS need ms4525 or airspeed.airspeed_dp_cal_pa."
        )
    return out


def _load_dp(conn, tables: set[str], dp_zero_pa: float, out: PerfSummary) -> list[dict]:
    if "airspeed" in tables and _has_cols(conn, "airspeed", ("ts_ns", "airspeed_dp_cal_pa")):
        rows = _load(conn, tables, "airspeed", ("ts_ns", "airspeed_dp_cal_pa"))
        for r in rows:
            r["qc_pa"] = r.get("airspeed_dp_cal_pa")
        out.notes.append(
            "Impact pressure is airspeed.airspeed_dp_cal_pa (zero already applied by the recorder)."
        )
        return rows
    if "ms4525" in tables and _has_cols(conn, "ms4525", ("ts_ns", "airspeed_dp_pa")):
        rows = _load(conn, tables, "ms4525", ("ts_ns", "airspeed_dp_pa"))
        for r in rows:
            v = r.get("airspeed_dp_pa")
            r["qc_pa"] = None if v is None else v - dp_zero_pa
        out.notes.append(
            f"Impact pressure is ms4525.airspeed_dp_pa minus dp_zero_pa={dp_zero_pa:.2f}. "
            "A same-day pitot zero belongs in that offset."
        )
        return rows
    out.notes.append("No pitot ΔP (ms4525 or airspeed.airspeed_dp_cal_pa). CAS and TAS are omitted.")
    return []


def _has_cols(conn, table: str, cols: tuple[str, ...]) -> bool:
    have = set(table_columns(conn, table))
    return all(c in have for c in cols)


def _load(conn, tables: set[str], table: str, cols: tuple[str, ...]) -> list[dict]:
    if table not in tables or not _has_cols(conn, table, cols):
        return []
    quoted = ", ".join(f'"{c}"' for c in cols)
    try:
        cur = conn.execute(
            f'SELECT {quoted} FROM "{table}" WHERE ts_ns IS NOT NULL ORDER BY ts_ns'
        )
    except sqlite3.Error:
        return []
    rows = []
    for rec in cur:
        rows.append(dict(zip(cols, rec)))
    return rows


def _near(rows: list[dict], ts: int, key: str) -> float | None:
    if not rows:
        return None
    lo, hi = 0, len(rows) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if rows[mid]["ts_ns"] < ts:
            lo = mid + 1
        else:
            hi = mid
    best = None
    best_dt = JOIN_NS + 1
    for i in (lo - 1, lo):
        if i < 0 or i >= len(rows):
            continue
        dt = abs(rows[i]["ts_ns"] - ts)
        if dt < best_dt:
            best_dt = dt
            best = rows[i]
    if best is None or best_dt > JOIN_NS:
        return None
    v = best.get(key)
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    return float(v)


def _append(bucket: list[float], v: float | None) -> None:
    if v is not None and not math.isnan(v):
        bucket.append(v)


def _median(xs: list[float]) -> float | None:
    if not xs:
        return None
    ys = sorted(xs)
    n = len(ys)
    mid = n // 2
    if n % 2:
        return ys[mid]
    return 0.5 * (ys[mid - 1] + ys[mid])


def _fmt(pairs: list[tuple[str, float | None]]) -> str:
    parts = []
    for name, val in pairs:
        parts.append(f"{name}=—" if val is None else f"{name}={val:.1f}")
    return "  ".join(parts)


def _plausible_static_pa(v: float) -> bool:
    return v is not None and not math.isnan(v) and 10_000 < v < 120_000


def _plausible_temp_c(v: float) -> bool:
    return v is not None and not math.isnan(v) and -100 < v < 200


def _isentropic_kt(qc_pa: float, static_pa: float, temp_k: float) -> float | None:
    if (
        qc_pa is None
        or static_pa is None
        or temp_k is None
        or math.isnan(qc_pa)
        or math.isnan(static_pa)
        or math.isnan(temp_k)
        or qc_pa <= 0
        or static_pa <= 0
        or temp_k <= 0
    ):
        return None
    inner = (qc_pa / static_pa + 1.0) ** ((GAMMA - 1.0) / GAMMA) - 1.0
    if inner <= 0 or math.isnan(inner):
        return None
    a = math.sqrt(GAMMA * DRY_AIR_R * temp_k)
    v_mps = a * math.sqrt((2.0 / (GAMMA - 1.0)) * inner)
    return v_mps / MPS_PER_KT
