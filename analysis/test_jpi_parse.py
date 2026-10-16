"""Parser tests for the JPI EDM-930 download format.

Expected numbers for the synthetic file are computed from the seed rule
(running total starts at 240, high bytes at 0), not by calling the parser
twice. A real download is checked only when JPI_SAMPLE points at one.
"""

from __future__ import annotations

import os
import unittest
from datetime import datetime
from pathlib import Path

from analysis.jpi.fixture import encode_download, encode_record
from analysis.jpi.parse import ParseError, parse_bytes, parse_file

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "analysis" / "testdata" / "jpi" / "synthetic_edm930.jpi"
CSV_FIXTURE = ROOT / "analysis" / "testdata" / "jpi" / "synthetic_edm930.csv"

START = datetime(2024, 6, 1, 18, 0, 0)


def _records():
    # First sample establishes absolutes from the 240 seed.
    # egt1 = (240+20) + 4*256 = 1284
    # tit1 = (240+10) + 6*256 = 1786
    # rpm  = (240+16) + 4*256 = 1280
    # map  = (240+55)/10 = 29.5
    # ff   = (240-237)/10 = 0.3
    # volts= (240-113)/10 = 12.7
    # cht1 = 240-100 = 140
    # oil  = 240-60 = 180, oil press = 240-160 = 80
    # oat  = 240-200 = 40
    # usd  = (240-141)/10 = 9.9
    # alt delta 0 is not a reading
    first = {
        0: 20,
        48: 4,
        6: 10,
        54: 6,
        8: -100,
        41: 16,
        42: 4,
        40: 55,
        23: -237,
        20: -113,
        15: -60,
        17: -160,
        21: -200,
        22: -141,
        83: 0,
        87: 30,
        86: -12,
    }
    second = {0: 5, 41: 10, 23: 20, 87: 30, 86: -12}
    # repeat=3 inserts an 18 s gap on top of the 6 s step.
    third = {41: 1, 87: 30, 86: -12}
    return [encode_record(first), encode_record(second), encode_record(third, repeat=3)]


def synthetic_bytes() -> bytes:
    other = [encode_record({41: 20, 42: 3, 0: 10, 48: 4})]  # rpm = 260+768 = 1028
    return encode_download(
        [
            (7, START, 6, _records(), 198000, -672000),
            (8, datetime(2024, 6, 1, 19, 0, 0), 6, other, 0, 0),
        ]
    )


class ParseSyntheticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.blob = synthetic_bytes()
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        if not FIXTURE.is_file() or FIXTURE.read_bytes() != cls.blob:
            FIXTURE.write_bytes(cls.blob)

    def test_fixture_file_matches_encoder(self):
        self.assertEqual(FIXTURE.read_bytes(), self.blob)

    def test_header_and_two_flights(self):
        dl = parse_bytes(self.blob, "synthetic_edm930.jpi")
        self.assertEqual(dl.kind, "jpi")
        self.assertEqual(dl.model, 930)
        self.assertEqual(dl.tail, "N1234B")
        self.assertEqual(dl.engine_temp_unit, "F")
        self.assertEqual([f.flight_id for f in dl.flights], [7, 8])
        self.assertEqual(dl.flights[0].interval_s, 6)
        self.assertEqual(len(dl.flights[0].samples), 3)
        self.assertEqual(len(dl.flights[1].samples), 1)

    def test_first_sample_values_and_absent_channels(self):
        flight = parse_bytes(self.blob, "s.jpi").flights[0]
        row = flight.samples[0].values
        self.assertEqual(row["rpm"], 1280.0)
        self.assertEqual(row["egt1_f"], 1284.0)
        self.assertEqual(row["tit1_f"], 1786.0)
        self.assertEqual(row["cht1_f"], 140.0)
        self.assertEqual(row["oil_temp_f"], 180.0)
        self.assertEqual(row["oil_press_psi"], 80.0)
        self.assertEqual(row["oat_f"], 40.0)
        self.assertAlmostEqual(row["map_inhg"], 29.5)
        self.assertAlmostEqual(row["fuel_flow_gph"], 0.3)
        self.assertAlmostEqual(row["fuel_used_gal"], 9.9)
        self.assertAlmostEqual(row["volts"], 12.7)
        self.assertAlmostEqual(row["lat_deg"], 198030 / 6000.0)
        self.assertAlmostEqual(row["lon_deg"], -672012 / 6000.0)
        self.assertNotIn("tit2_f", flight.channels)
        self.assertNotIn("egt2_f", flight.channels)
        self.assertNotIn("alt_ft", flight.channels)
        self.assertIsNone(row.get("tit2_f"))

    def test_repeat_opens_a_time_gap(self):
        flight = parse_bytes(self.blob, "s.jpi").flights[0]
        times = [s.t for s in flight.samples]
        self.assertEqual(times[0], START)
        self.assertEqual(times[1], datetime(2024, 6, 1, 18, 0, 6))
        # t, t+6, then repeat 3 adds 18 s on top of the next 6 s step → t+30.
        self.assertEqual(times[2], datetime(2024, 6, 1, 18, 0, 30))
        self.assertEqual(flight.samples[1].values["rpm"], 1290.0)
        self.assertAlmostEqual(flight.samples[1].values["fuel_flow_gph"], 2.3)
        self.assertEqual(flight.samples[2].values["rpm"], 1291.0)

    def test_second_flight_is_separate(self):
        flight = parse_bytes(self.blob, "s.jpi").flights[1]
        self.assertEqual(flight.samples[0].values["rpm"], 1028.0)
        self.assertNotIn("lat_deg", flight.channels)
        self.assertEqual(flight.start, datetime(2024, 6, 1, 19, 0, 0))

    def test_resync_skips_two_garbage_bytes(self):
        rec0 = encode_record({41: 16, 42: 4})
        rec1 = encode_record({41: 1})
        rec2 = encode_record({41: 1})
        blob = encode_download(
            [(3, START, 6, [rec0, b"\x05\x27" + rec1, rec2], 0, 0)]
        )
        flight = parse_bytes(blob, "slip.jpi").flights[0]
        self.assertEqual(len(flight.samples), 3)
        self.assertTrue(any("resync" in w for w in flight.warnings))
        self.assertEqual(flight.samples[0].values["rpm"], 1280.0)
        self.assertEqual(flight.samples[2].values["rpm"], 1282.0)

    def test_unrecognized_binary_is_an_error(self):
        with self.assertRaises(ParseError) as ctx:
            parse_bytes(b"\x00\x01\x02this is not a download", "notes.bin")
        self.assertIn("unrecognized JPI file", str(ctx.exception))
        self.assertIn("README.md", str(ctx.exception))

    def test_legacy_model_without_protocol_is_refused(self):
        # A $U file that claims an EDM-700 and an unknown protocol.
        body = b"$U, N1*6E\r\n$C,700,0,0,0,0,0,0,1*00\r\n"
        with self.assertRaises(ParseError):
            parse_bytes(body, "old.jpi")

    def test_committed_fixture_parses(self):
        dl = parse_file(FIXTURE)
        self.assertEqual(dl.flights[0].flight_id, 7)
        self.assertEqual(dl.flights[0].samples[0].values["egt1_f"], 1284.0)


class ParseCsvTests(unittest.TestCase):
    CSV = (
        "DATE,TIME,EGT1,EGT2,CHT1,TIT1,RPM,MAP,FF,USD,OIL_T,OIL_P,OAT,VOLTS,ALT,LAT,LONG\n"
        "06/01/2024,18:00:00,1400,1410,300,1580,2300,29.5,14.2,1.0,180,55,60,13.8,4500,33.4,-112.0\n"
        "06/01/2024,18:00:06,1402,,301,1581,2305,29.6,14.1,1.1,181,55,60,13.8,4520,33.41,-112.01\n"
        "06/01/2024,18:10:00,1200,1210,250,1400,1200,20.0,8.0,3.0,170,40,55,13.2,8000,33.5,-112.1\n"
    )

    def test_blank_cell_is_missing_and_gap_splits_flights(self):
        CSV_FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        CSV_FIXTURE.write_text(self.CSV, encoding="utf-8")
        dl = parse_bytes(self.CSV.encode(), "export.csv")
        self.assertEqual(dl.kind, "csv")
        self.assertEqual(len(dl.flights), 2)
        row = dl.flights[0].samples[0].values
        self.assertEqual(row["rpm"], 2300.0)
        self.assertAlmostEqual(row["map_inhg"], 29.5)
        self.assertAlmostEqual(row["fuel_flow_gph"], 14.2)
        self.assertEqual(row["egt1_f"], 1400.0)
        self.assertEqual(row["tit1_f"], 1580.0)
        self.assertEqual(row["oat_f"], 60.0)
        self.assertAlmostEqual(row["volts"], 13.8)
        second = dl.flights[0].samples[1].values
        self.assertIsNone(second["egt2_f"])
        self.assertEqual(dl.flights[1].samples[0].values["alt_ft"], 8000.0)
        self.assertEqual(len(dl.flights[1].samples), 1)

    def test_csv_without_engine_columns_is_refused(self):
        with self.assertRaises(ParseError):
            parse_bytes(b"name,value\nalpha,1\n", "notes.csv")


class GpsQuirkTests(unittest.TestCase):
    """Init mark and one-sample high-byte spikes seen on a real EDM-930."""

    def _one(self, records, interval=6, lat_raw=180000, lon_raw=-600000):
        blob = encode_download(
            [(9, START, interval, records, lat_raw, lon_raw)]
        )
        return parse_bytes(blob, "gps-quirk.jpi").flights[0]

    def test_handshake_100_counts_is_not_applied(self):
        flight = self._one(
            [
                encode_record({87: 100, 86: -100}),
                encode_record({87: 30, 86: -12}),
            ]
        )
        first, second = flight.samples
        self.assertAlmostEqual(first.values["lat_deg"], 180000 / 6000.0)
        self.assertAlmostEqual(first.values["lon_deg"], -600000 / 6000.0)
        self.assertAlmostEqual(second.values["lat_deg"], (180000 + 30) / 6000.0)
        self.assertAlmostEqual(second.values["lon_deg"], (-600000 - 12) / 6000.0)

    def test_high_byte_ff_spike_is_omitted_and_track_continues(self):
        # Low 255 sign-negative with high 255 is -65535 counts (~1,200 km).
        # The next record's high 255 and low 244 lands 11 counts from the
        # pre-spike fix. That intermediate sample is not a position.
        flight = self._one(
            [
                encode_record({87: 20}),
                encode_record({87: -255, 82: 255, 86: 4}),
                encode_record({87: 244, 82: 255}),
            ]
        )
        self.assertAlmostEqual(flight.samples[0].values["lat_deg"], (180000 + 20) / 6000.0)
        self.assertIsNone(flight.samples[1].values.get("lat_deg"))
        self.assertIsNone(flight.samples[1].values.get("lon_deg"))
        # Net of the pair is -11 counts from the first fix. Lon +4 stays.
        self.assertAlmostEqual(flight.samples[2].values["lat_deg"], (180000 + 9) / 6000.0)
        self.assertAlmostEqual(flight.samples[2].values["lon_deg"], (-600000 + 4) / 6000.0)
        self.assertTrue(any("omitted" in w for w in flight.warnings))

    def test_long_gap_16bit_delta_is_kept(self):
        # (2<<8)|167 = 679 counts, about 12.6 km. repeat=40 at 6 s is a
        # 246 s gap, so the implied speed is a cruise groundspeed.
        flight = self._one(
            [
                encode_record({87: 30}),
                encode_record({87: 167, 82: 2}, repeat=40),
            ]
        )
        self.assertAlmostEqual(
            flight.samples[1].values["lat_deg"], (180000 + 30 + 679) / 6000.0
        )


class RealDownloadTests(unittest.TestCase):
    """Optional. Set JPI_SAMPLE to a protocol-2 .JPI the pilot dropped locally."""

    def test_real_file_if_present(self):
        path = os.environ.get("JPI_SAMPLE")
        if not path:
            self.skipTest("JPI_SAMPLE not set")
        dl = parse_file(path)
        self.assertTrue(dl.flights)
        # The public EDM-830 sample U251222.JPI (not committed) is the file
        # this assertion was checked against. Other real dumps only need to parse.
        if dl.model == 830 and any(f.flight_id == 1196 for f in dl.flights):
            flight = next(f for f in dl.flights if f.flight_id == 1196)
            self.assertEqual(len(flight.samples), 216)
            row = flight.samples[0].values
            self.assertEqual(row["rpm"], 1024.0)
            self.assertEqual(row["egt1_f"], 1159.0)
            self.assertEqual(row["cht1_f"], 125.0)
            self.assertAlmostEqual(row["fuel_flow_gph"], 0.3)
            self.assertAlmostEqual(row["volts"], 12.7)
            self.assertNotIn("map_inhg", flight.channels)
            self.assertNotIn("tit1_f", flight.channels)
            self.assertAlmostEqual(row["lat_deg"], 201044 / 6000.0, places=5)
            self.assertAlmostEqual(row["lon_deg"], -673704 / 6000.0, places=5)
        if dl.model == 930 and any(f.flight_id == 306 for f in dl.flights):
            self.assertGreaterEqual(len(dl.flights), 12)
            self.assertTrue(all(f.interval_s == 2 for f in dl.flights))
            self.assertNotIn("alt_ft", set().union(*(f.channels for f in dl.flights)))
            long = max(dl.flights, key=lambda f: len(f.samples))
            for name in (
                "rpm",
                "map_inhg",
                "fuel_flow_gph",
                "tit1_f",
                "egt1_f",
                "cht1_f",
                "oat_c",
                "volts",
                "amps",
                "hp_pct",
                "lat_deg",
                "lon_deg",
            ):
                self.assertIn(name, long.channels)
            self._assert_gps_steps_are_physical(dl)

    def _assert_gps_steps_are_physical(self, dl):
        import math

        limit = 130.0

        def hav(a, b, c, d):
            r = 6_371_000.0
            p1, p2 = math.radians(a), math.radians(c)
            dp, dl = math.radians(c - a), math.radians(d - b)
            h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
            return 2 * r * math.asin(min(1.0, math.sqrt(h)))

        for flight in dl.flights:
            prev = None
            for sample in flight.samples:
                lat = sample.values.get("lat_deg")
                lon = sample.values.get("lon_deg")
                if lat is None or lon is None:
                    continue
                if prev is None:
                    prev = (lat, lon, sample.t)
                    continue
                if lat == prev[0] and lon == prev[1]:
                    continue
                dt = max((sample.t - prev[2]).total_seconds(), 1.0)
                self.assertLessEqual(hav(prev[0], prev[1], lat, lon) / dt, limit)
                prev = (lat, lon, sample.t)


if __name__ == "__main__":
    unittest.main()
