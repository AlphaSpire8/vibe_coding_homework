import csv
import gzip
import shutil
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import duckdb

from scripts.download_data import sha256
from scripts.prepare_data import (
    enrich_trips,
    normalize_trips,
    prepare_weather,
    refilter_trips,
)

MODERN = [
    "ride_id",
    "rideable_type",
    "started_at",
    "ended_at",
    "start_station_name",
    "start_station_id",
    "end_station_name",
    "end_station_id",
    "start_lat",
    "start_lng",
    "end_lat",
    "end_lng",
    "member_casual",
]
LEGACY = [
    "trip_id",
    "starttime",
    "stoptime",
    "bikeid",
    "tripduration",
    "from_station_id",
    "from_station_name",
    "to_station_id",
    "to_station_name",
    "usertype",
    "gender",
    "birthyear",
]


def write_csv(path, columns, rows):
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def modern_row(ride_id="001", **changes):
    row = dict(
        zip(
            MODERN,
            [
                ride_id,
                "classic_bike",
                "2024-01-01 12:00:00",
                "2024-01-01 12:01:00",
                "A",
                "01",
                "B",
                "02",
                "41.8",
                "-87.6",
                "41.9",
                "-87.7",
                "member",
            ],
        )
    )
    return row | changes


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.connection = duckdb.connect(config={"threads": 2})
        self.intermediate = self.root / "normalized"
        self.intermediate.mkdir()

    def tearDown(self):
        self.connection.close()
        self.temporary.cleanup()

    def normalize(self, rows, part="part-001.parquet", legacy=False):
        path = self.root / "input.csv"
        write_csv(path, LEGACY if legacy else MODERN, rows)
        return normalize_trips(
            self.connection,
            path,
            {
                "archive": "Divvy_Stations_Trips_2013.zip"
                if legacy
                else "202401-divvy-tripdata.zip",
                "member": part + ".csv",
            },
            self.intermediate / part,
        )

    def test_minute_timestamps_use_reported_seconds(self):
        row = dict(
            zip(
                LEGACY,
                [
                    "0001",
                    "2013-06-27 12:11",
                    "2013-06-27 12:16",
                    "02",
                    "316",
                    "01",
                    " A ",
                    "02",
                    "B",
                    "Subscriber",
                    "Male",
                    "1990",
                ],
            )
        )
        self.normalize([row], legacy=True)
        result = self.connection.execute(
            "SELECT ride_id,start_station_id,duration_seconds,wall_duration_seconds,flag_duration_mismatch,rider_type,start_station_name FROM normalized"
        ).fetchone()
        self.assertEqual(result, ("0001", "01", 316.0, 300.0, False, "member", "A"))
        row["tripduration"] = "2,904.0"
        row["stoptime"] = "2013-06-27 12:59"
        self.normalize([row], legacy=True)
        self.assertEqual(
            self.connection.execute(
                "SELECT duration_seconds FROM normalized"
            ).fetchone()[0],
            2904,
        )

    def test_filter_boundaries_missing_stations_and_conflicting_ids(self):
        duplicate = modern_row("same")
        rows = [
            duplicate,
            modern_row("conflict"),
            modern_row("long", ended_at="2024-01-02 12:00:00"),
            modern_row("short", ended_at="2024-01-01 12:00:59.999"),
            modern_row("too_long", ended_at="2024-01-02 12:00:00.001"),
            modern_row("missing_station", end_station_name=" "),
            modern_row("invalid_time", started_at="invalid"),
            modern_row("", started_at="2024-01-01 12:00:00"),
        ]
        self.normalize(rows)
        self.normalize(
            [duplicate, modern_row("conflict", ended_at="2024-01-01 12:02:00")],
            part="part-002.parquet",
        )
        output = self.root / "output"
        with redirect_stdout(StringIO()):
            audit = enrich_trips(self.connection, self.intermediate, output)
        kept = self.connection.execute(
            "SELECT ride_id,duration_seconds,flag_conflicting_id FROM read_parquet(?) ORDER BY ride_id,duration_seconds",
            [str(output / "trips_clean/*.parquet")],
        ).fetchall()
        self.assertEqual(
            kept,
            [
                ("conflict", 60.0, True),
                ("conflict", 120.0, True),
                ("long", 86400.0, False),
                ("same", 60.0, False),
            ],
        )
        self.assertEqual(sum(row["rows_all"] for row in audit), 10)
        self.assertEqual(sum(row["rows_after_rule_5"] for row in audit), 4)
        copy = self.connection.execute(
            "SELECT source_member,exact_duplicate_rank FROM read_parquet(?) WHERE ride_id='same' ORDER BY source_member",
            [str(output / "trips_all/*.parquet")],
        ).fetchall()
        self.assertEqual(
            copy, [("part-001.parquet.csv", 1), ("part-002.parquet.csv", 2)]
        )

    def test_unknown_rider_and_missing_coordinates_are_flagged(self):
        self.normalize(
            [modern_row(member_casual="Other", start_lat="", end_lat="0", end_lng="0")]
        )
        result = self.connection.execute(
            "SELECT rider_type,flag_rider_unmapped,flag_coordinate_missing,flag_zero_coordinates,flag_station_missing FROM normalized"
        ).fetchone()
        self.assertEqual(result, ("unknown", True, True, True, False))

    def test_legacy_and_modern_ids_have_separate_namespaces(self):
        row = dict(
            zip(
                LEGACY,
                [
                    "001",
                    "2013-06-27 12:11",
                    "2013-06-27 12:12",
                    "2",
                    "60",
                    "01",
                    "A",
                    "02",
                    "B",
                    "Subscriber",
                    "",
                    "",
                ],
            )
        )
        self.normalize([row], legacy=True)
        self.normalize([modern_row("001")], part="part-002.parquet")
        output = self.root / "output"
        with redirect_stdout(StringIO()):
            enrich_trips(self.connection, self.intermediate, output)
        rows = self.connection.execute(
            "SELECT id_namespace,flag_duplicate_id FROM read_parquet(?) ORDER BY id_namespace",
            [str(output / "trips_clean/*.parquet")],
        ).fetchall()
        self.assertEqual(rows, [("legacy", False), ("modern", False)])

    def test_weather_units_quality_and_trace_are_preserved(self):
        folder = self.root / "data/raw/noaa"
        folder.mkdir(parents=True)
        path = folder / "USW00094846.csv.gz"
        with gzip.open(path, "wt") as stream:
            stream.write("USW00094846,20240101,PRCP,25,,,W,2400\n")
            stream.write("USW00094846,20240101,AWND,12,,,W,2400\n")
            stream.write("USW00094846,20240101,TMAX,123,,,W,2400\n")
            stream.write("USW00094846,20240101,TMIN,-45,,,W,2400\n")
            stream.write("USW00094846,20240101,SNOW,0,T,,W,2400\n")
            stream.write("USW00094846,20240101,SNWD,-9999,,,W,2400\n")
            stream.write("USW00094846,20240102,PRCP,10,,S,W,2400\n")
        with (
            patch("scripts.prepare_data.ROOT", self.root),
            patch("scripts.prepare_data.STATIONS", ("USW00094846",)),
        ):
            result = prepare_weather(self.connection, self.root / "output")
        values = self.connection.execute(
            "SELECT prcp_mm,awnd_m_s,tmax_c,tmin_c,snow_mm,snwd_mm,snow_trace FROM weather_daily WHERE weather_date=DATE '2024-01-01'"
        ).fetchone()
        self.assertEqual(values, (2.5, 1.2, 12.3, -4.5, 0.0, None, True))
        self.assertIsNone(
            self.connection.execute(
                "SELECT prcp_mm FROM weather_daily WHERE weather_date=DATE '2024-01-02'"
            ).fetchone()[0]
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT raw_value,converted_value FROM weather_long WHERE is_quality_flagged"
            ).fetchone(),
            (10, 1.0),
        )
        self.assertEqual(result["target_observations"], 7)

    def test_scooter_filter_can_reuse_unchanged_standardized_rows(self):
        self.normalize(
            [
                modern_row("bike"),
                modern_row("scooter", rideable_type="electric_scooter"),
                modern_row("unknown", rideable_type=""),
            ]
        )
        original = self.root / "original"
        with redirect_stdout(StringIO()):
            enrich_trips(self.connection, self.intermediate, original)
        path = original / "trips_all/part-001.parquet"
        fingerprint = sha256(path)
        rebuilt = self.root / "rebuilt"
        shutil.copytree(original / "trips_all", rebuilt / "trips_all")
        with (
            duckdb.connect(config={"threads": 2}) as connection,
            redirect_stdout(StringIO()),
        ):
            audit = refilter_trips(connection, rebuilt)
            ids = connection.execute(
                "SELECT ride_id FROM read_parquet(?) ORDER BY ride_id",
                [str(rebuilt / "trips_clean/*.parquet")],
            ).fetchall()
        self.assertEqual(ids, [("bike",), ("unknown",)])
        self.assertEqual(audit[0]["rows_all"], 3)
        self.assertEqual(audit[0]["rows_after_rule_6"], 2)
        self.assertEqual(sha256(path), fingerprint)
        self.assertEqual(sha256(rebuilt / "trips_all/part-001.parquet"), fingerprint)


if __name__ == "__main__":
    unittest.main()
