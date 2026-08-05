"""Phase 2: six-position / tumble accel calibration offline companion.

Load a kingfisher calibration JSON (from ~/kingfisher/calibration/) or a
hand-built face-means dict, fit diagonal scale+bias, and optionally plot
before/after ‖a‖.

Examples:
  uv run --project analysis python -m analysis.cal_accel \\
      ~/kingfisher/calibration/cabin_imu_20260803T120000Z.json
  uv run --project analysis python scripts/analyze_flights.py cal-accel \\
      --json ~/kingfisher/calibration/cabin_imu_….json --plot
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

G0 = 9.80665

FACES = ("+X", "-X", "+Y", "-Y", "+Z", "-Z")
AXIS = {"+X": 0, "-X": 0, "+Y": 1, "-Y": 1, "+Z": 2, "-Z": 2}


def load_artifact(path: Path) -> dict[str, Any]:
    with path.open() as f:
        return json.load(f)


def face_accels(artifact: dict[str, Any]) -> dict[str, np.ndarray]:
    """Return face → accel mean (m/s²) from a cal artifact or {faces: …}."""
    samples = artifact.get("face_samples") or artifact.get("faces") or {}
    out: dict[str, np.ndarray] = {}
    for face, sm in samples.items():
        if isinstance(sm, dict) and "accel_ms2" in sm:
            out[face] = np.asarray(sm["accel_ms2"], dtype=float)
        elif isinstance(sm, (list, tuple)) and len(sm) == 3:
            out[face] = np.asarray(sm, dtype=float)
    return out


def fit_diag(faces: dict[str, np.ndarray]) -> dict[str, Any]:
    """Diagonal S, b from opposite-face pairs (same math as internal/calibrate)."""
    for f in FACES:
        if f not in faces:
            raise ValueError(f"missing face {f}")
    scale = np.zeros(3)
    bias = np.zeros(3)
    pairs = (("+X", "-X"), ("+Y", "-Y"), ("+Z", "-Z"))
    for axis, (fp, fn) in enumerate(pairs):
        ap = faces[fp][axis]
        an = faces[fn][axis]
        bias[axis] = 0.5 * (ap + an)
        half = 0.5 * (ap - an)
        if abs(half) < 1:
            raise ValueError(f"face pair {fp}/{fn} too close on axis {axis}")
        scale[axis] = G0 / half

    norms_raw = []
    norms_corr = []
    for f in FACES:
        a = faces[f]
        norms_raw.append(float(np.linalg.norm(a)))
        corr = scale * (a - bias)
        norms_corr.append(float(np.linalg.norm(corr)))
    mean_corr = float(np.mean(norms_corr))
    rms = float(np.sqrt(np.mean((np.asarray(norms_corr) - G0) ** 2)))
    return {
        "accel_scale": scale.tolist(),
        "accel_bias": bias.tolist(),
        "mean_norm_raw": float(np.mean(norms_raw)),
        "mean_norm_ms2": mean_corr,
        "residual_rms_ms2": rms,
        "norms_raw": norms_raw,
        "norms_corr": norms_corr,
        "faces": FACES,
    }


def plot_norms(fit: dict[str, Any], out: Path) -> None:
    import matplotlib.pyplot as plt

    faces = fit["faces"]
    x = np.arange(len(faces))
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.axhline(G0, color="gray", ls="--", label="g₀")
    ax.bar(x - 0.15, fit["norms_raw"], width=0.3, label="raw ‖a‖")
    ax.bar(x + 0.15, fit["norms_corr"], width=0.3, label="corr ‖a‖")
    ax.set_xticks(x)
    ax.set_xticklabels(faces)
    ax.set_ylabel("m/s²")
    ax.set_title(
        f"Six-position accel  mean_corr={fit['mean_norm_ms2']:.4f}  "
        f"RMS={fit['residual_rms_ms2']:.4f}"
    )
    ax.legend()
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=120)
    plt.close(fig)


def run(json_path: Path, plot_path: Path | None = None, refit: bool = True) -> dict[str, Any]:
    art = load_artifact(json_path)
    faces = face_accels(art)
    if not faces and art.get("imu"):
        # Artifact without face means — echo stored fit only.
        imu = art["imu"]
        print(json.dumps(imu, indent=2))
        return imu
    if refit or "imu" not in art:
        fit = fit_diag(faces)
    else:
        imu = art["imu"]
        # Still compute before/after norms from faces if present.
        fit = fit_diag(faces)
        fit["stored"] = imu
    print(
        f"scale={fit['accel_scale']}\n"
        f"bias={fit['accel_bias']}\n"
        f"mean ‖a_raw‖={fit['mean_norm_raw']:.4f}  "
        f"mean ‖a_corr‖={fit['mean_norm_ms2']:.4f}  "
        f"RMS={fit['residual_rms_ms2']:.4f}"
    )
    if plot_path is not None:
        plot_norms(fit, plot_path)
        print(f"wrote {plot_path}")
    return fit


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Offline six-position accel cal")
    p.add_argument("json", type=Path, help="calibration JSON artifact")
    p.add_argument(
        "--plot",
        type=Path,
        nargs="?",
        const=Path("cal_accel_norms.png"),
        default=None,
        help="write before/after ‖a‖ bar chart (default path if flag alone)",
    )
    args = p.parse_args(argv)
    if not args.json.is_file():
        print(f"not found: {args.json}", file=sys.stderr)
        return 1
    run(args.json, plot_path=args.plot)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
