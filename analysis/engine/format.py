"""Plain-text scorecard for the CLI."""

from __future__ import annotations


def format_review(review: dict) -> str:
    lines = [
        f"{review.get('session')}  {review.get('overall')}",
        f"altitude: {review.get('alt_source') or 'none'}   IAS: {review.get('ias')}",
    ]
    counts = review.get("counts") or {}
    lines.append(
        "scorecard: "
        + "  ".join(f"{k}={counts.get(k, 0)}" for k in ("pass", "caution", "fail", "skip"))
    )
    for note in review.get("notes") or []:
        lines.append(f"note: {note}")
    lines.append("phases:")
    for phase in review.get("phases") or []:
        mins = (phase.get("duration_s") or 0) / 60.0
        lines.append(
            f"  {phase.get('name', ''):12} {mins:6.1f} min  {phase.get('t0_iso', '')}"
        )
    lines.append("checks:")
    for check in review.get("checks") or []:
        lines.append(
            f"  {check.get('status', ''):8} {check.get('title', '')}: {check.get('observed', '')}"
        )
        limit = check.get("limit") or ""
        if limit:
            lines.append(f"           limit: {limit}")
        detail = check.get("detail") or ""
        if detail:
            lines.append(f"           {detail}")
    events = review.get("events") or []
    if events:
        lines.append("events:")
        for event in events[:40]:
            lines.append(
                f"  {event.get('t_iso', '')}  {event.get('severity', ''):8} {event.get('message', '')}"
            )
        if len(events) > 40:
            lines.append(f"  … {len(events) - 40} more")
    return "\n".join(lines) + "\n"


def format_summary(summary: dict) -> str:
    lines = [f"flights: {summary.get('n', 0)}"]
    corr = summary.get("correlation") or {}
    if corr.get("note"):
        lines.append(f"CHT3: {corr['note']}")
        if corr.get("cht3_vs_boost_r") is not None:
            lines.append(f"  r(CHT3, boost events) = {corr['cht3_vs_boost_r']}")
        if corr.get("cht3_vs_lo_ff_drop_r") is not None:
            lines.append(f"  r(CHT3, LO-lowers-FF) = {corr['cht3_vs_lo_ff_drop_r']}")
    flags = summary.get("flags") or []
    if flags:
        lines.append("flags:")
        for flag in flags:
            lines.append(f"  {flag.get('kind')}: {flag.get('detail')}")
    lines.append(
        f"{'session':42} {'result':8} {'CHT3 cr':7} {'peak':6} {'>380s':6} {'FF':6} {'TIT':6} {'LOP':6} {'boost':5} {'mag':8} {'oilT':5}"
    )
    for row in summary.get("flights") or []:
        lines.append(
            f"{str(row.get('session') or ''):42} "
            f"{str(row.get('overall') or ''):8} "
            f"{_n(row.get('cht3_offset_cruise_f'), 1):7} "
            f"{_n(row.get('peak_cht_f'), 0):6} "
            f"{_n(row.get('time_above_380_s'), 0):6} "
            f"{_n(row.get('cruise_ff_gph'), 1):6} "
            f"{_n(row.get('cruise_tit_f'), 0):6} "
            f"{_n(row.get('lop_depth_f'), 0):6} "
            f"{int(row.get('boost_lo') or 0) + int(row.get('boost_hi') or 0):5d} "
            f"{str(row.get('mag_status') or ''):8} "
            f"{_n(row.get('oil_temp_max_f'), 0):5}"
        )
    return "\n".join(lines) + "\n"


def _n(v, digits: int) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.{digits}f}"
    except (TypeError, ValueError):
        return "—"
