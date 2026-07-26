"""Optional YAML-ish front matter + notes next to a flight DB.

Example: 20260621T185102Z_n456t.md

---
class: flight
flightaware: https://...
engine_dump: ../engine/raw/foo.csv
weather: later
---

Free-form notes below the front matter.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_FRONT = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL)


@dataclass
class Sidecar:
    path: Path
    overrides: dict[str, str] = field(default_factory=dict)
    notes: str = ""


def sidecar_path_for(db_path: Path) -> Path:
    return db_path.with_suffix(".md")


def load_sidecar(db_path: Path) -> Sidecar | None:
    path = sidecar_path_for(db_path)
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    m = _FRONT.match(text)
    overrides: dict[str, str] = {}
    notes = text.strip()
    if m:
        for line in m.group(1).splitlines():
            line = line.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            k, v = line.split(":", 1)
            overrides[k.strip()] = v.strip()
        notes = m.group(2).strip()
    return Sidecar(path=path, overrides=overrides, notes=notes)
