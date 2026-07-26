"""Generate docs/analysis/ledger.md from catalog (+ optional health)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CLASS_ORDER = (
    "flight",
    "taxi_only",
    "soak",
    "experiment",
    "no_info",
    "unknown",
)


def _health_by_file(health: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not health:
        return {}
    return {s["file"]: s for s in health.get("sessions", [])}


def render_ledger(
    catalog: dict[str, Any],
    *,
    health: dict[str, Any] | None = None,
    notes_preserve: str = "",
) -> str:
    gen = catalog.get("generated_utc") or datetime.now(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    by = catalog.get("by_class", {})
    sessions = catalog.get("sessions", [])
    hmap = _health_by_file(health)

    lines: list[str] = []
    lines.append("# Flight data ledger")
    lines.append("")
    lines.append(
        f"_Auto-generated {gen}. Re-run `python scripts/analyze_flights.py catalog` "
        f"(and `health` for flights). Hand notes live below the marker or in `*.md` sidecars._"
    )
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append("| Class | Count |")
    lines.append("|-------|------:|")
    for c in CLASS_ORDER:
        if c in by:
            lines.append(f"| `{c}` | {by[c]} |")
    for c, n in sorted(by.items()):
        if c not in CLASS_ORDER:
            lines.append(f"| `{c}` | {n} |")
    lines.append(f"| **total** | **{catalog.get('count', len(sessions))}** |")
    lines.append("")
    lines.append(
        "Classification: `flight` if GPS max groundspeed ≥ 40 kt (or sidecar override); "
        "`taxi_only` 5–40 kt; long stationary → `soak`; tiny/short → `no_info`. "
        "See [README](README.md)."
    )
    lines.append("")

    # Flights
    flights = [s for s in sessions if s.get("class") == "flight"]
    lines.append("## Flights")
    lines.append("")
    if not flights:
        lines.append("_None classified yet._")
        lines.append("")
    else:
        lines.append(
            "| File | Dur (h) | Max gs | Grade | Coverage | Airborne (min) | "
            "Pitot | Missing | Notes |"
        )
        lines.append(
            "|------|--------:|-------:|-------|----------|---------------:"
            "|:-----:|---------|-------|"
        )
        for s in sorted(flights, key=lambda x: x["file"]):
            h = hmap.get(s["file"], {})
            grade = h.get("grade", "—")
            cov = h.get("coverage", "—")
            air = h.get("airborne_min", "—")
            pitot = "Y" if s.get("has_pitot") else "—"
            miss = ", ".join(
                (s.get("missing_core_cabin") or []) + (s.get("missing_core_pod") or [])
            ) or "—"
            note_bits = []
            if s.get("notes_preview"):
                note_bits.append(s["notes_preview"][:60])
            if h.get("aligned_pod_gaps_gt_1s"):
                note_bits.append(f"pod gaps×{h['aligned_pod_gaps_gt_1s']}")
            for n in h.get("notes") or []:
                if "coverage" not in n:
                    note_bits.append(n[:40])
            notes = "; ".join(note_bits) if note_bits else "—"
            gs = s.get("max_gs")
            gs_s = f"{gs:.0f}" if isinstance(gs, (int, float)) else "—"
            lines.append(
                f"| `{s['file']}` | {s.get('duration_h', 0):.2f} | {gs_s} | "
                f"{grade} | {cov} | {air} | {pitot} | {miss} | {notes} |"
            )
        lines.append("")

    # Soaks / experiments
    for label, title in (
        ("soak", "Soaks (stationary / overnight / hangar)"),
        ("experiment", "Experiments"),
        ("taxi_only", "Taxi-only"),
    ):
        group = [s for s in sessions if s.get("class") == label]
        lines.append(f"## {title}")
        lines.append("")
        if not group:
            lines.append("_None._")
            lines.append("")
            continue
        lines.append("| File | MB | Dur (h) | Sensors | Preview |")
        lines.append("|------|---:|--------:|---------|---------|")
        for s in sorted(group, key=lambda x: x["file"], reverse=True)[:40]:
            sens = ", ".join(s.get("sensors") or [])[:50]
            prev = (s.get("notes_preview") or "—")[:50]
            lines.append(
                f"| `{s['file']}` | {s.get('size_mb', 0):.0f} | "
                f"{s.get('duration_h', 0):.2f} | {sens} | {prev} |"
            )
        if len(group) > 40:
            lines.append(f"| _… +{len(group)-40} more_ | | | | |")
        lines.append("")

    # No-info archive candidates
    noinfo = [s for s in sessions if s.get("class") == "no_info"]
    lines.append("## No-information (archive candidates)")
    lines.append("")
    lines.append(
        "Tiny or empty sessions (desk restarts, failed boots). "
        "Safe to move to `~/kingfisher/flights-archive/` after review — do not delete forever yet."
    )
    lines.append("")
    if not noinfo:
        lines.append("_None._")
        lines.append("")
    else:
        total_mb = sum(s.get("size_mb", 0) for s in noinfo)
        lines.append(f"**{len(noinfo)} files**, ~{total_mb:.0f} MB total.")
        lines.append("")
        lines.append("<details><summary>File list</summary>")
        lines.append("")
        for s in sorted(noinfo, key=lambda x: x["file"]):
            lines.append(f"- `{s['file']}` ({s.get('size_mb', 0):.2f} MB)")
        lines.append("")
        lines.append("</details>")
        lines.append("")

    # Future data sources
    lines.append("## Future correlated sources")
    lines.append("")
    lines.append(
        "| Source | Status | Purpose |"
    )
    lines.append("|--------|--------|---------|")
    lines.append(
        "| Engine monitor dumps | planned (`~/kingfisher/engine/`) | "
        "RPM/MAP/EGT/CHT/FF sync for performance models |"
    )
    lines.append(
        "| Weather (METAR/TAF, winds & temps aloft, turbulence) | planned | "
        "Atmosphere truth for TAS/wind, icing/turb context |"
    )
    lines.append(
        "| Airband ATC audio | exists under `~/kingfisher/airband/` | "
        "Optional narrative correlation |"
    )
    lines.append(
        "| IMU tempcal soaks | `~/kingfisher/imu_tempcal/` | "
        "Bias / TCO characterization |"
    )
    lines.append("")

    lines.append("<!-- HAND_NOTES_BEGIN -->")
    if notes_preserve.strip():
        lines.append(notes_preserve.rstrip())
    else:
        lines.append("## Hand notes")
        lines.append("")
        lines.append(
            "_(Add lasting notes here; this section is preserved across ledger regenerations.)_"
        )
    lines.append("")
    lines.append("<!-- HAND_NOTES_END -->")
    lines.append("")
    return "\n".join(lines)


def extract_hand_notes(existing_md: str) -> str:
    begin = "<!-- HAND_NOTES_BEGIN -->"
    end = "<!-- HAND_NOTES_END -->"
    if begin not in existing_md or end not in existing_md:
        return ""
    return existing_md.split(begin, 1)[1].split(end, 1)[0].strip("\n")


def write_ledger(
    catalog: dict[str, Any],
    ledger_path: Path,
    *,
    health: dict[str, Any] | None = None,
) -> None:
    notes = ""
    if ledger_path.is_file():
        notes = extract_hand_notes(ledger_path.read_text(encoding="utf-8"))
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(
        render_ledger(catalog, health=health, notes_preserve=notes),
        encoding="utf-8",
    )
