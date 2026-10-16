"""Post-flight JPI EDM-930 ingest.

Not a live hub device. Parses a USB/EZTrends download or a CSV export,
aligns the monitor clock to a Kingfisher flight database, and writes
``jpi_edm930``. See ``docs/jpi-edm930.md``.
"""
