"""Session classification taxonomy."""

from __future__ import annotations

from enum import Enum


class SessionClass(str, Enum):
    FLIGHT = "flight"
    TAXI_ONLY = "taxi_only"
    SOAK = "soak"
    EXPERIMENT = "experiment"
    NO_INFO = "no_info"
    UNKNOWN = "unknown"


# Defaults (kt). Override via sidecar `class:` or CLI flags later.
FLIGHT_GS_KT = 40.0
TAXI_GS_KT = 5.0
NO_INFO_MAX_MB = 2.0
NO_INFO_MAX_MIN = 5.0
SOAK_MIN_HOURS = 2.0


def classify(
    *,
    size_mb: float,
    duration_h: float,
    max_gs: float | None,
    sidecar_class: str | None = None,
    tags: list[str] | None = None,
) -> SessionClass:
    """Assign a primary class.

    Precedence: explicit sidecar class > motion heuristics > size/duration.
    """
    if sidecar_class:
        try:
            return SessionClass(sidecar_class.strip().lower())
        except ValueError:
            pass

    tags = tags or []
    if "experiment" in tags or "tempcal" in tags:
        return SessionClass.EXPERIMENT

    gs = max_gs if max_gs is not None else 0.0
    if gs >= FLIGHT_GS_KT:
        return SessionClass.FLIGHT
    if gs >= TAXI_GS_KT:
        return SessionClass.TAXI_ONLY

    if size_mb < NO_INFO_MAX_MB or duration_h * 60 < NO_INFO_MAX_MIN:
        return SessionClass.NO_INFO

    if duration_h >= SOAK_MIN_HOURS and gs < TAXI_GS_KT:
        return SessionClass.SOAK

    if duration_h >= 0.25:
        return SessionClass.SOAK  # short stationary / hangar / desk

    return SessionClass.UNKNOWN
