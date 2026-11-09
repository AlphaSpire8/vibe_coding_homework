import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import duckdb

from scripts.explore_data import build_daily_panel, compute_tables, correlation


class ExplorationTests(unittest.TestCase):
    def test_correlations_handle_ties_missing_and_constants(self):
        result = correlation([1, 2, 2, 4, None], [4, 1, 1, 0, 99])
        self.assertEqual(result["n_days"], 4)
        self.assertAlmostEqual(result["spearman"], -1)
        constant = correlation([1, 1, 1], [1, 2, 3])
        self.assertIsNone(constant["pearson"])
        self.assertIsNone(constant["spearman"])

    def test_daily_panel_distinguishes_zero_from_unobserved_days(self):
        with duckdb.connect(config={"threads": 2}) as connection:
            connection.execute(
                "CREATE TABLE selected(trip_date DATE,rider_type VARCHAR,duration_seconds DOUBLE)"
            )
            connection.execute(
                "INSERT INTO selected VALUES ('2024-01-01','member',60),('2024-01-03','casual',120)"
            )
            connection.execute(
                "CREATE TABLE raw_days AS SELECT DATE '2024-01-01' AS trip_date,1 AS raw_rows UNION ALL SELECT DATE '2024-01-03',1"
            )
            connection.execute(
                "CREATE TABLE weather(weather_date DATE,prcp_mm DOUBLE,snow_mm DOUBLE,snwd_mm DOUBLE,awnd_m_s DOUBLE,tmax_c DOUBLE,tmin_c DOUBLE,prcp_trace BOOLEAN)"
            )
            connection.execute(
                "INSERT INTO weather VALUES ('2024-01-01',0,0,0,1,10,0,TRUE),('2024-01-02',NULL,0,0,1,10,0,FALSE),('2024-01-03',10,0,0,1,10,0,FALSE)"
            )
            build_daily_panel(connection, 2024, 1.0)
            rows = connection.execute(
                "SELECT trip_date,rides,rain_group FROM daily WHERE rider_type='casual' AND trip_date<=DATE '2024-01-03' ORDER BY trip_date"
            ).fetchall()
            self.assertEqual(
                rows,
                [
                    (date(2024, 1, 1), 0, 1),
                    (date(2024, 1, 2), None, None),
                    (date(2024, 1, 3), 1, 4),
                ],
            )
            count, first, last = connection.execute(
                "SELECT COUNT(*),MIN(trip_date),MAX(trip_date) FROM daily"
            ).fetchone()
            self.assertEqual(
                (count, first, last), (732, date(2024, 1, 1), date(2024, 12, 31))
            )

    def test_scope_excludes_holdout_behavior_and_weather(self):
        with (
            TemporaryDirectory() as directory,
            duckdb.connect(config={"threads": 2}) as connection,
        ):
            root = Path(directory)
            for name in ["trips_all", "trips_clean"]:
                (root / "data/processed" / name).mkdir(parents=True)
            connection.execute("""CREATE TABLE trips AS
                SELECT 'ride-'||CAST(n AS VARCHAR) AS ride_id,
                    CASE WHEN n=1 THEN 'member' ELSE 'casual' END AS rider_type,
                    'classic_bike' AS rideable_type,
                    CASE WHEN n<3 THEN DATE '2024-01-01' ELSE DATE '2025-01-01' END AS trip_date,
                    CASE WHEN n<3 THEN TIMESTAMP '2024-01-01 12:00:00' ELSE TIMESTAMP '2025-01-01 12:00:00' END AS started_at,
                    CASE WHEN n<3 THEN TIMESTAMP '2024-01-01 12:01:00' ELSE TIMESTAMP '2025-01-01 12:01:00' END AS ended_at,
                    60.0 AS duration_seconds, CAST(n AS VARCHAR) AS start_station_id,'Station '||CAST(n AS VARCHAR) AS start_station_name,
                    'end' AS end_station_id,'End' AS end_station_name,41.8 AS start_lat,-87.6 AS start_lng,
                    FALSE AS flag_conflicting_id,FALSE AS flag_station_missing,1 AS exact_duplicate_rank
                FROM RANGE(1,4) t(n)""")
            for name in ["trips_all", "trips_clean"]:
                connection.execute(
                    "COPY trips TO ? (FORMAT PARQUET)",
                    [str(root / f"data/processed/{name}/part.parquet")],
                )
            connection.execute("""CREATE TABLE weather AS SELECT 'USW00094846' AS station_id,trip_date AS weather_date,
                0.0 AS prcp_mm,0.0 AS snow_mm,0.0 AS snwd_mm,1.0 AS awnd_m_s,10.0 AS tmax_c,0.0 AS tmin_c,FALSE AS prcp_trace
                FROM trips GROUP BY trip_date""")
            connection.execute(
                "COPY weather TO ? (FORMAT PARQUET)",
                [str(root / "data/processed/weather_daily.parquet")],
            )
            connection.execute("DROP TABLE weather")
            scope = {
                "exploration_year": 2024,
                "holdout_year": 2025,
                "wet_threshold_mm": 1,
                "temperature_bin_width_c": 5,
            }
            prep = {
                "trips": {
                    "yearly_rows": [
                        {"year": 2024, "rows_all": 2, "rows_clean": 2},
                        {"year": 2025, "rows_all": 1, "rows_clean": 1},
                    ]
                }
            }
            with patch("scripts.explore_data.ROOT", root):
                tables = compute_tables(connection, scope, prep)
            self.assertEqual(sum(row["rides"] for row in tables["rider_summary"]), 2)
            self.assertEqual({row["trip_date"].year for row in tables["daily"]}, {2024})
            self.assertEqual(
                {row["start_station_id"] for row in tables["stations"]}, {"1", "2"}
            )
            self.assertEqual(
                connection.execute(
                    "SELECT DISTINCT YEAR(weather_date) FROM weather"
                ).fetchall(),
                [(2024,)],
            )


if __name__ == "__main__":
    unittest.main()
