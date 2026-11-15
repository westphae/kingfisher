"""Post-flight engine review for the TAT turbonormalized IO-550-B.

Not a live source. Nothing here publishes to ``live.Hub`` or polls sensors.
Inputs are a flight SQLite that already has ``jpi_edm930``, or a raw EDM-930
download. Limits default to AFMS-550 Rev K (STC SA5223NM) and can be overridden
from ``~/kingfisher/engine/limits.json``.
"""
